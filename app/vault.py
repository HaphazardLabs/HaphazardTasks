"""Encrypted vault: one file holding the whole SQLite database, sealed with AES-256-GCM.

File layout
    MAGIC (8 bytes) | header length (4 bytes, big-endian) | header JSON | nonce (12) | ciphertext+tag

The header carries the Argon2id parameters and the random data key wrapped twice:
once by a key derived from the passphrase, once by a key derived from the recovery key.
Everything before the nonce is authenticated as associated data, so any edit to the
header or the body makes the vault refuse to open.

The plaintext database only ever exists in memory.
"""
import base64
import json
import os
import secrets
import shutil
import sqlite3
import struct
import threading
import unicodedata
from pathlib import Path

from argon2.low_level import Type, hash_secret_raw
from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

MAGIC = b"HTVAULT1"
FORMAT_VERSION = 1
KEEP_VERSIONS = 5
MAX_HEADER = 64 * 1024
# 128 MiB, 3 passes, 4 lanes: roughly 0.5-1 s on a laptop.
DEFAULT_KDF = {"m": 131072, "t": 3, "p": 4}
RECOVERY_BYTES = 20  # 160 bits -> 32 base32 characters -> 8 groups of 4

WRAP_PASSPHRASE = b"HTV1|wrap|passphrase"
WRAP_RECOVERY = b"HTV1|wrap|recovery"


class VaultError(Exception):
    pass


class BadCredentials(VaultError):
    pass


class CorruptVault(VaultError):
    pass


def _b64(b):
    return base64.b64encode(b).decode("ascii")


def _unb64(s):
    return base64.b64decode(s, validate=True)


def _normalize_passphrase(passphrase):
    # NFC so the same passphrase typed on Linux and Windows derives the same key.
    return unicodedata.normalize("NFC", passphrase).encode("utf-8")


def derive_passphrase_key(passphrase, salt, kdf):
    return hash_secret_raw(
        _normalize_passphrase(passphrase), salt,
        time_cost=kdf["t"], memory_cost=kdf["m"], parallelism=kdf["p"],
        hash_len=32, type=Type.ID,
    )


def calibrate_kdf(target_seconds=0.6, max_t=16):
    """Pick Argon2id passes so unlocking takes about target_seconds on this machine."""
    import time
    kdf = dict(DEFAULT_KDF)
    start = time.perf_counter()
    derive_passphrase_key("calibration", secrets.token_bytes(16), kdf)
    elapsed = max(time.perf_counter() - start, 1e-3)
    kdf["t"] = max(DEFAULT_KDF["t"], min(max_t, round(DEFAULT_KDF["t"] * target_seconds / elapsed)))
    return kdf


def derive_recovery_key(raw, salt):
    return HKDF(algorithm=hashes.SHA256(), length=32, salt=salt,
                info=b"haphazardtasks recovery v1").derive(raw)


def new_recovery_key():
    return secrets.token_bytes(RECOVERY_BYTES)


def format_recovery(raw):
    s = base64.b32encode(raw).decode("ascii").rstrip("=")
    return "-".join(s[i:i + 4] for i in range(0, len(s), 4))


def parse_recovery(text):
    s = "".join(c for c in str(text).upper() if c.isalnum())
    if len(s) != 32:
        raise BadCredentials("recovery key must be 32 characters")
    try:
        return base64.b32decode(s)
    except ValueError:
        raise BadCredentials("recovery key is not valid") from None


def _wrap(kek, dek, label):
    nonce = secrets.token_bytes(12)
    return {"nonce": _b64(nonce), "ct": _b64(AESGCM(kek).encrypt(nonce, bytes(dek), label))}


def _unwrap(kek, wrapped, label):
    try:
        return AESGCM(kek).decrypt(_unb64(wrapped["nonce"]), _unb64(wrapped["ct"]), label)
    except InvalidTag:
        raise BadCredentials("wrong passphrase or recovery key") from None


def read_vault_file(path):
    """Return (authenticated prefix, header dict, sealed body)."""
    data = Path(path).read_bytes()
    if len(data) < 12 or data[:8] != MAGIC:
        raise CorruptVault("not a HaphazardTasks vault")
    (hlen,) = struct.unpack(">I", data[8:12])
    if hlen > MAX_HEADER or 12 + hlen > len(data):
        raise CorruptVault("vault header is damaged")
    try:
        header = json.loads(data[12:12 + hlen])
    except ValueError:
        raise CorruptVault("vault header is damaged") from None
    if not isinstance(header, dict) or header.get("v") != FORMAT_VERSION:
        raise CorruptVault("unsupported vault version")
    return data[:12 + hlen], header, data[12 + hlen:]


