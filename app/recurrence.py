"""Recurring tasks: next due dates, and rolling a task from one cycle to the next.

A cycle runs from the task's start_date to its due_date. A cycle closes when every item is
done ("complete") or when today is past its due date ("incomplete"). Closing saves a snapshot
to the cycle table, moves the task to the next window (start = old due + 1, due = next
occurrence of the pattern) and resets every item to pending. Windows missed entirely while the
app was closed are each recorded as incomplete, so gaps stay visible.
"""
import calendar
import json
import uuid
from datetime import date, timedelta

from .model import utcnow

PATTERNS = ("daily", "weekly", "monthly", "interval")
WEEKDAYS = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")
MAX_CATCHUP = 400  # never loop forever on a long-idle daily task


class Invalid(ValueError):
    pass


def validate(rec):
    """Return a cleaned recurrence dict, or None for a one-off task."""
    if rec in (None, {}, ""):
        return None
    if not isinstance(rec, dict) or rec.get("pattern") not in PATTERNS:
        raise Invalid("Repeat must be daily, weekly, monthly or every N days")
    out = {"pattern": rec["pattern"], "weekdays": None, "month_day": None, "every_n_days": None}
    if out["pattern"] == "weekly":
        days = rec.get("weekdays")
        if not isinstance(days, list) or not days or not all(isinstance(d, int) and 0 <= d <= 6 for d in days):
            raise Invalid("Pick at least one weekday")
        out["weekdays"] = sorted(set(days))
    elif out["pattern"] == "monthly":
        md = rec.get("month_day")
        if not isinstance(md, int) or not (md == -1 or 1 <= md <= 31):
            raise Invalid("Monthly repeats need a day from 1 to 31, or the last day")
        out["month_day"] = md
    elif out["pattern"] == "interval":
        n = rec.get("every_n_days")
        if not isinstance(n, int) or not 1 <= n <= 365:
            raise Invalid("Repeat every 1 to 365 days")
        out["every_n_days"] = n
    return out


def _month_candidate(year, month, month_day):
    last = calendar.monthrange(year, month)[1]
    return date(year, month, last if month_day == -1 else min(month_day, last))


def next_due(rec, after):
    """The first due date strictly after `after` (the previous cycle's due date)."""
    p = rec["pattern"]
    if p == "daily":
        return after + timedelta(days=1)
    if p == "interval":
        return after + timedelta(days=rec["every_n_days"])
    if p == "weekly":
        for i in range(1, 8):
            d = after + timedelta(days=i)
            if d.weekday() in rec["weekdays"]:
                return d
    if p == "monthly":
        y, m = after.year, after.month
        for _ in range(24):
            d = _month_candidate(y, m, rec["month_day"])
            if d > after:
                return d
            y, m = (y + 1, 1) if m == 12 else (y, m + 1)
    raise ValueError(f"cannot compute next due date for {rec}")


def describe(rec):
    if not rec:
        return None
    p = rec["pattern"]
    if p == "daily":
        return "Repeats daily"
    if p == "weekly":
        return "Repeats weekly · " + ", ".join(WEEKDAYS[d] for d in rec["weekdays"])
    if p == "monthly":
        return "Repeats monthly · " + ("last day" if rec["month_day"] == -1 else f"day {rec['month_day']}")
    return f"Repeats every {rec['every_n_days']} days"


# -- storage ---------------------------------------------------------------

def load(conn, task_id):
    row = conn.execute("SELECT pattern, weekdays, month_day, every_n_days FROM recurrence WHERE task_id = ?",
                       (task_id,)).fetchone()
    if not row:
        return None
    pattern, weekdays, month_day, every = row
    return {"pattern": pattern, "weekdays": json.loads(weekdays) if weekdays else None,
            "month_day": month_day, "every_n_days": every}


