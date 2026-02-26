import json
import os
from pathlib import Path

CONFIG_DIR = Path.home() / ".config" / "speechify-add"
AUTH_FILE = CONFIG_DIR / "auth.json"
BROWSER_PROFILE_DIR = CONFIG_DIR / "browser-profile"


def load() -> dict:
    if not AUTH_FILE.exists():
        return {}
    return json.loads(AUTH_FILE.read_text())


def save(data: dict):
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    AUTH_FILE.write_text(json.dumps(data, indent=2))
    os.chmod(AUTH_FILE, 0o600)
