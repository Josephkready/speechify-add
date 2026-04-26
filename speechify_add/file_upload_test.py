"""
Tests for file-upload feature: validation helpers and public API surface.

These are pure-logic tests — no browser, no network.
"""
from pathlib import Path

import pytest

import speechify_add
from speechify_add.browser import SUPPORTED_FILE_EXTS, _validate_file_path


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
