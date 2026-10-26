"""Start the server if it isn't running, then open the dashboard. Works on Linux and Windows."""
import os
import shutil
import subprocess
import sys
import time
import webbrowser
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

from app.run import DEFAULT_PORT, HOST, already_running  # noqa: E402

URL = f"http://{HOST}:{DEFAULT_PORT}/"


def start_server():
    python = sys.executable
    if os.name == "nt":
        pythonw = Path(python).with_name("pythonw.exe")
        if pythonw.exists():
            python = str(pythonw)
        flags = subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP
        subprocess.Popen([python, "-m", "app.run"], cwd=ROOT, creationflags=flags,
                         stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    else:
        subprocess.Popen([python, "-m", "app.run"], cwd=ROOT, start_new_session=True,
                         stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    for _ in range(50):
        time.sleep(0.2)
        if already_running():
            return True
    return False


def find_app_browser():
    """Chrome, Chromium or Edge, which can open the page as a standalone app window."""
    names = ["google-chrome", "google-chrome-stable", "chromium", "chromium-browser", "microsoft-edge",
             "msedge", "chrome"]
    for n in names:
        p = shutil.which(n)
        if p:
            return p
    if os.name == "nt":
        for base in (os.environ.get("PROGRAMFILES(X86)"), os.environ.get("PROGRAMFILES"),
                     os.environ.get("LOCALAPPDATA")):
            if not base:
                continue
            for rel in (r"Microsoft\Edge\Application\msedge.exe", r"Google\Chrome\Application\chrome.exe"):
                p = Path(base) / rel
                if p.exists():
                    return str(p)
    return None


def open_dashboard():
    browser = find_app_browser()
    if browser:
        try:
            subprocess.Popen([browser, f"--app={URL}"], stdin=subprocess.DEVNULL,
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            return
        except OSError:
            pass
    webbrowser.open(URL)


def main():
    if not already_running() and not start_server():
        print("The server did not start. See server.log in the data folder.")
        return 1
    open_dashboard()
    return 0


if __name__ == "__main__":
    sys.exit(main())
