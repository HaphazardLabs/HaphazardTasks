"""Tasks, phases and items: validation, reads and writes against the in-memory database."""
import json
import uuid
from datetime import date

from . import recurrence
from .model import utcnow

STATUSES = ("pending", "underway", "done", "blocked", "deferred")
PRIORITIES = ("High", "Medium", "Low")
MAX_PHASES = 4
MAX_ITEMS = 60
MAX_TITLE = 200
MAX_NAME = 80
MAX_ITEM = 300
MAX_NOTES = 20000
MAX_TAGS = 20


class Invalid(ValueError):
    """Bad input from the client. The message is shown to the user."""


class NotFound(LookupError):
    pass


def _id():
    return uuid.uuid4().hex


def _text(value, field, limit, required=True):
    if value is None:
        value = ""
    if not isinstance(value, str):
        raise Invalid(f"{field} must be text")
    value = value.strip()
    if required and not value:
        raise Invalid(f"{field} can't be empty")
    if len(value) > limit:
        raise Invalid(f"{field} is longer than {limit} characters")
    return value


def _date(value, field):
    try:
        return date.fromisoformat(value).isoformat()
    except (TypeError, ValueError):
        raise Invalid(f"{field} must be a date like 2026-11-14") from None


def validate(data):
    """Return a cleaned task structure. Phases and items may carry an id (existing) or not (new)."""
    if not isinstance(data, dict):
        raise Invalid("Send a task object")
    task = {
        "title": _text(data.get("title"), "Title", MAX_TITLE),
        "priority": data.get("priority", "Medium"),
        "start_date": _date(data.get("start_date"), "Start date"),
        "due_date": _date(data.get("due_date"), "Due date"),
        "notes_md": _text(data.get("notes_md", ""), "Notes", MAX_NOTES, required=False),
    }
    if task["priority"] not in PRIORITIES:
        raise Invalid("Priority must be High, Medium or Low")
    if task["due_date"] < task["start_date"]:
        raise Invalid("Due date can't be before the start date")
    tags = data.get("tags", [])
    if not isinstance(tags, list) or len(tags) > MAX_TAGS:
        raise Invalid(f"Tags must be a list of up to {MAX_TAGS}")
    task["tags"] = sorted({_text(t, "Tag", 40) for t in tags})
    try:
        task["recurrence"] = recurrence.validate(data.get("recurrence"))
    except recurrence.Invalid as e:
        raise Invalid(str(e)) from None

    phases = data.get("phases")
    if not isinstance(phases, list) or not 1 <= len(phases) <= MAX_PHASES:
        raise Invalid(f"A task needs 1 to {MAX_PHASES} phases")
    clean, count = [], 0
    for n, ph in enumerate(phases, 1):
        if not isinstance(ph, dict):
            raise Invalid("Each phase must be an object")
        items = ph.get("items")
        if not isinstance(items, list) or not items:
            raise Invalid(f"Phase {n} needs at least one item")
        cleaned_items = []
        for it in items:
            if not isinstance(it, dict):
                raise Invalid("Each item must be an object")
            status = it.get("status", "pending")
            if status not in STATUSES:
                raise Invalid(f"Unknown status: {status}")
            cleaned_items.append({"id": it.get("id"), "text": _text(it.get("text"), "Item", MAX_ITEM),
                                  "status": status})
        count += len(cleaned_items)
        clean.append({"id": ph.get("id"), "name": _text(ph.get("name"), f"Phase {n} name", MAX_NAME),
                      "items": cleaned_items})
    if count > MAX_ITEMS:
        raise Invalid(f"A task can have at most {MAX_ITEMS} items")
    task["phases"] = clean
    return task


# -- reads -------------------------------------------------------------------

TASK_COLS = "id, title, priority, start_date, due_date, notes_md, tags, archived_at, created_at, updated_at"


def _task_row(row):
    keys = TASK_COLS.split(", ")
    t = dict(zip(keys, row))
    t["tags"] = json.loads(t["tags"])
    t["phases"] = []
    return t


