"""Start the HaphazardTasks server on 127.0.0.1 only."""
import json
import os
import socket
import sys
import urllib.request

from waitress import serve

from .server import APP_NAME, DEFAULT_PORT, create_app, start_background

HOST = "127.0.0.1"  # never configurable: the app must not be reachable from the network


def already_running(port=DEFAULT_PORT):
    """True if HaphazardTasks already answers on the port; raises if something else holds it."""
    with socket.socket() as s:
        s.settimeout(0.5)
        if s.connect_ex((HOST, port)) != 0:
            return False
    try:
        req = urllib.request.Request(f"http://{HOST}:{port}/api/status")
        # Never route a loopback check through a (corporate) proxy.
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        with opener.open(req, timeout=2) as r:
            if json.load(r).get("app") == APP_NAME:
                return True
    except (OSError, ValueError):
        pass
    raise SystemExit(f"Port {port} is in use by another program. Close it and try again.")


def main():
    # pythonw.exe on Windows has no console: give stray writes somewhere harmless to go.
    for name in ("stdout", "stderr"):
        if getattr(sys, name) is None:
            setattr(sys, name, open(os.devnull, "w"))
    if already_running():
        print(f"{APP_NAME} is already running at http://{HOST}:{DEFAULT_PORT}/")
        return 0
    app = create_app()
    start_background(app)
    print(f"{APP_NAME} listening on http://{HOST}:{DEFAULT_PORT}/ (local only)")
    serve(app, host=HOST, port=DEFAULT_PORT, threads=6, ident=None)
    return 0


if __name__ == "__main__":
    sys.exit(main())