def save(conn, task_id, rec):
    """Replace the task's recurrence (None removes it). Caller manages the transaction."""
    conn.execute("DELETE FROM recurrence WHERE task_id = ?", (task_id,))
    rid = None
    if rec:
        rid = uuid.uuid4().hex
        conn.execute("INSERT INTO recurrence(id, task_id, pattern, weekdays, month_day, every_n_days) "
                     "VALUES (?, ?, ?, ?, ?, ?)",
                     (rid, task_id, rec["pattern"], json.dumps(rec["weekdays"]) if rec["weekdays"] else None,
                      rec["month_day"], rec["every_n_days"]))
    conn.execute("UPDATE task SET recurrence_id = ? WHERE id = ?", (rid, task_id))


def cycles(conn, task_id, limit=100):
    rows = conn.execute("SELECT start_date, due_date, closed_at, outcome, snapshot FROM cycle WHERE task_id = ? "
                        "ORDER BY start_date DESC LIMIT ?", (task_id, limit)).fetchall()
    out = []
    for start, due, closed, outcome, snap in rows:
        phases = json.loads(snap)
        items = [i for p in phases for i in p["items"]]
        counted = [i for i in items if i["status"] != "deferred"]
        out.append({"start_date": start, "due_date": due, "closed_at": closed, "outcome": outcome,
                    "done": sum(i["status"] == "done" for i in counted), "total": len(counted), "phases": phases})
    return out


# -- rollover --------------------------------------------------------------

def _state(conn, task_id):
    start, due, archived = conn.execute("SELECT start_date, due_date, archived_at FROM task WHERE id = ?",
                                        (task_id,)).fetchone()
    phases = []
    for pid, name in conn.execute("SELECT id, name FROM phase WHERE task_id = ? ORDER BY position", (task_id,)):
        items = [{"text": t, "status": s} for t, s in conn.execute(
            "SELECT text, status FROM item WHERE phase_id = ? ORDER BY position", (pid,))]
        phases.append({"name": name, "items": items})
    return date.fromisoformat(start), date.fromisoformat(due), archived, phases


def _complete(phases):
    counted = [i for p in phases for i in p["items"] if i["status"] != "deferred"]
    return bool(counted) and all(i["status"] == "done" for i in counted)


def roll(conn, task_id, today):
    """Close every cycle of this task that has ended. Returns the outcomes closed, oldest first."""
    rec = load(conn, task_id)
    if not rec:
        return []
    closed = []
    with conn:
        while len(closed) < MAX_CATCHUP:
            start, due, archived, phases = _state(conn, task_id)
            if archived:
                break
            if _complete(phases):
                outcome = "complete"
            elif today > due:
                outcome = "incomplete"
            else:
                break
            now = utcnow()
            conn.execute("INSERT INTO cycle(id, task_id, start_date, due_date, closed_at, outcome, snapshot) "
                         "VALUES (?, ?, ?, ?, ?, ?, ?)",
                         (uuid.uuid4().hex, task_id, start.isoformat(), due.isoformat(), now, outcome,
                          json.dumps(phases)))
            new_due = next_due(rec, due)
            new_start = due + timedelta(days=1)
            conn.execute("UPDATE task SET start_date = ?, due_date = ?, updated_at = ? WHERE id = ?",
                         (new_start.isoformat(), new_due.isoformat(), now, task_id))
            conn.execute("UPDATE item SET status = 'pending', status_changed_at = ? "
                         "WHERE phase_id IN (SELECT id FROM phase WHERE task_id = ?)", (now, task_id))
            conn.execute("INSERT INTO event(id, task_id, item_id, kind, from_value, to_value, at) "
                         "VALUES (?, ?, NULL, 'cycle', ?, ?, ?)",
                         (uuid.uuid4().hex, task_id, outcome, new_due.isoformat(), now))
            closed.append(outcome)
    return closed


def roll_all(conn, today):
    ids = [r[0] for r in conn.execute(
        "SELECT t.id FROM task t JOIN recurrence r ON r.task_id = t.id WHERE t.archived_at IS NULL")]
    return sum(len(roll(conn, tid, today)) for tid in ids)
