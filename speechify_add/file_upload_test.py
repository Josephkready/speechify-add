"""
Tests for file-upload feature: validation helpers, public API surface, and CLI.

These are pure-logic tests — no browser, no network.
"""
from pathlib import Path
from unittest.mock import patch

import pytest
from click.testing import CliRunner

import speechify_add
from speechify_add.browser import SUPPORTED_FILE_EXTS, _validate_file_path
from speechify_add.cli import cli


class TestValidateFilePath:
    def test_accepts_pdf(self, tmp_path):
        p = tmp_path / "a.pdf"
        p.write_bytes(b"%PDF-1.4\n")
        assert _validate_file_path(p) == p

    @pytest.mark.parametrize("ext", [".pdf", ".epub", ".html", ".htm", ".txt"])
    def test_accepts_each_supported_extension(self, tmp_path, ext):
        p = tmp_path / f"file{ext}"
        p.write_text("x")
        assert _validate_file_path(p) == p

    @pytest.mark.parametrize("ext", [".PDF", ".Epub", ".HTML"])
    def test_extension_check_is_case_insensitive(self, tmp_path, ext):
        p = tmp_path / f"file{ext}"
        p.write_text("x")
        assert _validate_file_path(p) == p

    def test_rejects_unsupported_extension(self, tmp_path):
        p = tmp_path / "a.docx"
        p.write_text("x")
        with pytest.raises(ValueError, match="Unsupported file type"):
            _validate_file_path(p)

    def test_error_lists_supported_extensions(self, tmp_path):
        p = tmp_path / "a.docx"
        p.write_text("x")
        with pytest.raises(ValueError) as exc_info:
            _validate_file_path(p)
        msg = str(exc_info.value)
        for ext in SUPPORTED_FILE_EXTS:
            assert ext in msg

    def test_rejects_missing_file(self, tmp_path):
        p = tmp_path / "missing.pdf"
        with pytest.raises(FileNotFoundError):
            _validate_file_path(p)

    def test_rejects_directory(self, tmp_path):
        d = tmp_path / "subdir.pdf"
        d.mkdir()
        with pytest.raises(ValueError, match="Not a file"):
            _validate_file_path(d)


class TestPublicAPISurface:
    def test_upload_text_is_exported(self):
        assert callable(speechify_add.upload_text)

    def test_upload_file_is_exported(self):
        assert callable(speechify_add.upload_file)

    def test_upload_url_is_exported(self):
        assert callable(speechify_add.upload_url)

    def test_all_lists_public_api(self):
        assert set(speechify_add.__all__) >= {
            "upload_text", "upload_file", "upload_url"
        }


class TestUploadFileAcceptsStrAndPath:
    """upload_file should accept either a str path or a Path object."""

    def test_rejects_missing_path_as_str(self, tmp_path):
        with pytest.raises(FileNotFoundError):
            speechify_add.upload_file(str(tmp_path / "nope.pdf"))

    def test_rejects_missing_path_as_path(self, tmp_path):
        with pytest.raises(FileNotFoundError):
            speechify_add.upload_file(tmp_path / "nope.pdf")

    def test_rejects_unsupported_extension(self, tmp_path):
        p = tmp_path / "a.docx"
        p.write_text("x")
        with pytest.raises(ValueError, match="Unsupported file type"):
            speechify_add.upload_file(p)


def _drive_coroutine(coro):
    """Run a non-awaiting mock coroutine to completion without touching asyncio.

    The CLI's `_run` is normally `asyncio.run`, which mutates the global event
    loop and interferes with other tests that use `asyncio.get_event_loop()`.
    For unit tests where `add_file` is mocked to a synchronous-bodied
    `async def`, we can drive the coroutine manually.
    """
    try:
        coro.send(None)
    except StopIteration as si:
        return si.value
    raise AssertionError("mock coroutine did not complete on first send")


class TestFileCLICommand:
    """CLI: speechify-add file <path> --title <title>."""

    def _make_pdf(self, tmp_path):
        p = tmp_path / "x.pdf"
        p.write_bytes(b"%PDF-1.4\n")
        return p

    def test_help_lists_file_command(self):
        result = CliRunner().invoke(cli, ["--help"])
        assert result.exit_code == 0
        assert "file" in result.output

    def test_file_help_describes_supported_types(self):
        result = CliRunner().invoke(cli, ["file", "--help"])
        assert result.exit_code == 0
        for ext in (".pdf", ".epub", ".html", ".txt"):
            assert ext in result.output

    def test_missing_path_exits_with_click_error(self, tmp_path):
        result = CliRunner().invoke(cli, ["file", str(tmp_path / "nope.pdf")])
        assert result.exit_code != 0
        assert "does not exist" in result.output.lower()

    def test_directory_path_rejected_by_click(self, tmp_path):
        d = tmp_path / "sub"
        d.mkdir()
        result = CliRunner().invoke(cli, ["file", str(d)])
        assert result.exit_code != 0

    def test_happy_path_prints_returned_url(self, tmp_path):
        pdf = self._make_pdf(tmp_path)
        fake_url = "https://app.speechify.com/item/abc-123"

        async def fake_add_file(path, title=""):
            return fake_url

        with patch("speechify_add.browser.add_file", side_effect=fake_add_file), \
             patch("speechify_add.cli._run", _drive_coroutine):
            result = CliRunner().invoke(cli, ["file", str(pdf)])

        assert result.exit_code == 0
        assert fake_url in result.output

    def test_title_flag_forwarded_to_add_file(self, tmp_path):
        pdf = self._make_pdf(tmp_path)
        captured = {}

        async def fake_add_file(path, title=""):
            captured["path"] = path
            captured["title"] = title
            return "https://app.speechify.com/item/uuid"

        with patch("speechify_add.browser.add_file", side_effect=fake_add_file), \
             patch("speechify_add.cli._run", _drive_coroutine):
            result = CliRunner().invoke(
                cli, ["file", str(pdf), "-t", "My Title"]
            )

        assert result.exit_code == 0
        assert captured["title"] == "My Title"
        assert Path(captured["path"]) == pdf

    def test_browser_error_exits_1_with_stderr(self, tmp_path):
        pdf = self._make_pdf(tmp_path)

        async def fake_add_file(path, title=""):
            raise RuntimeError("Speechify said no")

        with patch("speechify_add.browser.add_file", side_effect=fake_add_file), \
             patch("speechify_add.cli._run", _drive_coroutine):
            result = CliRunner().invoke(cli, ["file", str(pdf)])

        assert result.exit_code == 1
        assert "Error: Speechify said no" in result.output
