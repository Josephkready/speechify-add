"""Tests for speechify_add.config — load/save auth JSON with secure permissions."""

import tests.conftest  # noqa: F401 — mock third-party deps

import json
import os
import stat
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from speechify_add import config


class TestLoad(unittest.TestCase):
    """config.load() reads auth.json and handles edge cases."""

    def test_returns_empty_dict_when_file_missing(self):
        with tempfile.TemporaryDirectory() as d:
            fake_path = Path(d) / "nonexistent.json"
            with patch.object(config, "AUTH_FILE", fake_path):
                self.assertEqual(config.load(), {})

    def test_reads_valid_json(self):
        with tempfile.TemporaryDirectory() as d:
            f = Path(d) / "auth.json"
            f.write_text(json.dumps({"refresh_token": "abc"}))
            with patch.object(config, "AUTH_FILE", f):
                result = config.load()
        self.assertEqual(result, {"refresh_token": "abc"})

    def test_returns_empty_dict_on_corrupt_json(self):
        with tempfile.TemporaryDirectory() as d:
            f = Path(d) / "auth.json"
            f.write_text("{bad json")
            with patch.object(config, "AUTH_FILE", f):
                result = config.load()
        self.assertEqual(result, {})

    def test_returns_empty_dict_on_empty_file(self):
        with tempfile.TemporaryDirectory() as d:
            f = Path(d) / "auth.json"
            f.write_text("")
            with patch.object(config, "AUTH_FILE", f):
                result = config.load()
        self.assertEqual(result, {})


class TestSave(unittest.TestCase):
    """config.save() writes JSON with 0o600 permissions."""

    def test_creates_dir_and_writes_json(self):
        with tempfile.TemporaryDirectory() as d:
            config_dir = Path(d) / "sub"
            auth_file = config_dir / "auth.json"
            with (
                patch.object(config, "CONFIG_DIR", config_dir),
                patch.object(config, "AUTH_FILE", auth_file),
            ):
                config.save({"key": "val"})

            self.assertTrue(auth_file.exists())
            self.assertEqual(json.loads(auth_file.read_text()), {"key": "val"})

    def test_file_permissions_are_600(self):
        with tempfile.TemporaryDirectory() as d:
            config_dir = Path(d)
            auth_file = config_dir / "auth.json"
            with (
                patch.object(config, "CONFIG_DIR", config_dir),
                patch.object(config, "AUTH_FILE", auth_file),
            ):
                config.save({"x": 1})

            mode = stat.S_IMODE(os.stat(auth_file).st_mode)
            self.assertEqual(mode, 0o600)

    def test_overwrites_existing_file(self):
        with tempfile.TemporaryDirectory() as d:
            config_dir = Path(d)
            auth_file = config_dir / "auth.json"
            auth_file.write_text(json.dumps({"old": True}))
            with (
                patch.object(config, "CONFIG_DIR", config_dir),
                patch.object(config, "AUTH_FILE", auth_file),
            ):
                config.save({"new": True})

            self.assertEqual(json.loads(auth_file.read_text()), {"new": True})


if __name__ == "__main__":
    unittest.main()
