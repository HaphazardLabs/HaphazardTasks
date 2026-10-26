"""Flask app: request guard, session, auto-lock and the vault API."""
import base64
import binascii
import hmac
import logging
import os
import secrets
import threading
import time
from logging.handlers import RotatingFileHandler
from pathlib import Path

from flask import Flask, Response, jsonify, request, send_from_directory

from datetime import date

from . import model, recurrence, tasks, templates
from .vault import (BadCredentials, CorruptVault, Vault, _keep_version, atomic_write, format_recovery,
                    new_recovery_key)

APP_NAME = "HaphazardTasks"
DEFAULT_PORT = 8796
COOKIE = "ht_session"
MIN_PASSPHRASE = 12
FREE_ATTEMPTS = 5
MAX_DELAY = 60
STATIC_DIR = Path(__file__).parent / "static"

# Reachable without a session. Everything else under /api/ needs one.
PUBLIC_API = {"/api/status", "/api/session", "/api/setup/begin", "/api/setup/finish", "/api/unlock"}
# No CSRF token exists before unlock; these still require a same-origin Origin header.
CSRF_EXEMPT = {"/api/setup/begin", "/api/setup/finish", "/api/unlock"}

CSP = ("default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; "
       "font-src 'self'; connect-src 'self'; object-src 'none'; base-uri 'none'; "
       "frame-ancestors 'none'; form-action 'self'")


def default_data_dir():
    override = os.environ.get("HAPHAZARDTASKS_DATA")
    if override:
        return Path(override)
    if os.name == "nt":
        return Path(os.environ["APPDATA"]) / APP_NAME
    base = os.environ.get("XDG_DATA_HOME") or Path.home() / ".local" / "share"
    return Path(base) / APP_NAME


class State:
    """Everything the server knows while running. Holds the open vault only while unlocked."""

    def __init__(self, vault_path, kdf=None, idle_override=None, clock=time.monotonic):
        self.vault_path = Path(vault_path)
        self.kdf = kdf
        self.idle_override = idle_override
        self.clock = clock
        self.mutex = threading.RLock()
        self.vault = None
        self.session = None
        self.csrf = None
        self.idle_seconds = 15 * 60
        self.last_activity = 0.0
        self.locked_since = clock()
        self.pending_recovery = None
        self.failed = 0
        self.next_attempt_at = 0.0

    @property
    def unlocked(self):
        return self.vault is not None

    def start_session(self, vault):
        with self.mutex:
            if self.vault is not None and self.vault is not vault:
                self.vault.close()
            self.vault = vault
            self.session = secrets.token_urlsafe(32)
            self.csrf = secrets.token_urlsafe(32)
            minutes = int(model.get_setting(vault.conn, "auto_lock_minutes", "15"))
            self.idle_seconds = self.idle_override or max(1, min(120, minutes)) * 60
            self.last_activity = self.clock()
            self.failed = 0
            self.next_attempt_at = 0.0

    def lock(self, reason):
        with self.mutex:
            if self.vault is not None:
                self.vault.close(save=True)
                logging.getLogger(APP_NAME).info("vault locked (%s)", reason)
            self.vault = None
            self.session = None
            self.csrf = None
            self.locked_since = self.clock()

    def touch(self):
        self.last_activity = self.clock()

    def idle_left(self):
        if not self.unlocked:
            return 0
        return max(0, int(self.idle_seconds - (self.clock() - self.last_activity)))

    def check_idle(self):
        with self.mutex:
            if self.unlocked and self.clock() - self.last_activity >= self.idle_seconds:
                self.lock("idle")

    def flush(self):
        """Save pending changes. Called by the background saver."""
        with self.mutex:
            if self.vault is not None and self.vault.dirty:
                self.vault.save()


def _setup_logging(data_dir):
    log = logging.getLogger(APP_NAME)
    if log.handlers:
        return log
    log.setLevel(logging.INFO)
    data_dir.mkdir(parents=True, exist_ok=True)
    handler = RotatingFileHandler(data_dir / "server.log", maxBytes=256 * 1024, backupCount=1,
                                  encoding="utf-8")
    handler.setFormatter(logging.Formatter("%(asctime)s %(message)s"))
    log.addHandler(handler)
    if os.name == "posix":
        os.chmod(data_dir, 0o700)
        os.chmod(data_dir / "server.log", 0o600)
    return log