def _fsync_dir(directory):
    if os.name != "posix":
        return
    fd = os.open(directory, os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def _keep_version(path):
    """Shift vault.htv.1..4 up one and copy the current vault to .1. The live file stays in place."""
    if not path.exists():
        return
    for i in range(KEEP_VERSIONS - 1, 0, -1):
        src = path.with_name(f"{path.name}.{i}")
        if src.exists():
            os.replace(src, path.with_name(f"{path.name}.{i + 1}"))
    dest = path.with_name(f"{path.name}.1")
    shutil.copyfile(path, dest)
    if os.name == "posix":
        os.chmod(dest, 0o600)


def atomic_write(path, data):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    if os.name == "posix":
        os.chmod(path.parent, 0o700)
    tmp = path.with_name(path.name + ".tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | getattr(os, "O_BINARY", 0), 0o600)
    with os.fdopen(fd, "wb") as f:
        f.write(data)
        f.flush()
        os.fsync(f.fileno())
    _keep_version(path)
    os.replace(tmp, path)
    _fsync_dir(path.parent)


def _memory_db():
    conn = sqlite3.connect(":memory:", check_same_thread=False)
    return conn


def _harden(conn):
    # Keep every temporary structure in memory so nothing spills to disk.
    conn.execute("PRAGMA temp_store = MEMORY")
    conn.execute("PRAGMA foreign_keys = ON")


class Vault:
    """An unlocked vault. Holds the data key and the in-memory database until close()."""

    def __init__(self, path, header, dek, conn):
        self.path = Path(path)
        self._header = header
        self._dek = bytearray(dek)
        self.conn = conn
        self.lock = threading.RLock()
        self._dirty = False
        self.unlocked_with_recovery = False

    # -- lifecycle -------------------------------------------------------

    @classmethod
    def create(cls, path, passphrase, recovery_raw, kdf=None, init_db=None):
        path = Path(path)
        if path.exists():
            raise VaultError("a vault already exists at this location")
        kdf = dict(kdf or calibrate_kdf())
        dek = AESGCM.generate_key(bit_length=256)
        rsalt = secrets.token_bytes(16)
        header = {
            "v": FORMAT_VERSION,
            "kdf": {"alg": "argon2id", "m": kdf["m"], "t": kdf["t"], "p": kdf["p"], "salt": ""},
            "wrap_passphrase": None,
            "recovery": {"salt": _b64(rsalt),
                         **_wrap(derive_recovery_key(recovery_raw, rsalt), dek, WRAP_RECOVERY)},
        }
        conn = _memory_db()
        _harden(conn)
        if init_db:
            init_db(conn)
        vault = cls(path, header, dek, conn)
        vault._set_passphrase(passphrase)
        vault.save()
        return vault

    @classmethod
    def open(cls, path, passphrase=None, recovery_key=None):
        if (passphrase is None) == (recovery_key is None):
            raise ValueError("give exactly one of passphrase or recovery_key")
        prefix, header, body = read_vault_file(path)
        try:
            if passphrase is not None:
                kdf = header["kdf"]
                kek = derive_passphrase_key(passphrase, _unb64(kdf["salt"]), kdf)
                dek = _unwrap(kek, header["wrap_passphrase"], WRAP_PASSPHRASE)
            else:
                rec = header["recovery"]
                kek = derive_recovery_key(parse_recovery(recovery_key), _unb64(rec["salt"]))
                dek = _unwrap(kek, rec, WRAP_RECOVERY)
        except (KeyError, TypeError, ValueError):
            raise CorruptVault("vault header is damaged") from None
        if len(body) < 12 + 16:
            raise CorruptVault("vault contents are truncated")
        try:
            plain = AESGCM(dek).decrypt(body[:12], body[12:], prefix)
        except InvalidTag:
            raise CorruptVault("vault contents failed authentication: the file is damaged or was altered") from None
        conn = _memory_db()
        conn.deserialize(plain)
        _harden(conn)
        vault = cls(path, header, dek, conn)
        vault.unlocked_with_recovery = recovery_key is not None
        return vault

    def close(self, save=True):
        with self.lock:
            if self.conn is None:
                return
            if save and self._dirty:
                self.save()
            self.conn.close()
            self.conn = None
            # Best effort: Python cannot guarantee other copies of the key are wiped.
            for i in range(len(self._dek)):
                self._dek[i] = 0
            self._dek = bytearray()

    @property
    def is_open(self):
        return self.conn is not None

    # -- persistence -----------------------------------------------------

    def mark_dirty(self):
        self._dirty = True

    @property
    def dirty(self):
        return self._dirty

    def save(self):
        with self.lock:
            plain = self.conn.serialize()
            hbytes = json.dumps(self._header, separators=(",", ":"), sort_keys=True).encode("utf-8")
            prefix = MAGIC + struct.pack(">I", len(hbytes)) + hbytes
            nonce = secrets.token_bytes(12)
            atomic_write(self.path, prefix + nonce + AESGCM(bytes(self._dek)).encrypt(nonce, plain, prefix))
            self._dirty = False

    # -- credentials -----------------------------------------------------

    def _set_passphrase(self, passphrase):
        kdf = self._header["kdf"]
        salt = secrets.token_bytes(16)
        kek = derive_passphrase_key(passphrase, salt, kdf)
        kdf["salt"] = _b64(salt)
        self._header["wrap_passphrase"] = _wrap(kek, self._dek, WRAP_PASSPHRASE)

    def verify_passphrase(self, passphrase):
        kdf = self._header["kdf"]
        try:
            kek = derive_passphrase_key(passphrase, _unb64(kdf["salt"]), kdf)
            _unwrap(kek, self._header["wrap_passphrase"], WRAP_PASSPHRASE)
            return True
        except BadCredentials:
            return False

    def change_passphrase(self, new_passphrase):
        """Re-wrap the data key under a new passphrase. The data key itself does not change."""
        with self.lock:
            self._set_passphrase(new_passphrase)
            self.save()
            self.unlocked_with_recovery = False
