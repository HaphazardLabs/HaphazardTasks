import pytest

from app.vault import Vault
from tests.test_server import BASE, ORIGIN, PASS, get, post, setup_vault  # noqa: F401  (fixtures below)
from tests.test_server import app, client, clock  # noqa: F401


def send(client, method, path, csrf, json=None):
    return client.open(path, method=method, json=json or {}, base_url=BASE,
                       headers={**ORIGIN, "X-CSRF-Token": csrf})


def sample(**over):
    t = {
        "title": "Q4 vendor security review", "priority": "High",
        "start_date": "2026-10-01", "due_date": "2026-11-14", "notes_md": "Three vendors.",
        "tags": ["example"],
        "phases": [
            {"name": "Discovery", "items": [{"text": "Collect SOC 2 reports", "status": "done"},
                                            {"text": "Map data flows", "status": "done"}]},
            {"name": "Assessment", "items": [{"text": "Draft risk register", "status": "underway"},
                                             {"text": "Review pen-test results", "status": "blocked"}]},
        ],
    }
    t.update(over)
    return t


@pytest.fixture
def session(client):
    _, csrf = setup_vault(client)
    return csrf


def test_create_and_list(client, session):
    r = send(client, "POST", "/api/tasks", session, sample())
    assert r.status_code == 201
    t = r.get_json()
    assert [p["name"] for p in t["phases"]] == ["Discovery", "Assessment"]
    assert [i["status"] for i in t["phases"][1]["items"]] == ["underway", "blocked"]
    listed = get(client, "/api/tasks").get_json()
    assert len(listed) == 1 and listed[0]["id"] == t["id"]


@pytest.mark.parametrize("bad, msg", [
    ({"title": "  "}, "Title"),
    ({"priority": "Urgent"}, "Priority"),
    ({"start_date": "next week"}, "Start date"),
    ({"due_date": "2026-09-01"}, "before the start"),
    ({"phases": []}, "1 to 4 phases"),
    ({"phases": [{"name": f"P{i}", "items": [{"text": "x"}]} for i in range(5)]}, "1 to 4 phases"),
    ({"phases": [{"name": "P", "items": []}]}, "at least one item"),
    ({"phases": [{"name": "P", "items": [{"text": "x", "status": "maybe"}]}]}, "Unknown status"),
])
def test_validation(client, session, bad, msg):
    r = send(client, "POST", "/api/tasks", session, sample(**bad))
    assert r.status_code == 400
    assert msg in r.get_json()["error"]


def test_status_change_survives_lock_and_unlock(client, session, app):
    t = send(client, "POST", "/api/tasks", session, sample()).get_json()
    item = t["phases"][1]["items"][1]
    r = send(client, "PATCH", f"/api/items/{item['id']}", session, {"status": "done"})
    assert r.status_code == 200
    assert r.get_json()["phases"][1]["items"][1]["status"] == "done"
    post(client, "/api/lock", csrf=session)
    assert post(client, "/api/unlock", {"passphrase": PASS}).status_code == 200
    again = get(client, f"/api/tasks/{t['id']}").get_json()
    assert again["phases"][1]["items"][1]["status"] == "done"
    hist = get(client, f"/api/tasks/{t['id']}/history").get_json()
    assert hist[0]["kind"] == "status" and hist[0]["from"] == "blocked" and hist[0]["to"] == "done"


def test_background_saver_writes_within_a_second(client, session, app):
    """Changes reach the encrypted file without a lock: the saver flushes dirty state."""
    import time
    from app.server import start_background
    state = app.extensions["ht_state"]
    start_background(app, quit_after_locked=0, interval=0.05)
    send(client, "POST", "/api/tasks", session, sample(title="Saved by the background thread"))
    for _ in range(40):
        if not state.vault.dirty:
            break
        time.sleep(0.05)
    assert not state.vault.dirty
    v = Vault.open(state.vault_path, passphrase=PASS)
    assert v.conn.execute("SELECT title FROM task").fetchone()[0] == "Saved by the background thread"
    v.close(save=False)


def test_edit_keeps_ids_and_statuses(client, session):
    t = send(client, "POST", "/api/tasks", session, sample()).get_json()
    disc, assess = t["phases"]
    # Swap phase order, rename an item, move an item between phases, drop one, add one.
    edited = sample(title="Renamed", phases=[
        {"id": assess["id"], "name": "Assessment", "items": [
            {"id": assess["items"][0]["id"], "text": "Draft risk register v2", "status": "underway"},
            {"id": disc["items"][1]["id"], "text": "Map data flows", "status": "done"},
            {"text": "New item"}]},
        {"id": disc["id"], "name": "Discovery", "items": [
            {"id": disc["items"][0]["id"], "text": "Collect SOC 2 reports", "status": "done"}]},
    ])
    r = send(client, "PUT", f"/api/tasks/{t['id']}", session, edited)
    assert r.status_code == 200, r.get_json()
    out = r.get_json()
    assert out["title"] == "Renamed"
    assert [p["id"] for p in out["phases"]] == [assess["id"], disc["id"]]
    items = out["phases"][0]["items"]
    assert items[0]["id"] == assess["items"][0]["id"] and items[0]["text"] == "Draft risk register v2"
    assert items[1]["id"] == disc["items"][1]["id"] and items[1]["status"] == "done"
    assert items[2]["status"] == "pending"
    all_ids = {i["id"] for p in out["phases"] for i in p["items"]}
    assert assess["items"][1]["id"] not in all_ids  # dropped item is gone


