import base64

from app.vault import Vault
from tests.test_server import BASE, ORIGIN, PASS, get, post, setup_vault  # noqa: F401
from tests.test_server import app, client, clock  # noqa: F401
from tests.test_tasks import sample, send

MARKER = "BACKUP-MARKER-QUOKKA-5521"


def test_export_is_the_encrypted_vault(client, app):
    _, csrf = setup_vault(client)
    send(client, "POST", "/api/tasks", csrf, sample(title=MARKER))
    r = send(client, "POST", "/api/backup/export", csrf)
    assert r.status_code == 200
    assert r.headers["Content-Disposition"].startswith('attachment; filename="HaphazardTasks-backup-')
    assert r.headers["Cache-Control"] == "no-store"
    blob = r.data
    assert blob[:8] == b"HTVAULT1"
    assert MARKER.encode() not in blob
    assert blob == app.extensions["ht_state"].vault_path.read_bytes()


def test_export_needs_session_and_csrf(client):
    _, csrf = setup_vault(client)
    assert client.post("/api/backup/export", json={}, base_url=BASE, headers=ORIGIN).status_code == 403
    other = client.application.test_client()
    r = other.post("/api/backup/export", json={}, base_url=BASE, headers={**ORIGIN, "X-CSRF-Token": csrf})
    assert r.status_code == 401


def test_import_round_trip_keeps_old_vault_as_version(client, app, tmp_path):
    _, csrf = setup_vault(client)
    send(client, "POST", "/api/tasks", csrf, sample(title="Before backup"))
    backup = send(client, "POST", "/api/backup/export", csrf).data
    send(client, "POST", "/api/tasks", csrf, sample(title="Added after backup"))
    r = send(client, "POST", "/api/backup/import", csrf,
             {"file_b64": base64.b64encode(backup).decode(), "passphrase": PASS})
    assert r.status_code == 200, r.get_json()
    csrf2 = r.get_json()["csrf"]
    assert [t["title"] for t in get(client, "/api/tasks").get_json()] == ["Before backup"]
    # the replaced vault (with the newer task) survives as vault.htv.1
    v = Vault.open(app.extensions["ht_state"].vault_path.with_name("vault.htv.1"), passphrase=PASS)
    titles = [r[0] for r in v.conn.execute("SELECT title FROM task ORDER BY title")]
    v.close(save=False)
    assert "Added after backup" in titles
    assert send(client, "POST", "/api/activity", csrf2).status_code == 200
    assert not list(tmp_path.glob("import.tmp*"))


def test_import_wrong_passphrase_changes_nothing(client, app, tmp_path):
    _, csrf = setup_vault(client)
    send(client, "POST", "/api/tasks", csrf, sample(title="Keep me"))
    backup = send(client, "POST", "/api/backup/export", csrf).data
    before = app.extensions["ht_state"].vault_path.read_bytes()
    r = send(client, "POST", "/api/backup/import", csrf,
             {"file_b64": base64.b64encode(backup).decode(), "passphrase": "not the passphrase"})
    assert r.status_code == 401
    assert app.extensions["ht_state"].vault_path.read_bytes() == before
    assert get(client, "/api/tasks").status_code == 200  # still unlocked, same session
    assert not list(tmp_path.glob("import.tmp*"))


def test_import_rejects_garbage_and_tampering(client):
    _, csrf = setup_vault(client)
    backup = bytearray(send(client, "POST", "/api/backup/export", csrf).data)
    r = send(client, "POST", "/api/backup/import", csrf, {"file_b64": base64.b64encode(b"hello").decode(), "passphrase": PASS})
    assert r.status_code == 400
    backup[-3] ^= 1
    r = send(client, "POST", "/api/backup/import", csrf, {"file_b64": base64.b64encode(bytes(backup)).decode(), "passphrase": PASS})
    assert r.status_code == 400 and "altered" in r.get_json()["error"]
    r = send(client, "POST", "/api/backup/import", csrf, {"file_b64": "%%%", "passphrase": PASS})
    assert r.status_code == 400
