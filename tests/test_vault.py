import json
import struct

import pytest

from app import model
from app.vault import (MAGIC, BadCredentials, CorruptVault, Vault, VaultError, format_recovery,
                       new_recovery_key, parse_recovery)

FAST_KDF = {"m": 8192, "t": 1, "p": 1}
PASS = "correct horse battery staple"
MARKER = "SECRET-CLIENT-ZEBRA-7731"


@pytest.fixture
def made(tmp_path):
    raw = new_recovery_key()
    path = tmp_path / "vault.htv"
    v = Vault.create(path, PASS, raw, kdf=FAST_KDF, init_db=model.init_db)
    return path, raw, v


def add_secret_task(conn):
    now = model.utcnow()
    conn.execute("INSERT INTO task(id, title, start_date, due_date, notes_md, created_at, updated_at) "
                 "VALUES ('t1', ?, '2026-10-01', '2026-11-14', ?, ?, ?)", (MARKER, MARKER + " notes", now, now))
    conn.commit()


def test_round_trip(made):
    path, _, v = made
    add_secret_task(v.conn)
    v.mark_dirty()
    v.close()
    v2 = Vault.open(path, passphrase=PASS)
    assert v2.conn.execute("SELECT title FROM task").fetchone()[0] == MARKER
    assert model.get_setting(v2.conn, "schema_version") == "1"


def test_no_plaintext_on_disk(made):
    path, _, v = made
    add_secret_task(v.conn)
    v.save()
    v.save()  # creates a version file too
    for f in path.parent.iterdir():
        data = f.read_bytes()
        assert MARKER.encode() not in data, f.name
        assert MARKER.encode("utf-16-le") not in data, f.name
        assert b"CREATE TABLE" not in data, f.name
        assert b"SQLite format" not in data, f.name


def test_wrong_passphrase(made):
    path, _, v = made
    v.close()
    with pytest.raises(BadCredentials):
        Vault.open(path, passphrase=PASS + "x")


def test_recovery_key_unlocks(made):
    path, raw, v = made
    add_secret_task(v.conn)
    v.mark_dirty()
    v.close()
    typed = format_recovery(raw).lower().replace("-", " ")
    v2 = Vault.open(path, recovery_key=typed)
    assert v2.unlocked_with_recovery


def test_wrong_recovery_key(made):
    path, _, v = made
    v.close()
    with pytest.raises(BadCredentials):
        Vault.open(path, recovery_key=format_recovery(new_recovery_key()))
    with pytest.raises(BadCredentials):
        Vault.open(path, recovery_key="too-short")


def test_body_tamper_detected(made):
    path, _, v = made
    v.close()
    data = bytearray(path.read_bytes())
    data[-5] ^= 0x01
    path.write_bytes(bytes(data))
    with pytest.raises(CorruptVault):
        Vault.open(path, passphrase=PASS)


def test_header_tamper_detected(made):
    path, _, v = made
    v.close()
    data = path.read_bytes()
    (hlen,) = struct.unpack(">I", data[8:12])
    header = json.loads(data[12:12 + hlen])
    header["note"] = "x"  # any change to the authenticated header
    hb = json.dumps(header, separators=(",", ":"), sort_keys=True).encode()
    path.write_bytes(MAGIC + struct.pack(">I", len(hb)) + hb + data[12 + hlen:])
    with pytest.raises(CorruptVault):
        Vault.open(path, passphrase=PASS)


def test_kdf_tamper_fails(made):
    path, _, v = made
    v.close()
    data = path.read_bytes()
    (hlen,) = struct.unpack(">I", data[8:12])
    header = json.loads(data[12:12 + hlen])
    header["kdf"]["t"] = 2
    hb = json.dumps(header, separators=(",", ":"), sort_keys=True).encode()
    path.write_bytes(MAGIC + struct.pack(">I", len(hb)) + hb + data[12 + hlen:])
    with pytest.raises(VaultError):
        Vault.open(path, passphrase=PASS)


def test_not_a_vault(tmp_path):
    p = tmp_path / "vault.htv"
    p.write_bytes(b"hello world, not a vault")
    with pytest.raises(CorruptVault):
        Vault.open(p, passphrase=PASS)


def test_change_passphrase_keeps_recovery(made):
    path, raw, v = made
    v.change_passphrase("a brand new passphrase")
    v.close()
    with pytest.raises(BadCredentials):
        Vault.open(path, passphrase=PASS)
    Vault.open(path, passphrase="a brand new passphrase").close()
    Vault.open(path, recovery_key=format_recovery(raw)).close()


def test_versions_capped_at_five(made):
    path, _, v = made
    for _ in range(9):
        v.save()
    names = sorted(f.name for f in path.parent.iterdir())
    assert names == ["vault.htv"] + [f"vault.htv.{i}" for i in range(1, 6)]
    # Every kept version is itself a valid, openable vault.
    Vault.open(path.with_name("vault.htv.5"), passphrase=PASS).close()


def test_create_refuses_existing(made):
    path, raw, _ = made
    with pytest.raises(VaultError):
        Vault.create(path, PASS, raw, kdf=FAST_KDF)


def test_recovery_format_round_trip():
    raw = new_recovery_key()
    text = format_recovery(raw)
    assert len(text.split("-")) == 8 and all(len(g) == 4 for g in text.split("-"))
    assert parse_recovery(text) == raw


def test_unicode_passphrase_normalized(tmp_path):
    raw = new_recovery_key()
    composed, decomposed = "café passphrase!", "café passphrase!"
    Vault.create(tmp_path / "v.htv", composed, raw, kdf=FAST_KDF, init_db=model.init_db).close()
    Vault.open(tmp_path / "v.htv", passphrase=decomposed).close()


def test_all_vault_files_owner_only(made):
    import os
    import stat
    path, _, v = made
    for _ in range(3):
        v.save()
    for f in path.parent.iterdir():
        assert stat.S_IMODE(os.stat(f).st_mode) == 0o600, f.name
    assert stat.S_IMODE(os.stat(path.parent).st_mode) == 0o700