def test_edit_ignores_foreign_ids(client, session):
    a = send(client, "POST", "/api/tasks", session, sample(title="A")).get_json()
    b = send(client, "POST", "/api/tasks", session, sample(title="B")).get_json()
    stolen = a["phases"][0]["items"][0]["id"]
    r = send(client, "PUT", f"/api/tasks/{b['id']}", session, sample(
        title="B", phases=[{"name": "P", "items": [{"id": stolen, "text": "hijack", "status": "done"}]}]))
    assert r.status_code == 200
    assert get(client, f"/api/tasks/{a['id']}").get_json()["phases"][0]["items"][0]["text"] == "Collect SOC 2 reports"
    assert r.get_json()["phases"][0]["items"][0]["id"] != stolen


def test_delete(client, session):
    t = send(client, "POST", "/api/tasks", session, sample()).get_json()
    assert send(client, "DELETE", f"/api/tasks/{t['id']}", session).status_code == 200
    assert get(client, f"/api/tasks/{t['id']}").status_code == 404
    assert get(client, "/api/tasks").get_json() == []


def test_unknown_item_and_task(client, session):
    assert send(client, "PATCH", "/api/items/nope", session, {"status": "done"}).status_code == 404
    assert send(client, "PUT", "/api/tasks/nope", session, sample()).status_code == 404


def test_task_routes_need_session_and_csrf(client, session):
    other = client.application.test_client()
    assert other.get("/api/tasks", base_url=BASE).status_code == 401
    r = client.post("/api/tasks", json=sample(), base_url=BASE, headers=ORIGIN)
    assert r.status_code == 403


def test_item_text_is_stored_verbatim_not_interpreted(client, session):
    evil = '<img src=x onerror="alert(1)">'
    t = send(client, "POST", "/api/tasks", session,
             sample(title=evil, phases=[{"name": evil, "items": [{"text": evil}]}])).get_json()
    assert t["title"] == evil and t["phases"][0]["items"][0]["text"] == evil


def test_archive_and_restore(client, session):
    t = send(client, "POST", "/api/tasks", session, sample()).get_json()
    r = send(client, "POST", f"/api/tasks/{t['id']}/archive", session)
    assert r.status_code == 200 and r.get_json()["archived_at"]
    assert get(client, "/api/tasks").get_json() == []
    arch = get(client, "/api/tasks?archived=1").get_json()
    assert [x["id"] for x in arch] == [t["id"]]
    r = send(client, "POST", f"/api/tasks/{t['id']}/restore", session)
    assert r.get_json()["archived_at"] is None
    assert len(get(client, "/api/tasks").get_json()) == 1
    kinds = [e["kind"] for e in get(client, f"/api/tasks/{t['id']}/history").get_json()]
    assert kinds[:2] == ["restored", "archived"]


def test_archive_survives_lock(client, session):
    t = send(client, "POST", "/api/tasks", session, sample()).get_json()
    send(client, "POST", f"/api/tasks/{t['id']}/archive", session)
    post(client, "/api/lock", csrf=session)
    post(client, "/api/unlock", {"passphrase": PASS})
    assert len(get(client, "/api/tasks?archived=1").get_json()) == 1


def test_settings_validate_and_apply_auto_lock(client, session, app, clock):
    assert get(client, "/api/settings").get_json() == {"auto_lock_minutes": 15, "theme": "system"}
    for bad in ({"auto_lock_minutes": 0}, {"auto_lock_minutes": 500}, {"auto_lock_minutes": "x"}, {"theme": "neon"}):
        assert send(client, "PUT", "/api/settings", session, bad).status_code == 400
    state = app.extensions["ht_state"]
    state.idle_override = None
    r = send(client, "PUT", "/api/settings", session, {"auto_lock_minutes": 2, "theme": "dark"})
    assert r.get_json() == {"auto_lock_minutes": 2, "theme": "dark"}
    assert state.idle_seconds == 120
    clock.t += 121
    assert get(client, "/api/tasks").status_code == 401  # locked by the new, shorter timeout
    post(client, "/api/unlock", {"passphrase": PASS})
    assert get(client, "/api/settings").get_json()["theme"] == "dark"
    assert state.idle_seconds == 120
