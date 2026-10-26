"""Build the portable Windows package from Linux (or anywhere): no installer, no admin rights.

    python packaging/build_windows.py

Produces dist/HaphazardTasks-windows-x64-<version>.zip containing:
    HaphazardTasks/
      Start HaphazardTasks.bat        double-click to run
      Create desktop shortcut.bat     optional
      README-WINDOWS.txt
      launch.py, app/                 the application
      python/                         Python embeddable runtime + pinned, hash-checked dependencies
"""
import hashlib
import shutil
import subprocess
import sys
import urllib.request
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from app import __version__  # noqa: E402

PY_VERSION = "3.14.4"
PY_TAG = "314"
EMBED_URL = f"https://www.python.org/ftp/python/{PY_VERSION}/python-{PY_VERSION}-embed-amd64.zip"
# Matches the sha256_sum python.org publishes for this file (checked 2026-10-26 via
# https://www.python.org/api/v2/downloads/release_file/?release=1101). A mismatch means different bytes: stop.
EMBED_SHA256 = "cda80a9b1e75c0f1b4f9872ca1b417f0d19bce32facc811aea9180e70fad5fb9"
CACHE = ROOT / "packaging" / ".cache"
DIST = ROOT / "dist"
STAGE = DIST / "stage" / "HaphazardTasks"


def sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def fetch_embed():
    CACHE.mkdir(parents=True, exist_ok=True)
    target = CACHE / Path(EMBED_URL).name
    if not target.exists():
        print("Downloading", EMBED_URL)
        urllib.request.urlretrieve(EMBED_URL, target)
    digest = sha256(target)
    if EMBED_SHA256 == "PIN_ON_FIRST_BUILD":
        src = Path(__file__).read_text()
        Path(__file__).write_text(src.replace('EMBED_SHA256 = "cda80a9b1e75c0f1b4f9872ca1b417f0d19bce32facc811aea9180e70fad5fb9"', f'EMBED_SHA256 = "{digest}"'))
        print("Pinned embeddable Python SHA-256:", digest)
    elif digest != EMBED_SHA256:
        raise SystemExit(f"Embeddable Python hash mismatch: got {digest}, expected {EMBED_SHA256}")
    return target


def main():
    if STAGE.parent.exists():
        shutil.rmtree(STAGE.parent)
    STAGE.mkdir(parents=True)

    # 1. Application files (no tests, no venv, no caches).
    shutil.copytree(ROOT / "app", STAGE / "app", ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
    shutil.copy2(ROOT / "launch.py", STAGE)
    for name in ("Start HaphazardTasks.bat", "Create desktop shortcut.bat"):
        shutil.copy2(ROOT / "packaging" / "windows" / name, STAGE / name)
    shutil.copy2(ROOT / "packaging" / "windows" / "README-WINDOWS.txt", STAGE)

    # 2. Embeddable Python, with a path file that finds site-packages and the app.
    py = STAGE / "python"
    with zipfile.ZipFile(fetch_embed()) as z:
        z.extractall(py)
    (py / f"python{PY_TAG}._pth").write_text(
        f"python{PY_TAG}.zip\n.\nLib\\site-packages\n..\nimport site\n", newline="\r\n")

    # 3. Dependencies: Windows wheels only, every file checked against requirements.lock.
    site = py / "Lib" / "site-packages"
    subprocess.run([sys.executable, "-m", "pip", "install", "--quiet", "--no-deps", "--require-hashes",
                    "--only-binary=:all:", "--platform", "win_amd64", "--python-version", PY_VERSION,
                    "--implementation", "cp", "--target", str(site), "-r", str(ROOT / "requirements.lock")],
                   check=True)

    # 4. Zip it.
    DIST.mkdir(exist_ok=True)
    out = DIST / f"HaphazardTasks-windows-x64-{__version__}.zip"
    if out.exists():
        out.unlink()
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as z:
        for f in sorted(STAGE.rglob("*")):
            if f.is_file():
                z.write(f, f.relative_to(STAGE.parent))
    print(f"Built {out} ({out.stat().st_size / 1e6:.1f} MB), SHA-256 {sha256(out)}")


if __name__ == "__main__":
    main()
