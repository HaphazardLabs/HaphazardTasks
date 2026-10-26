"""User-created checklist templates: phases and item text only. None ship with the app."""
import json
import uuid

from .model import utcnow
from .tasks import MAX_ITEM, MAX_ITEMS, MAX_NAME, MAX_PHASES, Invalid, NotFound, _text, get_task

MAX_TEMPLATE_NAME = 120


def validate(data):
    if not isinstance(data, dict):
        raise Invalid("Send a template object")
    name = _text(data.get("name"), "Template name", MAX_TEMPLATE_NAME)
    phases = data.get("phases")
    if not isinstance(phases, list) or not 1 <= len(phases) <= MAX_PHASES:
        raise Invalid(f"A template needs 1 to {MAX_PHASES} phases")
    clean, count = [], 0
    for n, ph in enumerate(phases, 1):
        if not isinstance(ph, dict) or not isinstance(ph.get("items"), list) or not ph["items"]:
            raise Invalid(f"Phase {n} needs at least one item")
        items = [_text(it.get("text") if isinstance(it, dict) else it, "Item", MAX_ITEM) for it in ph["items"]]
        count += len(items)
        clean.append({"name": _text(ph.get("name"), f"Phase {n} name", MAX_NAME), "items": items})
    if count > MAX_ITEMS:
        raise Invalid(f"A template can have at most {MAX_ITEMS} items")
    return {"name": name, "phases": clean}


def _row(row):
    tid, name, body, created, updated = row
    return {"id": tid, "name": name, "phases": json.loads(body)["phases"], "created_at": created, "updated_at": updated}


def list_templates(conn):
    return [_row(r) for r in conn.execute(
        "SELECT id, name, body, created_at, updated_at FROM template ORDER BY name COLLATE NOCASE")]


def get_template(conn, template_id):
    row = conn.execute("SELECT id, name, body, created_at, updated_at FROM template WHERE id = ?",
                       (template_id,)).fetchone()
    if not row:
        raise NotFound("No such template")
    return _row(row)


def create_template(conn, data):
    if isinstance(data, dict) and data.get("from_task"):
        task = get_task(conn, data["from_task"])
        data = {"name": data.get("name") or task["title"],
                "phases": [{"name": p["name"], "items": [i["text"] for i in p["items"]]} for p in task["phases"]]}
    t = validate(data)
    now = utcnow()
    tid = uuid.uuid4().hex
    with conn:
        conn.execute("INSERT INTO template(id, name, body, created_at, updated_at) VALUES (?, ?, ?, ?, ?)",
                     (tid, t["name"], json.dumps({"phases": t["phases"]}), now, now))
    return get_template(conn, tid)


def update_template(conn, template_id, data):
    get_template(conn, template_id)
    t = validate(data)
    with conn:
        conn.execute("UPDATE template SET name = ?, body = ?, updated_at = ? WHERE id = ?",
                     (t["name"], json.dumps({"phases": t["phases"]}), utcnow(), template_id))
    return get_template(conn, template_id)


def delete_template(conn, template_id):
    get_template(conn, template_id)
    with conn:
        conn.execute("DELETE FROM template WHERE id = ?", (template_id,))
