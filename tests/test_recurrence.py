from datetime import date

import pytest

from app import recurrence
from tests.test_server import BASE, ORIGIN, PASS, get, post, setup_vault  # noqa: F401
from tests.test_server import client, clock  # noqa: F401
from app.server import create_app

FAST_KDF = {"m": 8192, "t": 1, "p": 1}
D = date.fromisoformat


class Today:
    def __init__(self, d):
        self.d = D(d)

    def __call__(self):
        return self.d


# -- pure date rules ------------------------------------------------------------

def test_next_due_daily_and_interval():
    assert recurrence.next_due({"pattern": "daily"}, D("2026-12-31")) == D("2027-01-01")
    assert recurrence.next_due({"pattern": "interval", "every_n_days": 10}, D("2026-10-26")) == D("2026-11-05")


def test_next_due_weekly():
    mon_fri = {"pattern": "weekly", "weekdays": [0, 4]}
    assert recurrence.next_due(mon_fri, D("2026-10-30")) == D("2026-11-02")  # Fri -> Mon
    assert recurrence.next_due(mon_fri, D("2026-11-02")) == D("2026-11-06")  # Mon -> Fri
    assert recurrence.next_due({"pattern": "weekly", "weekdays": [4]}, D("2026-10-30")) == D("2026-11-06")


@pytest.mark.parametrize("after, md, expected", [
    ("2026-01-31", -1, "2026-02-28"),   # last day, short month
    ("2028-01-31", -1, "2028-02-29"),   # last day, leap year
    ("2026-02-28", -1, "2026-03-31"),
    ("2026-01-31", 31, "2026-02-28"),   # day 31 clamps to February's end
    ("2026-02-28", 31, "2026-03-31"),   # ...and returns to 31 in March
    ("2026-10-15", 15, "2026-11-15"),
    ("2026-10-10", 15, "2026-10-15"),   # later in the same month
    ("2026-12-31", 1, "2027-01-01"),    # year rollover
])
def test_next_due_monthly(after, md, expected):
    assert recurrence.next_due({"pattern": "monthly", "month_day": md}, D(after)) == D(expected)


@pytest.mark.parametrize("bad", [
    {"pattern": "hourly"}, {"pattern": "weekly", "weekdays": []}, {"pattern": "weekly", "weekdays": [7]},
    {"pattern": "monthly", "month_day": 0}, {"pattern": "monthly", "month_day": 32},
    {"pattern": "interval", "every_n_days": 0}, {"pattern": "interval", "every_n_days": "3"},
])
def test_validate_rejects(bad):
    with pytest.raises(recurrence.Invalid):
        recurrence.validate(bad)


def test_describe():
    assert recurrence.describe({"pattern": "weekly", "weekdays": [0, 4]}) == "Repeats weekly · Mon, Fri"
    assert recurrence.describe({"pattern": "monthly", "month_day": -1}) == "Repeats monthly · last day"


# -- rollover through the API with a simulated calendar ------------------------------

@pytest.fixture
def today():
    return Today("2026-10-26")


@pytest.fixture
def rapp(tmp_path, clock, today):  # noqa: F811
    return create_app(data_dir=tmp_path, kdf=FAST_KDF, idle_seconds=10 ** 6, clock=clock, log_to_file=False, today=today)


@pytest.fixture
def rc(rapp):
    return rapp.test_client()


@pytest.fixture
def csrf(rc):
    return setup_vault(rc)[1]


def send(c, method, path, csrf, json=None):
    return c.open(path, method=method, json=json or {}, base_url=BASE, headers={**ORIGIN, "X-CSRF-Token": csrf})


def make(c, csrf, rec, start, due, items=("One", "Two")):
    body = {"title": "Recurring", "priority": "Medium", "start_date": start, "due_date": due, "recurrence": rec,
            "phases": [{"name": "Cycle", "items": [{"text": t} for t in items]}]}
    r = send(c, "POST", "/api/tasks", csrf, body)
    assert r.status_code == 201, r.get_json()
    return r.get_json()


def task(c, tid):
    return get(c, f"/api/tasks/{tid}").get_json()


def cycles(c, tid):
    return get(c, f"/api/tasks/{tid}/cycles").get_json()


def test_completing_a_cycle_rolls_immediately(rc, csrf, today):
    t = make(rc, csrf, {"pattern": "weekly", "weekdays": [4]}, "2026-10-24", "2026-10-30")
    ids = [i["id"] for i in t["phases"][0]["items"]]
    r = send(rc, "PATCH", f"/api/items/{ids[0]}", csrf, {"status": "done"}).get_json()
    assert r["cycle_closed"] is None
    r = send(rc, "PATCH", f"/api/items/{ids[1]}", csrf, {"status": "done"}).get_json()
    assert r["cycle_closed"] == "complete"
    assert (r["start_date"], r["due_date"]) == ("2026-10-31", "2026-11-06")
    assert {i["status"] for i in r["phases"][0]["items"]} == {"pending"}
    cs = cycles(rc, t["id"])
    assert len(cs) == 1 and cs[0]["outcome"] == "complete" and cs[0]["done"] == 2 and cs[0]["total"] == 2
    assert cs[0]["phases"][0]["items"][0]["status"] == "done"   # snapshot keeps the final statuses
    assert get(rc, f"/api/tasks/{t['id']}/history").get_json()[0]["kind"] == "cycle"


