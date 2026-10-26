# HaphazardTasks: Summary for IT Review

> A summary you can send to your IT team when asking to run HaphazardTasks on a work computer.
> It is written in the first person; adjust the request and intended use to match yours.

**Request:** approval to run HaphazardTasks 1.0.0, a personal task and checklist tracker, on my Windows work computer. It needs no admin rights, no installer and no network access. It runs only on this machine, as my user account.

HaphazardTasks is a small local web app. A Python server listens on `127.0.0.1:8796`, and Edge opens it as an app window. Task data is stored in one encrypted file in my user profile. The full source is available for review at [github.com/HaphazardLabs/HaphazardTasks](https://github.com/HaphazardLabs/HaphazardTasks) under the MIT license.

## Footprint on the machine

| Area | What it does |
| --- | --- |
| Install | Unzip a portable folder (18.6 MB zip) under my profile, e.g. `Documents\HaphazardTasks`. No installer or MSI. |
| Privileges | Runs as my standard user account. Never requests elevation. |
| Files written | `%APPDATA%\HaphazardTasks\`: `vault.htv` (encrypted data), `vault.htv.1`–`.5` (previous encrypted versions), `server.log`. Optional desktop shortcut (`.lnk`). |
| Registry, services, tasks | None. No registry keys, Windows services, scheduled tasks, drivers or autostart entries. |
| Processes | `python\pythonw.exe` from the app folder (the server), plus Edge or Chrome opened with `--app=http://127.0.0.1:8796/`. The server exits by itself 30 minutes after the vault locks. |
| Launch | `Start HaphazardTasks.bat` (two lines; starts `pythonw.exe launch.py`). |

## Network behaviour

- **Listens only on loopback:** TCP `127.0.0.1:8796`. The bind address is hard-coded and not configurable. It is unreachable from the LAN, and no Windows Firewall rule or prompt is involved.
- **No outbound connections:** no telemetry, update checks, CDNs or cloud services. Fonts and scripts are bundled. The launcher's "already running?" check is a loopback request that bypasses any configured proxy.
- **Hardened against local web attacks:**
  - Requests are refused unless the Host header is `127.0.0.1:8796` or `localhost:8796` (blocks DNS rebinding).
  - State-changing requests need a same-origin `Origin`, a JSON body and a CSRF token. Cross-site `Sec-Fetch-Site` is refused.
  - Responses carry a strict Content-Security-Policy (no inline script, no eval), `X-Frame-Options: DENY` and `Cache-Control: no-store`.
  - The session cookie is HttpOnly and SameSite=Strict.

## Data protection

- **At rest:** the whole database is sealed in `vault.htv` with AES-256-GCM, with the file header authenticated. Plaintext task data is never written to disk; it exists only in process memory while unlocked.
- **Keys:** a random 256-bit data key is wrapped by a key derived from my passphrase (Argon2id, 128 MiB) and separately by a one-time recovery key. Neither the passphrase nor any key is stored.
- **Access:** a passphrase is required to unlock. The vault auto-locks after 15 idle minutes (adjustable from 1 to 120) on a server-side timer, so it locks even with the window closed. Repeated failed unlocks are delayed, up to 60 seconds.
- **Logs:** `server.log` records request paths and status codes only, never task content.
- **Data movement:** none automatic. A manual "Export encrypted backup" writes a copy of the encrypted file; it opens only with the passphrase.
- **What it does not protect against:** anyone with administrative or endpoint-monitoring access to this computer while the vault is unlocked. This tool is not intended for CUI, ITAR, classified or customer-regulated data.

## Third-party components

Every package is pinned to an exact version and installed only if its SHA-256 matches the lock file (`pip --require-hashes`). The Python runtime is checked against the SHA-256 python.org publishes.

| Component | Version | Purpose |
| --- | --- | --- |
| Python embeddable package (python.org, signed by the PSF) | 3.14.4 | Runtime. SHA-256 `cda80a9b1e75c0f1b4f9872ca1b417f0d19bce32facc811aea9180e70fad5fb9` |
| cryptography | 50.0.2 | AES-256-GCM, HKDF |
| argon2-cffi / argon2-cffi-bindings | 25.1.0 / 26.1.0 | Argon2id key derivation |
| Flask, Werkzeug | 3.1.3, 3.1.9 | Web framework |
| waitress | 3.0.2 | WSGI server (pure Python) |
| Jinja2, MarkupSafe, itsdangerous, click, blinker, colorama, cffi, pycparser | 3.1.6, 3.0.4, 2.2.0, 8.5.0, 1.9.0, 0.4.6, 2.1.1, 3.12 | Flask and cffi dependencies |
| Archivo, IBM Plex Mono fonts | bundled | UI fonts, SIL Open Font License |

## Removal

1. Lock the vault, or wait 30 minutes after it locks, so `pythonw.exe` exits. Task Manager can also end it.
2. Delete the app folder (e.g. `Documents\HaphazardTasks`).
3. Delete `%APPDATA%\HaphazardTasks`, plus the desktop shortcut if one was created.

Nothing else remains on the system.

## Known limitations

- The `.bat` launchers and app scripts are not code-signed. The Python runtime binaries are signed by the Python Software Foundation.
- The code has automated tests (91 unit and API tests, plus browser walkthroughs) but has not had an independent security audit.
- It is a single-user tool and does not sync to other machines.
- Endpoint protection may flag a Python process listening on a local port. That traffic is loopback only.
