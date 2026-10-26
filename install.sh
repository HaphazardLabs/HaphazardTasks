#!/usr/bin/env bash
# Linux install: create the virtualenv, install pinned dependencies, add the desktop icon.
set -euo pipefail
cd "$(dirname "$(readlink -f "$0")")"
python3 -m venv .venv
./.venv/bin/pip install --quiet --require-hashes -r requirements.lock
APP="$PWD"
ENTRY="[Desktop Entry]
Type=Application
Version=1.0
Name=HaphazardTasks
GenericName=Encrypted Task Dashboard
Comment=Local, encrypted task dashboard with status rings
Exec=$APP/launch.sh
Icon=$APP/app/static/icon.svg
Terminal=false
Categories=Office;ProjectManagement;
Keywords=tasks;todo;checklist;dashboard;"
if [ -n "${HT_NO_DESKTOP:-}" ]; then
  echo "Installed (no desktop entry). Start with ./launch.sh"
  exit 0
fi
mkdir -p ~/.local/share/applications
printf '%s\n' "$ENTRY" > ~/.local/share/applications/HaphazardTasks.desktop
if [ -d ~/Desktop ]; then
  mkdir -p ~/Desktop/HaphazardTasks/Backups
  printf '%s\n' "$ENTRY" | sed 's/^Name=HaphazardTasks$/Name=Open HaphazardTasks/' > "$HOME/Desktop/HaphazardTasks/Open HaphazardTasks.desktop"
  chmod +x "$HOME/Desktop/HaphazardTasks/Open HaphazardTasks.desktop"
  gio set "$HOME/Desktop/HaphazardTasks/Open HaphazardTasks.desktop" metadata::trusted true 2>/dev/null || true
fi
echo "Installed. Open HaphazardTasks from the desktop icon or the app menu."