def _attach(conn, tasks):
    if not tasks:
        return tasks
    by_id = {t["id"]: t for t in tasks}
    for t in tasks:
        t["recurrence"] = recurrence.load(conn, t["id"])
        t["recurrence_label"] = recurrence.describe(t["recurrence"])
    marks = ",".join("?" * len(by_id))
    phases = {}
    for pid, tid, name, pos in conn.execute(
            f"SELECT id, task_id, name, position FROM phase WHERE task_id IN ({marks}) ORDER BY position",
            list(by_id)):
        ph = {"id": pid, "name": name, "items": []}
        phases[pid] = ph
        by_id[tid]["phases"].append(ph)
    if phases:
        pmarks = ",".join("?" * len(phases))
        for iid, pid, text, status, changed in conn.execute(
                f"SELECT id, phase_id, text, status, status_changed_at FROM item "
                f"WHERE phase_id IN ({pmarks}) ORDER BY position", list(phases)):
            phases[pid]["items"].append({"id": iid, "text": text, "status": status,
                                         "status_changed_at": changed})
    return tasks


def list_tasks(conn, archived=False):
    where = "archived_at IS NOT NULL" if archived else "archived_at IS NULL"
    order = "archived_at DESC" if archived else "due_date, title"
    rows = conn.execute(f"SELECT {TASK_COLS} FROM task WHERE {where} ORDER BY {order}").fetchall()
    return _attach(conn, [_task_row(r) for r in rows])


def get_task(conn, task_id):
    row = conn.execute(f"SELECT {TASK_COLS} FROM task WHERE id = ?", (task_id,)).fetchone()
    if not row:
        raise NotFound("No such task")
    return _attach(conn, [_task_row(row)])[0]


def task_id_for_item(conn, item_id):
    row = conn.execute("SELECT p.task_id FROM item i JOIN phase p ON p.id = i.phase_id WHERE i.id = ?",
                       (item_id,)).fetchone()
    if not row:
        raise NotFound("No such item")
    return row[0]


def history(conn, task_id, limit=50):
    get_task(conn, task_id)
    rows = conn.execute("SELECT e.kind, e.from_value, e.to_value, e.at, i.text FROM event e "
                        "LEFT JOIN item i ON i.id = e.item_id WHERE e.task_id = ? "
                        "ORDER BY e.at DESC, e.rowid DESC LIMIT ?", (task_id, limit)).fetchall()
    return [{"kind": k, "from": f, "to": t, "at": at, "item": text} for k, f, t, at, text in rows]


# -- writes ------------------------------------------------------------------

def _event(conn, task_id, kind, item_id=None, from_value=None, to_value=None, at=None):
    conn.execute("INSERT INTO event(id, task_id, item_id, kind, from_value, to_value, at) "
                 "VALUES (?, ?, ?, ?, ?, ?, ?)",
                 (_id(), task_id, item_id, kind, from_value, to_value, at or utcnow()))


def _write_phases(conn, task_id, phases, now):
    """Upsert phases and items in the given order; delete any of this task's rows not listed."""
    old_phases = {r[0] for r in conn.execute("SELECT id FROM phase WHERE task_id = ?", (task_id,))}
    old_items = {r[0]: r[1] for r in conn.execute(
        "SELECT i.id, i.status FROM item i JOIN phase p ON p.id = i.phase_id WHERE p.task_id = ?", (task_id,))}
    keep_phases, keep_items = set(), set()
    for ppos, ph in enumerate(phases):
        pid = ph["id"] if ph["id"] in old_phases else _id()
        if pid in old_phases:
            conn.execute("UPDATE phase SET name = ?, position = ? WHERE id = ?", (ph["name"], ppos, pid))
        else:
            conn.execute("INSERT INTO phase(id, task_id, name, position) VALUES (?, ?, ?, ?)",
                         (pid, task_id, ph["name"], ppos))
        keep_phases.add(pid)
        for ipos, it in enumerate(ph["items"]):
            iid = it["id"] if it["id"] in old_items else None
            if iid:
                # Editing keeps an item's status unless the client changed it explicitly.
                conn.execute("UPDATE item SET phase_id = ?, text = ?, position = ? WHERE id = ?",
                             (pid, it["text"], ipos, iid))
                if it["status"] != old_items[iid]:
                    conn.execute("UPDATE item SET status = ?, status_changed_at = ? WHERE id = ?",
                                 (it["status"], now, iid))
                    _event(conn, task_id, "status", iid, old_items[iid], it["status"], now)
            else:
                iid = _id()
                conn.execute("INSERT INTO item(id, phase_id, text, status, position, status_changed_at) "
                             "VALUES (?, ?, ?, ?, ?, ?)", (iid, pid, it["text"], it["status"], ipos, now))
            keep_items.add(iid)
    for iid in set(old_items) - keep_items:
        conn.execute("DELETE FROM event WHERE item_id = ?", (iid,))
        conn.execute("DELETE FROM item WHERE id = ?", (iid,))
    for pid in old_phases - keep_phases:
        conn.execute("DELETE FROM phase WHERE id = ?", (pid,))