def create_app(data_dir=None, port=DEFAULT_PORT, kdf=None, idle_seconds=None, clock=time.monotonic,
               log_to_file=True, today=date.today):
    data_dir = Path(data_dir) if data_dir else default_data_dir()
    state = State(data_dir / "vault.htv", kdf=kdf, idle_override=idle_seconds, clock=clock)
    log = _setup_logging(data_dir) if log_to_file else logging.getLogger(APP_NAME)

    app = Flask(__name__, static_folder=None)
    app.config["MAX_CONTENT_LENGTH"] = 64 * 1024 * 1024  # room for a backup import
    app.extensions["ht_state"] = state

    allowed_hosts = {f"127.0.0.1:{port}", f"localhost:{port}"}
    allowed_origins = {f"http://{h}" for h in allowed_hosts}

    def error(status, message, **extra):
        resp = jsonify({"error": message, **extra})
        resp.status_code = status
        return resp

    def has_session():
        token = request.cookies.get(COOKIE, "")
        return bool(state.session and token and hmac.compare_digest(token, state.session))

    @app.before_request
    def guard():
        # 1. Only answer requests addressed to this machine by name. Blocks DNS rebinding.
        if request.headers.get("Host", "") not in allowed_hosts:
            return error(403, "Host not allowed")
        # 2. Browsers send Sec-Fetch-Site; refuse anything another site triggered.
        if request.headers.get("Sec-Fetch-Site", "same-origin") not in ("same-origin", "none"):
            return error(403, "Cross-site request refused")
        if request.method not in ("GET", "HEAD"):
            # 3. State changes must come from our own page...
            if request.headers.get("Origin", "") not in allowed_origins:
                return error(403, "Origin not allowed")
            if not request.is_json:
                return error(415, "Send JSON")
            # 4. ...and carry the CSRF token issued at unlock.
            if request.path not in CSRF_EXEMPT:
                token = request.headers.get("X-CSRF-Token", "")
                if not (state.csrf and hmac.compare_digest(token, state.csrf)):
                    return error(403, "Missing or stale CSRF token")
        state.check_idle()
        if request.path.startswith("/api/") and request.path not in PUBLIC_API and not has_session():
            return error(401, "Vault is locked", locked=True)
        return None

    @app.after_request
    def harden(resp):
        h = resp.headers
        h["Content-Security-Policy"] = CSP
        h["X-Content-Type-Options"] = "nosniff"
        h["X-Frame-Options"] = "DENY"
        h["Referrer-Policy"] = "no-referrer"
        h["Cross-Origin-Opener-Policy"] = "same-origin"
        h["Cross-Origin-Resource-Policy"] = "same-origin"
        h["Permissions-Policy"] = "camera=(), microphone=(), geolocation=(), payment=(), usb=()"
        h["Cache-Control"] = "no-store"
        h.pop("Server", None)
        # Paths and status codes only. Never bodies or query strings.
        log.info("%s %s %s", request.method, request.path, resp.status_code)
        return resp

    def body():
        return request.get_json(silent=True) or {}

    def set_session_cookie(resp):
        resp.set_cookie(COOKIE, state.session, httponly=True, samesite="Strict", path="/")
        return resp

    # -- pages -----------------------------------------------------------

    @app.get("/")
    def index():
        return send_from_directory(STATIC_DIR, "index.html")

    @app.get("/static/<path:name>")
    def static_file(name):
        return send_from_directory(STATIC_DIR, name)

    # -- session ---------------------------------------------------------

    @app.get("/api/status")
    def status():
        return jsonify(app=APP_NAME, vault_exists=state.vault_path.exists(), unlocked=has_session())

    @app.get("/api/session")
    def session_info():
        if not has_session():
            return jsonify(unlocked=False, vault_exists=state.vault_path.exists())
        return jsonify(unlocked=True, vault_exists=True, csrf=state.csrf,
                       idle_seconds_left=state.idle_left(), idle_seconds_total=state.idle_seconds,
                       must_change_passphrase=state.vault.unlocked_with_recovery)

    @app.post("/api/setup/begin")
    def setup_begin():
        with state.mutex:
            if state.vault_path.exists():
                return error(409, "A vault already exists")
            state.pending_recovery = new_recovery_key()
            return jsonify(recovery_key=format_recovery(state.pending_recovery))

    @app.post("/api/setup/finish")
    def setup_finish():
        data = body()
        passphrase = data.get("passphrase", "")
        with state.mutex:
            if state.vault_path.exists():
                return error(409, "A vault already exists")
            if state.pending_recovery is None:
                return error(400, "Start setup again to get a recovery key")
            if not isinstance(passphrase, str) or len(passphrase) < MIN_PASSPHRASE:
                return error(400, f"Use a passphrase of at least {MIN_PASSPHRASE} characters")
            expected = format_recovery(state.pending_recovery).replace("-", "")[-8:]
            typed = "".join(c for c in str(data.get("recovery_check", "")).upper() if c.isalnum())
            if not hmac.compare_digest(typed, expected):
                return error(400, "The last two groups of the recovery key don't match")
            vault = Vault.create(state.vault_path, passphrase, state.pending_recovery,
                                 kdf=state.kdf, init_db=model.init_db)
            state.pending_recovery = None
            state.start_session(vault)
            log.info("vault created")
            return set_session_cookie(jsonify(unlocked=True, csrf=state.csrf))

    @app.post("/api/unlock")
    def unlock():
        data = body()
        passphrase, recovery = data.get("passphrase"), data.get("recovery_key")
        with state.mutex:
            if not state.vault_path.exists():
                return error(404, "No vault yet. Run setup first.")
            wait = state.next_attempt_at - state.clock()
            if wait > 0:
                return error(429, "Too many attempts. Wait before trying again.",
                             retry_after=int(wait) + 1)
            try:
                if isinstance(recovery, str) and recovery.strip():
                    vault = Vault.open(state.vault_path, recovery_key=recovery)
                elif isinstance(passphrase, str) and passphrase:
                    vault = Vault.open(state.vault_path, passphrase=passphrase)
                else:
                    return error(400, "Enter your passphrase")
            except BadCredentials:
                state.failed += 1
                if state.failed >= FREE_ATTEMPTS:
                    delay = min(MAX_DELAY, 2 ** (state.failed - FREE_ATTEMPTS))
                    state.next_attempt_at = state.clock() + delay
                log.info("unlock failed (%d)", state.failed)
                return error(401, "Wrong passphrase or recovery key")
            except CorruptVault as e:
                log.info("unlock refused: %s", e)
                return error(500, str(e))
            if model.migrate(vault.conn):
                vault.save()
            state.start_session(vault)
            log.info("vault unlocked%s", " with recovery key" if vault.unlocked_with_recovery else "")
            return set_session_cookie(jsonify(
                unlocked=True, csrf=state.csrf,
                must_change_passphrase=vault.unlocked_with_recovery))

    @app.post("/api/lock")
    def lock():
        state.lock("user")
        resp = jsonify(unlocked=False)
        resp.delete_cookie(COOKIE, path="/")
        return resp

    @app.post("/api/activity")
    def activity():
        state.touch()
        return jsonify(idle_seconds_left=state.idle_left())

    @app.post("/api/passphrase")
    def change_passphrase():
        data = body()
        new = data.get("new_passphrase", "")
        with state.mutex:
            vault = state.vault
            if not vault.unlocked_with_recovery:
                if not vault.verify_passphrase(str(data.get("current_passphrase", ""))):
                    return error(401, "Current passphrase is wrong")
            if not isinstance(new, str) or len(new) < MIN_PASSPHRASE:
                return error(400, f"Use a passphrase of at least {MIN_PASSPHRASE} characters")
            vault.change_passphrase(new)
            state.touch()
            log.info("passphrase changed")
            return jsonify(ok=True)

    # -- vault -----------------------------------------------------------

    @app.get("/api/vault")
    def vault_info():
        with state.mutex:
            info = model.vault_summary(state.vault.conn)
            path = state.vault_path
            info["file_bytes"] = path.stat().st_size
            info["versions"] = sum(1 for i in range(1, 6) if path.with_name(f"{path.name}.{i}").exists())
            info["location"] = str(path)
            return jsonify(info)

    # -- backup ----------------------------------------------------------

    @app.post("/api/backup/export")
    def backup_export():
        """The vault file exactly as stored: already encrypted, opens only with the passphrase."""
        with state.mutex:
            state.vault.save()
            data = state.vault_path.read_bytes()
            state.touch()
        stamp = time.strftime("%Y-%m-%d-%H%M")
        log.info("backup exported")
        return Response(data, mimetype="application/octet-stream", headers={
            "Content-Disposition": f'attachment; filename="HaphazardTasks-backup-{stamp}.htv"'})

    @app.post("/api/backup/import")
    def backup_import():
        """Replace the vault with a backup. Checks the passphrase first; keeps the old vault as a version."""
        data = body()
        passphrase = data.get("passphrase")
        try:
            blob = base64.b64decode(data.get("file_b64") or "", validate=True)
        except (binascii.Error, ValueError):
            return error(400, "That file could not be read")
        if not blob or not isinstance(passphrase, str) or not passphrase:
            return error(400, "Choose a backup file and enter its passphrase")
        with state.mutex:
            tmp = state.vault_path.with_name("import.tmp")
            try:
                atomic_write(tmp, blob)
                try:
                    Vault.open(tmp, passphrase=passphrase).close(save=False)
                except BadCredentials:
                    return error(401, "That passphrase does not open this backup")
                except CorruptVault as e:
                    return error(400, f"Not a usable backup: {e}")
                state.lock("import")
                _keep_version(state.vault_path)
                os.replace(tmp, state.vault_path)
            finally:
                for leftover in (tmp, tmp.with_name(tmp.name + ".1")):
                    if leftover.exists():
                        leftover.unlink()
            vault = Vault.open(state.vault_path, passphrase=passphrase)
            if model.migrate(vault.conn):
                vault.save()
            state.start_session(vault)
            log.info("backup imported")
            return set_session_cookie(jsonify(unlocked=True, csrf=state.csrf))

    # -- tasks -----------------------------------------------------------

    def mutate(fn, *args):
        """Run a write against the open vault; mark it for saving within a second."""
        with state.mutex:
            try:
                result = fn(state.vault.conn, *args)
            except tasks.Invalid as e:
                return error(400, str(e))
            except tasks.NotFound as e:
                return error(404, str(e))
            state.vault.mark_dirty()
            state.touch()
            return result

    def read(fn, *args):
        with state.mutex:
            try:
                return fn(state.vault.conn, *args)
            except tasks.NotFound as e:
                return error(404, str(e))

    def as_json(result, status=200):
        if not isinstance(result, (dict, list)):
            return result  # already an error response
        resp = jsonify(result)
        resp.status_code = status
        return resp

    def roll_due_cycles():
        """Close any recurring cycles that ended since the last look. Cheap when nothing is due."""
        with state.mutex:
            if recurrence.roll_all(state.vault.conn, today()):
                state.vault.mark_dirty()

    @app.get("/api/tasks")
    def list_tasks():
        roll_due_cycles()
        return as_json(read(tasks.list_tasks, request.args.get("archived") == "1"))

    @app.get("/api/tasks/<task_id>/cycles")
    def task_cycles(task_id):
        return as_json(read(lambda conn, tid: (tasks.get_task(conn, tid), recurrence.cycles(conn, tid))[1], task_id))

    # -- templates ---------------------------------------------------------

    @app.get("/api/templates")
    def list_templates():
        return as_json(read(templates.list_templates))

    @app.post("/api/templates")
    def create_template():
        return as_json(mutate(templates.create_template, body()), 201)

    @app.put("/api/templates/<template_id>")
    def update_template(template_id):
        return as_json(mutate(templates.update_template, template_id, body()))

    @app.delete("/api/templates/<template_id>")
    def delete_template(template_id):
        result = mutate(templates.delete_template, template_id)
        return result if result is not None else jsonify(deleted=template_id)

    @app.post("/api/tasks/<task_id>/archive")
    def archive_task(task_id):
        return as_json(mutate(tasks.archive_task, task_id))

    @app.post("/api/tasks/<task_id>/restore")
    def restore_task(task_id):
        return as_json(mutate(tasks.restore_task, task_id))

    @app.get("/api/settings")
    def get_settings():
        with state.mutex:
            return jsonify(model.get_settings(state.vault.conn))

    @app.put("/api/settings")
    def put_settings():
        with state.mutex:
            try:
                out = model.update_settings(state.vault.conn, body())
            except ValueError as e:
                return error(400, str(e))
            state.vault.mark_dirty()
            if not state.idle_override:
                state.idle_seconds = out["auto_lock_minutes"] * 60
            state.touch()
            return jsonify(out)

    @app.post("/api/tasks")
    def create_task():
        return as_json(mutate(tasks.create_task, body()), 201)

    @app.get("/api/tasks/<task_id>")
    def get_task(task_id):
        roll_due_cycles()
        return as_json(read(tasks.get_task, task_id))

    @app.put("/api/tasks/<task_id>")
    def update_task(task_id):
        return as_json(mutate(tasks.update_task, task_id, body()))

    @app.delete("/api/tasks/<task_id>")
    def delete_task(task_id):
        result = mutate(tasks.delete_task, task_id)
        return result if result is not None else jsonify(deleted=task_id)

    @app.get("/api/tasks/<task_id>/history")
    def task_history(task_id):
        return as_json(read(tasks.history, task_id))

    @app.patch("/api/items/<item_id>")
    def set_item(item_id):
        return as_json(mutate(tasks.set_item_status, item_id, body().get("status"), today()))

    return app


def start_background(app, quit_after_locked=30 * 60, interval=1.0, on_quit=None):
    """Saver + idle watchdog. Runs server-side so the vault locks even with the tab closed."""
    state = app.extensions["ht_state"]

    def loop():
        while True:
            time.sleep(interval)
            try:
                state.check_idle()
                state.flush()
                if (quit_after_locked and not state.unlocked
                        and state.clock() - state.locked_since > quit_after_locked):
                    logging.getLogger(APP_NAME).info("stopping: locked for %ds", quit_after_locked)
                    (on_quit or (lambda: os._exit(0)))()
                    return
            except Exception:  # keep the watchdog alive no matter what
                logging.getLogger(APP_NAME).exception("background loop error")

    t = threading.Thread(target=loop, name="ht-watchdog", daemon=True)
    t.start()
    return t
