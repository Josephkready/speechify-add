import json
import os
from pathlib import Path

# TODO: Respect XDG_CONFIG_HOME if set instead of hardcoding ~/.config
CONFIG_DIR = Path.home() / ".config" / "speechify-add"
AUTH_FILE = CONFIG_DIR / "auth.json"
BROWSER_PROFILE_DIR = CONFIG_DIR / "browser-profile"


def load() -> dict:
    if not AUTH_FILE.exists():
        return {}
    try:
        return json.loads(AUTH_FILE.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, ValueError):
        return {}


def save(data: dict):
    # TODO: Use atomic write (write to temp file + rename) to avoid corrupt
    # auth.json if the process is interrupted mid-write
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    AUTH_FILE.write_text(json.dumps(data, indent=2))
    os.chmod(AUTH_FILE, 0o600)