def create_task(conn, data):
    t = validate(data)
    now = utcnow()
    tid = _id()
    with conn:
        conn.execute("INSERT INTO task(id, title, priority, start_date, due_date, notes_md, tags, "
                     "created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                     (tid, t["title"], t["priority"], t["start_date"], t["due_date"], t["notes_md"],
                      json.dumps(t["tags"]), now, now))
        for ph in t["phases"]:
            ph["id"] = None
            for it in ph["items"]:
                it["id"] = None
        _write_phases(conn, tid, t["phases"], now)
        recurrence.save(conn, tid, t["recurrence"])
        _event(conn, tid, "created", at=now)
    return get_task(conn, tid)


def update_task(conn, task_id, data):
    get_task(conn, task_id)
    t = validate(data)
    now = utcnow()
    with conn:
        conn.execute("UPDATE task SET title = ?, priority = ?, start_date = ?, due_date = ?, notes_md = ?, "
                     "tags = ?, updated_at = ? WHERE id = ?",
                     (t["title"], t["priority"], t["start_date"], t["due_date"], t["notes_md"],
                      json.dumps(t["tags"]), now, task_id))
        _write_phases(conn, task_id, t["phases"], now)
        recurrence.save(conn, task_id, t["recurrence"])
        _event(conn, task_id, "edited", at=now)
    return get_task(conn, task_id)


def delete_task(conn, task_id):
    get_task(conn, task_id)
    with conn:
        conn.execute("DELETE FROM task WHERE id = ?", (task_id,))


def archive_task(conn, task_id):
    task = get_task(conn, task_id)
    if not task["archived_at"]:
        now = utcnow()
        with conn:
            conn.execute("UPDATE task SET archived_at = ?, updated_at = ? WHERE id = ?", (now, now, task_id))
            _event(conn, task_id, "archived", at=now)
    return get_task(conn, task_id)


def restore_task(conn, task_id):
    task = get_task(conn, task_id)
    if task["archived_at"]:
        now = utcnow()
        with conn:
            conn.execute("UPDATE task SET archived_at = NULL, updated_at = ? WHERE id = ?", (now, task_id))
            _event(conn, task_id, "restored", at=now)
    return get_task(conn, task_id)


def set_item_status(conn, item_id, status, today=None):
    if status not in STATUSES:
        raise Invalid(f"Unknown status: {status}")
    task_id = task_id_for_item(conn, item_id)
    (old,) = conn.execute("SELECT status FROM item WHERE id = ?", (item_id,)).fetchone()
    if old != status:
        now = utcnow()
        with conn:
            conn.execute("UPDATE item SET status = ?, status_changed_at = ? WHERE id = ?", (status, now, item_id))
            conn.execute("UPDATE task SET updated_at = ? WHERE id = ?", (now, task_id))
            _event(conn, task_id, "status", item_id, old, status, now)
    closed = recurrence.roll(conn, task_id, today or date.today()) if status == "done" else []
    task = get_task(conn, task_id)
    task["cycle_closed"] = closed[0] if closed else None
    return task