def test_missed_cycles_recorded_incomplete(rc, csrf, today):
    t = make(rc, csrf, {"pattern": "weekly", "weekdays": [4]}, "2026-10-24", "2026-10-30")
    first = t["phases"][0]["items"][0]["id"]
    send(rc, "PATCH", f"/api/items/{first}", csrf, {"status": "done"})
    today.d = D("2026-11-17")   # away for 2.5 weeks: cycles due Oct 30, Nov 6 and Nov 13 all passed
    t2 = task(rc, t["id"])
    assert (t2["start_date"], t2["due_date"]) == ("2026-11-14", "2026-11-20")
    cs = cycles(rc, t["id"])
    assert [c["outcome"] for c in cs] == ["incomplete"] * 3
    assert [c["due_date"] for c in cs] == ["2026-11-13", "2026-11-06", "2026-10-30"]
    assert cs[-1]["done"] == 1   # the partly-done cycle keeps its real progress
    assert cs[0]["done"] == 0


def test_daily_rolls_each_day(rc, csrf, today):
    t = make(rc, csrf, {"pattern": "daily"}, "2026-10-26", "2026-10-26")
    today.d = D("2026-10-29")
    t2 = task(rc, t["id"])
    assert (t2["start_date"], t2["due_date"]) == ("2026-10-29", "2026-10-29")
    assert len(cycles(rc, t["id"])) == 3


def test_monthly_last_day_through_february(rc, csrf, today):
    t = make(rc, csrf, {"pattern": "monthly", "month_day": -1}, "2027-01-01", "2027-01-31")
    today.d = D("2027-03-05")
    t2 = task(rc, t["id"])
    assert (t2["start_date"], t2["due_date"]) == ("2027-03-01", "2027-03-31")
    assert [c["due_date"] for c in cycles(rc, t["id"])] == ["2027-02-28", "2027-01-31"]


def test_interval(rc, csrf, today):
    t = make(rc, csrf, {"pattern": "interval", "every_n_days": 10}, "2026-10-20", "2026-10-29")
    today.d = D("2026-11-09")
    t2 = task(rc, t["id"])
    assert (t2["start_date"], t2["due_date"]) == ("2026-11-09", "2026-11-18")


def test_not_rolled_on_due_day(rc, csrf, today):
    t = make(rc, csrf, {"pattern": "daily"}, "2026-10-26", "2026-10-26")
    assert task(rc, t["id"])["due_date"] == "2026-10-26"
    assert cycles(rc, t["id"]) == []


def test_archived_recurring_task_does_not_roll(rc, csrf, today):
    t = make(rc, csrf, {"pattern": "daily"}, "2026-10-26", "2026-10-26")
    send(rc, "POST", f"/api/tasks/{t['id']}/archive", csrf)
    today.d = D("2026-11-30")
    get(rc, "/api/tasks")
    assert cycles(rc, t["id"]) == []


def test_editing_recurrence_on_and_off(rc, csrf, today):
    t = make(rc, csrf, None, "2026-10-20", "2026-10-30")
    assert t["recurrence"] is None
    body = {**{k: t[k] for k in ("title", "priority", "start_date", "due_date", "notes_md", "tags")},
            "phases": t["phases"], "recurrence": {"pattern": "monthly", "month_day": 30}}
    r = send(rc, "PUT", f"/api/tasks/{t['id']}", csrf, body).get_json()
    assert r["recurrence_label"] == "Repeats monthly · day 30"
    body["recurrence"] = None
    assert send(rc, "PUT", f"/api/tasks/{t['id']}", csrf, body).get_json()["recurrence"] is None


def test_rollover_persists_through_lock(rc, csrf, today):
    t = make(rc, csrf, {"pattern": "daily"}, "2026-10-26", "2026-10-26")
    today.d = D("2026-10-28")
    get(rc, "/api/tasks")
    post(rc, "/api/lock", csrf=csrf)
    post(rc, "/api/unlock", {"passphrase": PASS})
    assert len(cycles(rc, t["id"])) == 2


# -- templates -------------------------------------------------------------------------

def test_template_from_task_drops_statuses_dates_notes(rc, csrf):
    t = make(rc, csrf, None, "2026-10-20", "2026-10-30", items=("Collect", "Review"))
    send(rc, "PATCH", f"/api/items/{t['phases'][0]['items'][0]['id']}", csrf, {"status": "done"})
    r = send(rc, "POST", "/api/templates", csrf, {"from_task": t["id"]})
    assert r.status_code == 201
    tpl = r.get_json()
    assert tpl["name"] == "Recurring"
    assert tpl["phases"] == [{"name": "Cycle", "items": ["Collect", "Review"]}]
    assert "start_date" not in tpl and "notes_md" not in tpl


def test_template_crud_and_validation(rc, csrf):
    r = send(rc, "POST", "/api/templates", csrf, {"name": "Vendor review", "phases": [
        {"name": "Discovery", "items": ["SOC 2", "Data flows"]}, {"name": "Close", "items": ["Summary"]}]})
    tpl = r.get_json()
    assert [x["name"] for x in get(rc, "/api/templates").get_json()] == ["Vendor review"]
    r = send(rc, "PUT", f"/api/templates/{tpl['id']}", csrf, {"name": "Vendor review v2", "phases": tpl["phases"][:1]})
    assert r.get_json()["name"] == "Vendor review v2" and len(r.get_json()["phases"]) == 1
    assert send(rc, "POST", "/api/templates", csrf, {"name": "", "phases": tpl["phases"]}).status_code == 400
    assert send(rc, "POST", "/api/templates", csrf, {"name": "x", "phases": [{"name": "P", "items": []}]}).status_code == 400
    assert send(rc, "DELETE", f"/api/templates/{tpl['id']}", csrf).status_code == 200
    assert get(rc, "/api/templates").get_json() == []
    assert send(rc, "DELETE", f"/api/templates/{tpl['id']}", csrf).status_code == 404


def test_no_templates_ship(rc, csrf):
    assert get(rc, "/api/templates").get_json() == []
