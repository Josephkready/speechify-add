import json
import os
import tempfile
from pathlib import Path

CONFIG_DIR = Path.home() / ".config" / "speechify-add"
AUTH_FILE = CONFIG_DIR / "auth.json"
BROWSER_PROFILE_DIR = CONFIG_DIR / "browser-profile"


def load() -> dict:
    if not AUTH_FILE.exists():
        return {}
    try:
        return json.loads(AUTH_FILE.read_text())
    except (json.JSONDecodeError, OSError):
        return {}


def save(data: dict):
    CONFIG_DIR.mkdir(mode=0o700, parents=True, exist_ok=True)
    # Atomic write: create temp file with restricted permissions, then rename.
    # Avoids a TOCTOU race where auth.json is briefly world-readable.
    fd, temp_path = tempfile.mkstemp(dir=CONFIG_DIR, suffix=".tmp")
    try:
        with os.fdopen(fd, "w") as f:
            os.fchmod(f.fileno(), 0o600)
            json.dump(data, f, indent=2)
        os.replace(temp_path, AUTH_FILE)
    except BaseException:
        try:
            os.unlink(temp_path)
        except OSError:
            pass
        raise
