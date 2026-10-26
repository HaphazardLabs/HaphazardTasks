# HaphazardTasks

Local, encrypted task dashboard. Each task is a set of concentric rings: one per checklist phase,
plus an outer day-cycle ring from start date to due date. Runs only on `127.0.0.1:8796`.

Version 1.0.0 · MIT license · single user · Linux and Windows

## Download

- **Windows:** download `HaphazardTasks-windows-x64-1.0.0.zip` from
  [Releases](https://github.com/HaphazardLabs/HaphazardTasks/releases), unzip it under your user profile and
  double-click `Start HaphazardTasks.bat`. No installer or admin rights. Check the zip's SHA-256 against the
  one in the release notes.
- **Linux:** clone the repo and run `./install.sh` (needs Python 3.11+).
- **Asking IT first?** [docs/IT-REVIEW.md](docs/IT-REVIEW.md) is a one-page summary of what it installs,
  what it touches on the network, how data is protected and how to remove it.

## Status

| Phase | Scope | State |
| --- | --- | --- |
| 1 | Encrypted vault, setup + recovery key, unlock/lock, auto-lock, hardened localhost server | Done |
| 2 | Tasks, phases, items, phase rings, day-cycle ring, task grid, status menu, editor | Done |
| 3 | Overview strip, focus panel + history, Markdown notes, tags, search, archive, drag reorder, shortcuts, settings | Done |
| 4 | Templates (user-made only) and recurring tasks with cycle rollover and past cycles | Done |
| 5 | Linux installer, Windows portable zip, encrypted backup export/import, hash-pinned dependencies | Done |

## Install and run

**Linux:** `./install.sh` once (creates `.venv` from `requirements.lock`, adds the desktop
icon), then the **HaphazardTasks** icon or `./launch.sh`. `HT_NO_DESKTOP=1 ./install.sh` skips the icon.

**Windows:** use the release zip, or build it yourself: `python packaging/build_windows.py` builds
`dist/HaphazardTasks-windows-x64-1.0.0.zip`. Unzip it under your profile and double-click
`Start HaphazardTasks.bat`. No installer, no admin rights. See `packaging/windows/README-WINDOWS.txt`.

The launcher starts the server if needed and opens Chrome/Edge as an app window. The server stops
itself 30 minutes after the vault locks.

## Where things live

| | Linux | Windows |
| --- | --- | --- |
| Vault | `~/.local/share/HaphazardTasks/vault.htv` | `%APPDATA%\HaphazardTasks\vault.htv` |
| Previous versions | `vault.htv.1` … `.5` (encrypted) | same |
| Log (paths and status codes only) | `server.log` beside the vault | same |

Each computer keeps its own separate vault. To move data, use Settings → Export encrypted backup on one
and Restore from a backup file on the other (same file format on both). `HAPHAZARDTASKS_DATA`
overrides the folder (used for testing).

## Security

- The whole SQLite database lives in memory while unlocked and is written to disk only inside
  `vault.htv`, sealed with AES-256-GCM. The header is authenticated; any edit is detected.
- A random data key is wrapped by an Argon2id key from your passphrase (128 MiB, passes tuned to
  ~0.5 s on first run) and separately by the recovery key. Lose both and the data is gone.
- The server binds 127.0.0.1 only and answers only `127.0.0.1:8796`/`localhost:8796` Host headers
  (blocks DNS rebinding). Changes need a same-origin `Origin`, a JSON body and a CSRF token;
  cross-site `Sec-Fetch-Site` is refused. Strict CSP (no inline script, no eval), `no-store` on
  every response, HttpOnly + SameSite=Strict session cookie.
- Auto-lock (1–120 min, default 15) runs on the server, so closing the tab doesn't keep the vault
  open. Five failed unlocks start a growing delay, up to 60 s.
- Vault files are owner-only (0600, folder 0700 on Linux). No CDNs, no telemetry, no outbound calls.
  Turn on full-disk encryption (LUKS / BitLocker): memory can reach swap.
- Dependencies install only from `requirements.lock` with `--require-hashes`. The bundled Windows
  Python matches the SHA-256 python.org publishes.

## Recurring tasks

Repeat daily, weekly on chosen weekdays, monthly on a day (or the last day), or every N days.
A cycle closes when every item is done, or when its due date passes. Closing saves a snapshot to
Past cycles (complete or incomplete), moves the task to the next window and resets the checklist.
Windows missed while the app was closed are each recorded as incomplete.

## Keyboard

`N` new task · `/` search · `L` lock · `Esc` close · `↑`/`↓` move through a checklist ·
`1`–`5` set Pending, Underway, Done, Blocked, Deferred · `Alt+↑/↓` move an item or phase in the editor.

## Tests

```
.venv/bin/python -m pytest -q        # 91 tests: vault, server guard, tasks, recurrence, templates, backup
node --test tests/*.cjs              # 15 tests: ring geometry, day-ring and health edge cases
```

Browser walkthroughs (Playwright + headless Chrome, against a throwaway vault; instructions at the
top of each file): `tests/ui/walkthrough.py` (44 checks: every view by mouse and keyboard, real
1-minute auto-lock) and `tests/ui/walkthrough_p45.py` (27 checks: recurrence, templates, backup).

## Layout

```
app/vault.py          vault format, crypto, atomic save, versions
app/model.py          schema, migrations, settings
app/tasks.py          tasks/phases/items: validation, diff-based edits, status changes, history, archive
app/recurrence.py     repeat rules, next due dates, cycle rollover
app/templates.py      user templates
app/server.py         Flask app: guard, session, auto-lock, API, backup
app/run.py            waitress on 127.0.0.1:8796
app/static/           index.html, app.css, rings.js (geometry), dashboard.js (UI), app.js (setup/unlock),
                      bundled fonts (OFL), icons
launch.py             start server if needed, open an app window
install.sh, launch.sh Linux
packaging/            build_windows.py, windows/ (launcher .bat, shortcut helper, README)
requirements.in/.lock dependencies; regenerate the lock with pip-compile --generate-hashes
tests/
```

## License

MIT, see [LICENSE](LICENSE). Bundled fonts: SIL Open Font License 1.1.

No warranty. HaphazardTasks has automated tests but no independent security audit. Don't use it for
regulated or classified data unless your organisation approves it.
