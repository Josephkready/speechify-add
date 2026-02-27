"""Tests for speechify_add.cli — URL collection, command routing."""

import tests.conftest  # noqa: F401 — mock third-party deps

import asyncio
import sys
import tempfile
import unittest
from io import StringIO
from pathlib import Path
from unittest.mock import AsyncMock, patch

from speechify_add.cli import _collect_urls, _run, _add_one


class TestCollectUrls(unittest.TestCase):
    """_collect_urls gathers URLs from args, files, or stdin."""

    def test_single_url_argument(self):
        result = _collect_urls("http://example.com", None, False)
        self.assertEqual(result, ["http://example.com"])

    def test_from_file(self):
        with tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False) as f:
            f.write("http://one.com\n")
            f.write("# this is a comment\n")
            f.write("http://two.com\n")
            f.write("\n")  # blank line
            name = f.name
        try:
            result = _collect_urls(None, name, False)
        finally:
            Path(name).unlink()

        self.assertEqual(result, ["http://one.com", "http://two.com"])

    def test_from_file_strips_whitespace(self):
        with tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False) as f:
            f.write("  http://padded.com  \n")
            name = f.name
        try:
            result = _collect_urls(None, name, False)
        finally:
            Path(name).unlink()

        self.assertEqual(result, ["http://padded.com"])

    def test_from_file_ignores_indented_comments(self):
        with tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False) as f:
            f.write("  # indented comment\n")
            f.write("http://valid.com\n")
            name = f.name
        try:
            result = _collect_urls(None, name, False)
        finally:
            Path(name).unlink()

        self.assertEqual(result, ["http://valid.com"])

    def test_from_stdin(self):
        fake_stdin = StringIO("http://stdin1.com\nhttp://stdin2.com\n")
        with patch.object(sys, "stdin", fake_stdin):
            result = _collect_urls(None, None, True)
        self.assertEqual(result, ["http://stdin1.com", "http://stdin2.com"])

    def test_stdin_skips_blank_lines(self):
        fake_stdin = StringIO("http://a.com\n\n\nhttp://b.com\n")
        with patch.object(sys, "stdin", fake_stdin):
            result = _collect_urls(None, None, True)
        self.assertEqual(result, ["http://a.com", "http://b.com"])

    def test_returns_empty_when_no_input(self):
        result = _collect_urls(None, None, False)
        self.assertEqual(result, [])


class TestAddOne(unittest.TestCase):
    """_add_one routes to the correct backend."""

    def test_api_mode_calls_api(self):
        mock_api = AsyncMock()
        with patch("speechify_add.api.add_url", mock_api):
            asyncio.get_event_loop().run_until_complete(
                _add_one("http://test.com", "api")
            )
        mock_api.assert_awaited_once_with("http://test.com")

    def test_browser_mode_calls_browser(self):
        mock_browser = AsyncMock()
        with patch("speechify_add.browser.add_url", mock_browser):
            asyncio.get_event_loop().run_until_complete(
                _add_one("http://test.com", "browser")
            )
        mock_browser.assert_awaited_once_with("http://test.com")


class TestRun(unittest.TestCase):
    """_run helper executes coroutines."""

    def test_runs_coroutine(self):
        async def identity():
            return 42

        self.assertEqual(_run(identity()), 42)

    def test_propagates_exceptions(self):
        async def fail():
            raise ValueError("boom")

        with self.assertRaises(ValueError):
            _run(fail())


if __name__ == "__main__":
    unittest.main()
