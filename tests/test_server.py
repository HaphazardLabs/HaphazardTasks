import pytest

from app.server import COOKIE, create_app, start_background

FAST_KDF = {"m": 8192, "t": 1, "p": 1}
PASS = "correct horse battery staple"
BASE = "http://127.0.0.1:8796"
ORIGIN = {"Origin": BASE}


class Clock:
    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        return self.t


@pytest.fixture
def clock():
    return Clock()


@pytest.fixture
def app(tmp_path, clock):
    return create_app(data_dir=tmp_path, kdf=FAST_KDF, idle_seconds=60, clock=clock, log_to_file=False)


@pytest.fixture
def client(app):
    return app.test_client()


def post(client, path, json=None, csrf=None, headers=None):
    h = dict(ORIGIN)
    if csrf:
        h["X-CSRF-Token"] = csrf
    h.update(headers or {})
    return client.post(path, json=json or {}, headers=h, base_url=BASE)


def get(client, path, headers=None):
    return client.get(path, headers=headers or {}, base_url=BASE)


def setup_vault(client):
    key = post(client, "/api/setup/begin").get_json()["recovery_key"]
    check = key.replace("-", "")[-8:]
    r = post(client, "/api/setup/finish", {"passphrase": PASS, "recovery_check": check})
    assert r.status_code == 200, r.get_json()
    return key, r.get_json()["csrf"]


# -- guard ------------------------------------------------------------------

def test_foreign_host_refused(client):
    r = client.get("/api/status", base_url="http://evil.example:8796")
    assert r.status_code == 403
    r = client.get("/", headers={"Host": "attacker.test"}, base_url=BASE)
    assert r.status_code == 403


def test_localhost_name_allowed(client):
    assert client.get("/api/status", base_url="http://localhost:8796").status_code == 200


def test_post_without_origin_refused(client):
    r = client.post("/api/setup/begin", json={}, base_url=BASE)
    assert r.status_code == 403


def test_post_foreign_origin_refused(client):
    r = post(client, "/api/setup/begin", headers={"Origin": "https://evil.example"})
    assert r.status_code == 403


def test_cross_site_fetch_refused(client):
    r = get(client, "/api/status", headers={"Sec-Fetch-Site": "cross-site"})
    assert r.status_code == 403


def test_form_post_refused(client):
    r = client.post("/api/unlock", data={"passphrase": PASS}, headers=ORIGIN, base_url=BASE)
    assert r.status_code == 415


def test_security_headers(client):
    r = get(client, "/")
    assert r.status_code == 200
    assert "default-src 'self'" in r.headers["Content-Security-Policy"]
    assert "frame-ancestors 'none'" in r.headers["Content-Security-Policy"]
    assert r.headers["Cache-Control"] == "no-store"
    assert r.headers["X-Content-Type-Options"] == "nosniff"
    assert "Access-Control-Allow-Origin" not in r.headers


# -- setup and unlock ---------------------------------------------------------

def test_setup_requires_recovery_confirmation(client):
    post(client, "/api/setup/begin")
    r = post(client, "/api/setup/finish", {"passphrase": PASS, "recovery_check": "AAAAAAAA"})
    assert r.status_code == 400
    assert not get(client, "/api/status").get_json()["vault_exists"]


def test_setup_rejects_short_passphrase(client):
    key = post(client, "/api/setup/begin").get_json()["recovery_key"]
    r = post(client, "/api/setup/finish", {"passphrase": "short", "recovery_check": key[-9:]})
    assert r.status_code == 400


def test_setup_then_session(client):
    setup_vault(client)
    s = get(client, "/api/session").get_json()
    assert s["unlocked"] and s["csrf"]
    assert post(client, "/api/setup/begin").status_code == 409


def test_cookie_flags(client):
    post(client, "/api/setup/begin")
    key = post(client, "/api/setup/begin").get_json()["recovery_key"]
    r = post(client, "/api/setup/finish", {"passphrase": PASS, "recovery_check": key[-9:]})
    cookie = r.headers["Set-Cookie"]
    assert COOKIE in cookie and "HttpOnly" in cookie and "SameSite=Strict" in cookie


