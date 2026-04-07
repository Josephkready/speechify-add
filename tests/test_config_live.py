"""Live tests for speechify_add/config.py.

These tests read/write the real config directory (~/.config/speechify-add).
They do NOT send any network requests or hit external APIs.

Run with: pytest -m live tests/test_config_live.py
"""
import json
import os
import stat
from pathlib import Path

import pytest

import speechify_add.config as config


# ---------------------------------------------------------------------------
# Live tests — real filesystem, real config dir
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module")
def backup_and_restore():
    """Back up and restore the real auth.json around live tests."""
    original_existed = config.AUTH_FILE.exists()
    original_content = config.AUTH_FILE.read_bytes() if original_existed else None
    yield
    if original_content is not None:
        config.CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        config.AUTH_FILE.write_bytes(original_content)
    elif config.AUTH_FILE.exists():
        config.AUTH_FILE.unlink()


@pytest.mark.live
def test_live_load_returns_dict_or_empty(backup_and_restore):
    """Reads real auth.json (if present) and confirms load() returns a dict.

    External services: none (local filesystem only).
    Cost/time: negligible.
    Requirements: none.
    """
    result = config.load()
    assert isinstance(result, dict)


@pytest.mark.live
def test_live_save_and_load_roundtrip(backup_and_restore):
    """Writes a sentinel value to the real config dir and reads it back.

    External services: none (local filesystem only).
    Cost/time: negligible.
    Requirements: write access to ~/.config/speechify-add/.
    """
    sentinel = {"_live_test": True, "token": "live-test-sentinel"}
    config.save(sentinel)
    result = config.load()
    assert result == sentinel


@pytest.mark.live
def test_live_auth_file_has_restricted_permissions(backup_and_restore):
    """Confirms the real auth.json is written with 0o600 permissions.

    External services: none (local filesystem only).
    Cost/time: negligible.
    Requirements: write access to ~/.config/speechify-add/.
    """
    config.save({"_live_test": True})
    file_mode = stat.S_IMODE(config.AUTH_FILE.stat().st_mode)
    assert file_mode == 0o600, f"Expected 0o600, got {oct(file_mode)}"
