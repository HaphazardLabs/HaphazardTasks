#!/usr/bin/env bash
# Start HaphazardTasks (if needed) and open the dashboard.
cd "$(dirname "$(readlink -f "$0")")" || exit 1
exec ./.venv/bin/python launch.py
