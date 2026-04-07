"""Tests for speechify_add/config.py — load() and save()."""
import json
import os
import stat
from pathlib import Path
from unittest.mock import patch

import pytest

import speechify_add.config as config


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _patch_paths(tmp_path):
    """Return a context-manager stack that redirects CONFIG_DIR and AUTH_FILE."""
    config_dir = tmp_path / ".config" / "speechify-add"
    auth_file = config_dir / "auth.json"
    return config_dir, auth_file


# ---------------------------------------------------------------------------
# Unit tests — load()
# ---------------------------------------------------------------------------

class TestLoad:
    def test_returns_empty_dict_when_file_missing(self, tmp_path):
        config_dir, auth_file = _patch_paths(tmp_path)
        with patch.object(config, "AUTH_FILE", auth_file):
            result = config.load()
        assert result == {}

    def test_returns_parsed_dict_for_valid_json(self, tmp_path):
        config_dir, auth_file = _patch_paths(tmp_path)
        config_dir.mkdir(parents=True)
        auth_file.write_text(json.dumps({"token": "abc123", "uid": "u1"}))
        with patch.object(config, "AUTH_FILE", auth_file):
            result = config.load()
        assert result == {"token": "abc123", "uid": "u1"}

    def test_returns_empty_dict_for_invalid_json(self, tmp_path):
        config_dir, auth_file = _patch_paths(tmp_path)
        config_dir.mkdir(parents=True)
        auth_file.write_text("{not valid json")
        with patch.object(config, "AUTH_FILE", auth_file):
            result = config.load()
        assert result == {}

    def test_returns_empty_dict_on_read_oserror(self, tmp_path):
        config_dir, auth_file = _patch_paths(tmp_path)
        config_dir.mkdir(parents=True)
        auth_file.write_text(json.dumps({"k": "v"}))
        # Make file unreadable
        auth_file.chmod(0o000)
        try:
            with patch.object(config, "AUTH_FILE", auth_file):
                result = config.load()
            assert result == {}
        finally:
            auth_file.chmod(0o644)

    def test_returns_empty_dict_for_empty_file(self, tmp_path):
        config_dir, auth_file = _patch_paths(tmp_path)
        config_dir.mkdir(parents=True)
        auth_file.write_text("")
        with patch.object(config, "AUTH_FILE", auth_file):
            result = config.load()
        assert result == {}

    @pytest.mark.parametrize("data", [
        {},
        {"token": "x"},
        {"nested": {"a": 1}},
        {"list": [1, 2, 3]},
    ])
    def test_load_roundtrip_various_shapes(self, tmp_path, data):
        config_dir, auth_file = _patch_paths(tmp_path)
        config_dir.mkdir(parents=True)
        auth_file.write_text(json.dumps(data))
        with patch.object(config, "AUTH_FILE", auth_file):
            result = config.load()
        assert result == data


# ---------------------------------------------------------------------------
# Unit tests — save()
# ---------------------------------------------------------------------------