def test_api_requires_session(client):
    setup_vault(client)
    other = client.application.test_client()  # no cookie
    r = other.get("/api/vault", base_url=BASE)
    assert r.status_code == 401 and r.get_json()["locked"]


def test_csrf_required(client):
    _, csrf = setup_vault(client)
    assert post(client, "/api/activity").status_code == 403
    assert post(client, "/api/activity", csrf="wrong").status_code == 403
    assert post(client, "/api/activity", csrf=csrf).status_code == 200


def test_lock_and_unlock(client):
    _, csrf = setup_vault(client)
    assert post(client, "/api/lock", csrf=csrf).status_code == 200
    assert get(client, "/api/vault").status_code == 401
    r = post(client, "/api/unlock", {"passphrase": PASS})
    assert r.status_code == 200 and not r.get_json()["must_change_passphrase"]
    assert get(client, "/api/vault").status_code == 200


def test_wrong_passphrase_and_backoff(client, clock):
    _, csrf = setup_vault(client)
    post(client, "/api/lock", csrf=csrf)
    for _ in range(5):
        assert post(client, "/api/unlock", {"passphrase": "nope nope nope"}).status_code == 401
    r = post(client, "/api/unlock", {"passphrase": PASS})
    assert r.status_code == 429 and r.get_json()["retry_after"] >= 1
    clock.t += 2
    assert post(client, "/api/unlock", {"passphrase": PASS}).status_code == 200


def test_recovery_unlock_forces_new_passphrase(client):
    key, csrf = setup_vault(client)
    post(client, "/api/lock", csrf=csrf)
    r = post(client, "/api/unlock", {"recovery_key": key})
    assert r.status_code == 200 and r.get_json()["must_change_passphrase"]
    csrf = r.get_json()["csrf"]
    assert post(client, "/api/passphrase", {"new_passphrase": "another long passphrase"}, csrf=csrf).status_code == 200
    post(client, "/api/lock", csrf=csrf)
    assert post(client, "/api/unlock", {"passphrase": PASS}).status_code == 401
    assert post(client, "/api/unlock", {"passphrase": "another long passphrase"}).status_code == 200


def test_change_passphrase_needs_current(client):
    _, csrf = setup_vault(client)
    r = post(client, "/api/passphrase", {"current_passphrase": "wrong", "new_passphrase": "x" * 20}, csrf=csrf)
    assert r.status_code == 401
    r = post(client, "/api/passphrase", {"current_passphrase": PASS, "new_passphrase": "x" * 20}, csrf=csrf)
    assert r.status_code == 200


# -- auto-lock -----------------------------------------------------------------

def test_idle_locks_on_next_request(client, clock):
    _, csrf = setup_vault(client)
    clock.t += 59
    assert get(client, "/api/session").get_json()["idle_seconds_left"] == 1
    clock.t += 1
    assert get(client, "/api/vault").status_code == 401
    assert not client.application.extensions["ht_state"].unlocked


def test_activity_resets_idle(client, clock):
    _, csrf = setup_vault(client)
    clock.t += 50
    post(client, "/api/activity", csrf=csrf)
    clock.t += 50
    assert get(client, "/api/vault").status_code == 200


def test_session_poll_is_not_activity(client, clock):
    setup_vault(client)
    for _ in range(6):
        clock.t += 10
        get(client, "/api/session")
    assert get(client, "/api/vault").status_code == 401


def test_watchdog_locks_with_no_requests(app, client, clock):
    """The tab is closed: no requests arrive, yet the vault still locks."""
    import time
    setup_vault(client)
    state = app.extensions["ht_state"]
    quits = []
    start_background(app, quit_after_locked=120, interval=0.02, on_quit=lambda: quits.append(1))
    clock.t += 61
    for _ in range(100):
        if not state.unlocked:
            break
        time.sleep(0.02)
    assert not state.unlocked and state.vault is None
    clock.t += 121
    for _ in range(100):
        if quits:
            break
        time.sleep(0.02)
    assert quits == [1]
