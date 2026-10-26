"""Database schema and migrations. The database lives only inside the encrypted vault."""
from datetime import datetime, timezone

SCHEMA_VERSION = 1

SCHEMA = """
CREATE TABLE task (
    id           TEXT PRIMARY KEY,
    title        TEXT NOT NULL,
    priority     TEXT NOT NULL DEFAULT 'Medium' CHECK (priority IN ('High', 'Medium', 'Low')),
    start_date   TEXT NOT NULL,
    due_date     TEXT NOT NULL,
    notes_md     TEXT NOT NULL DEFAULT '',
    tags         TEXT NOT NULL DEFAULT '[]',
    recurrence_id TEXT,
    archived_at  TEXT,
    created_at   TEXT NOT NULL,
    updated_at   TEXT NOT NULL
);
CREATE TABLE phase (
    id        TEXT PRIMARY KEY,
    task_id   TEXT NOT NULL REFERENCES task(id) ON DELETE CASCADE,
    name      TEXT NOT NULL,
    position  INTEGER NOT NULL
);
CREATE TABLE item (
    id                TEXT PRIMARY KEY,
    phase_id          TEXT NOT NULL REFERENCES phase(id) ON DELETE CASCADE,
    text              TEXT NOT NULL,
    status            TEXT NOT NULL DEFAULT 'pending'
                      CHECK (status IN ('done', 'underway', 'pending', 'blocked', 'deferred')),
    position          INTEGER NOT NULL,
    status_changed_at TEXT NOT NULL
);
CREATE TABLE event (
    id         TEXT PRIMARY KEY,
    task_id    TEXT NOT NULL REFERENCES task(id) ON DELETE CASCADE,
    item_id    TEXT,
    kind       TEXT NOT NULL,
    from_value TEXT,
    to_value   TEXT,
    at         TEXT NOT NULL
);
CREATE TABLE recurrence (
    id                TEXT PRIMARY KEY,
    task_id           TEXT NOT NULL REFERENCES task(id) ON DELETE CASCADE,
    pattern           TEXT NOT NULL CHECK (pattern IN ('daily', 'weekly', 'monthly', 'interval')),
    weekdays          TEXT,
    month_day         INTEGER,
    every_n_days      INTEGER,
    cycle_length_days INTEGER
);
CREATE TABLE cycle (
    id         TEXT PRIMARY KEY,
    task_id    TEXT NOT NULL REFERENCES task(id) ON DELETE CASCADE,
    start_date TEXT NOT NULL,
    due_date   TEXT NOT NULL,
    closed_at  TEXT NOT NULL,
    outcome    TEXT NOT NULL CHECK (outcome IN ('complete', 'incomplete')),
    snapshot   TEXT NOT NULL
);
CREATE TABLE template (
    id         TEXT PRIMARY KEY,
    name       TEXT NOT NULL,
    body       TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE TABLE setting (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
CREATE INDEX phase_task ON phase(task_id, position);
CREATE INDEX item_phase ON item(phase_id, position);
CREATE INDEX event_task ON event(task_id, at);
CREATE INDEX cycle_task ON cycle(task_id, start_date);
"""

DEFAULT_SETTINGS = {
    "auto_lock_minutes": "15",
    "theme": "system",
}


def utcnow():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def init_db(conn):
    with conn:
        conn.executescript(SCHEMA)
        conn.executemany("INSERT INTO setting(key, value) VALUES (?, ?)", [
            ("schema_version", str(SCHEMA_VERSION)),
            ("created_at", utcnow()),
            *DEFAULT_SETTINGS.items(),
        ])


# Each entry upgrades from version N to N+1. Empty until the schema first changes.
MIGRATIONS = {}


def migrate(conn):
    """Bring an unlocked database up to SCHEMA_VERSION. Returns True if anything changed."""
    version = int(get_setting(conn, "schema_version", "1"))
    if version > SCHEMA_VERSION:
        raise RuntimeError("this vault was written by a newer version of HaphazardTasks")
    changed = False
    while version < SCHEMA_VERSION:
        with conn:
            MIGRATIONS[version](conn)
            version += 1
            set_setting(conn, "schema_version", str(version))
        changed = True
    return changed


def get_setting(conn, key, default=None):
    row = conn.execute("SELECT value FROM setting WHERE key = ?", (key,)).fetchone()
    return row[0] if row else default


def set_setting(conn, key, value):
    conn.execute("INSERT INTO setting(key, value) VALUES (?, ?) "
                 "ON CONFLICT(key) DO UPDATE SET value = excluded.value", (key, str(value)))


THEMES = ("system", "light", "dark")


def get_settings(conn):
    return {
        "auto_lock_minutes": int(get_setting(conn, "auto_lock_minutes", "15")),
        "theme": get_setting(conn, "theme", "system"),
    }


def update_settings(conn, data):
    """Validate and store user settings. Raises ValueError with a message for the user."""
    out = {}
    if "auto_lock_minutes" in data:
        try:
            minutes = int(data["auto_lock_minutes"])
        except (TypeError, ValueError):
            raise ValueError("Auto-lock must be a whole number of minutes") from None
        if not 1 <= minutes <= 120:
            raise ValueError("Auto-lock must be between 1 and 120 minutes")
        out["auto_lock_minutes"] = minutes
    if "theme" in data:
        if data["theme"] not in THEMES:
            raise ValueError("Theme must be system, light or dark")
        out["theme"] = data["theme"]
    with conn:
        for k, v in out.items():
            set_setting(conn, k, v)
    return get_settings(conn)


def vault_summary(conn):
    return {
        "tasks": conn.execute("SELECT COUNT(*) FROM task WHERE archived_at IS NULL").fetchone()[0],
        "archived": conn.execute("SELECT COUNT(*) FROM task WHERE archived_at IS NOT NULL").fetchone()[0],
        "templates": conn.execute("SELECT COUNT(*) FROM template").fetchone()[0],
        "created_at": get_setting(conn, "created_at"),
        "schema_version": int(get_setting(conn, "schema_version", "1")),
    }