class TestSave:
    def test_creates_config_dir_if_missing(self, tmp_path):
        config_dir, auth_file = _patch_paths(tmp_path)
        assert not config_dir.exists()
        with patch.object(config, "CONFIG_DIR", config_dir), \
             patch.object(config, "AUTH_FILE", auth_file):
            config.save({"token": "t"})
        assert config_dir.exists()

    def test_auth_file_contains_correct_json(self, tmp_path):
        config_dir, auth_file = _patch_paths(tmp_path)
        data = {"token": "mytoken", "uid": "user42"}
        with patch.object(config, "CONFIG_DIR", config_dir), \
             patch.object(config, "AUTH_FILE", auth_file):
            config.save(data)
        assert json.loads(auth_file.read_text()) == data

    def test_auth_file_permissions_are_0o600(self, tmp_path):
        config_dir, auth_file = _patch_paths(tmp_path)
        with patch.object(config, "CONFIG_DIR", config_dir), \
             patch.object(config, "AUTH_FILE", auth_file):
            config.save({"x": 1})
        file_mode = stat.S_IMODE(auth_file.stat().st_mode)
        assert file_mode == 0o600

    def test_no_temp_file_left_after_success(self, tmp_path):
        config_dir, auth_file = _patch_paths(tmp_path)
        with patch.object(config, "CONFIG_DIR", config_dir), \
             patch.object(config, "AUTH_FILE", auth_file):
            config.save({"k": "v"})
        tmp_files = list(config_dir.glob("*.tmp"))
        assert tmp_files == [], f"Temp files remain: {tmp_files}"

    def test_temp_file_cleaned_up_on_json_error(self, tmp_path):
        """If json.dump raises, the temp file should be deleted."""
        config_dir, auth_file = _patch_paths(tmp_path)

        class Unserializable:
            pass

        with patch.object(config, "CONFIG_DIR", config_dir), \
             patch.object(config, "AUTH_FILE", auth_file):
            with pytest.raises(TypeError):
                config.save({"bad": Unserializable()})

        tmp_files = list(config_dir.glob("*.tmp"))
        assert tmp_files == [], f"Temp file not cleaned up: {tmp_files}"

    def test_overwrite_existing_auth_file(self, tmp_path):
        config_dir, auth_file = _patch_paths(tmp_path)
        config_dir.mkdir(parents=True)
        auth_file.write_text(json.dumps({"old": "data"}))
        with patch.object(config, "CONFIG_DIR", config_dir), \
             patch.object(config, "AUTH_FILE", auth_file):
            config.save({"new": "data"})
        assert json.loads(auth_file.read_text()) == {"new": "data"}


# ---------------------------------------------------------------------------
# Integration tests
# ---------------------------------------------------------------------------

class TestIntegration:
    def test_integration_save_then_load_roundtrip(self, tmp_path):
        """save() followed by load() returns the same data."""
        config_dir, auth_file = _patch_paths(tmp_path)
        original = {"firebase_token": "tok", "uid": "uid123", "email": "a@b.com"}
        with patch.object(config, "CONFIG_DIR", config_dir), \
             patch.object(config, "AUTH_FILE", auth_file):
            config.save(original)
            result = config.load()
        assert result == original

    def test_integration_load_before_any_save(self, tmp_path):
        """load() on a fresh directory (no auth file) returns empty dict."""
        _, auth_file = _patch_paths(tmp_path)
        with patch.object(config, "AUTH_FILE", auth_file):
            assert config.load() == {}

    def test_integration_save_twice_last_wins(self, tmp_path):
        """Two saves in sequence: only the last persisted value is loaded."""
        config_dir, auth_file = _patch_paths(tmp_path)
        with patch.object(config, "CONFIG_DIR", config_dir), \
             patch.object(config, "AUTH_FILE", auth_file):
            config.save({"token": "first"})
            config.save({"token": "second"})
            result = config.load()
        assert result["token"] == "second"

    def test_integration_idempotent_save(self, tmp_path):
        """Saving the same data twice results in the same content."""
        config_dir, auth_file = _patch_paths(tmp_path)
        data = {"token": "stable"}
        with patch.object(config, "CONFIG_DIR", config_dir), \
             patch.object(config, "AUTH_FILE", auth_file):
            config.save(data)
            config.save(data)
            result = config.load()
        assert result == data

    def test_integration_corrupted_file_then_save_recovers(self, tmp_path):
        """After a corrupted auth file, save() overwrites it and load() succeeds."""
        config_dir, auth_file = _patch_paths(tmp_path)
        config_dir.mkdir(parents=True)
        auth_file.write_text("{corrupted json{{")
        with patch.object(config, "CONFIG_DIR", config_dir), \
             patch.object(config, "AUTH_FILE", auth_file):
            # load returns {} due to corruption
            assert config.load() == {}
            # save fresh data
            config.save({"token": "fresh"})
            # now load correctly
            assert config.load() == {"token": "fresh"}
