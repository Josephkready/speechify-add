"""
Pure-logic unit tests — no network, no subprocess, no browser.
"""
import base64
import json
import os
import stat
from unittest.mock import patch

import click
import pytest

from speechify_add.api import _user_id_from_token
from speechify_add.cli import (
    _parse_item_id, _is_google_doc, _google_doc_export_url,
    _collect_urls, _collect_text, _title_from_text,
)
from speechify_add import config as speechify_config


# ---------------------------------------------------------------------------
# JWT helper
# ---------------------------------------------------------------------------

def _make_jwt(payload: dict) -> str:
    def b64(d):
        return base64.urlsafe_b64encode(json.dumps(d).encode()).rstrip(b"=").decode()
    return f"{b64({'alg': 'RS256'})}.{b64(payload)}.fakesig"


# ---------------------------------------------------------------------------
# 1. _user_id_from_token
# ---------------------------------------------------------------------------


class TestUserIdFromToken:
    def test_valid_jwt_user_id_key(self):
        token = _make_jwt({"user_id": "uid-abc123", "sub": "sub-xyz"})
        assert _user_id_from_token(token) == "uid-abc123"

    def test_valid_jwt_sub_fallback(self):
        token = _make_jwt({"sub": "sub-only-user"})
        assert _user_id_from_token(token) == "sub-only-user"

    def test_malformed_token_wrong_segments(self):
        with pytest.raises(RuntimeError):
            _user_id_from_token("not.a.valid.jwt.token.withtoomanysegments.extra")

    def test_malformed_token_only_one_segment(self):
        with pytest.raises(RuntimeError):
            _user_id_from_token("onlyone")

    def test_missing_user_id_and_sub(self):
        token = _make_jwt({"iss": "https://securetoken.google.com/proj", "aud": "proj"})
        with pytest.raises(RuntimeError):
            _user_id_from_token(token)


# ---------------------------------------------------------------------------
# 2. _parse_item_id
# ---------------------------------------------------------------------------

_SAMPLE_UUID = "783247eb-59c9-4ade-9027-e01f8d77d959"


class TestParseItemId:
    def test_bare_uuid(self):
        assert _parse_item_id(_SAMPLE_UUID) == _SAMPLE_UUID

    def test_full_speechify_url(self):
        url = f"https://app.speechify.com/item/{_SAMPLE_UUID}"
        assert _parse_item_id(url) == _SAMPLE_UUID

    def test_uppercase_uuid(self):
        upper = _SAMPLE_UUID.upper()
        result = _parse_item_id(upper)
        assert result.lower() == _SAMPLE_UUID

    def test_invalid_string_raises(self):
        with pytest.raises(click.BadParameter):
            _parse_item_id("not-a-uuid-at-all")


# ---------------------------------------------------------------------------
# 3. _is_google_doc and _google_doc_export_url
# ---------------------------------------------------------------------------

_GDOC_URL = "https://docs.google.com/document/d/1BxiMVs0XRA5nFMdKvBdBZjgmUUqptlbs74OgVE2upms/edit"
_GDOC_ID = "1BxiMVs0XRA5nFMdKvBdBZjgmUUqptlbs74OgVE2upms"


class TestGoogleDoc:
    def test_valid_google_docs_url_is_google_doc(self):
        assert _is_google_doc(_GDOC_URL) is True

    def test_valid_google_docs_export_url(self):
        export = _google_doc_export_url(_GDOC_URL)
        assert export == f"https://docs.google.com/document/d/{_GDOC_ID}/export?format=txt"

    def test_non_google_url_is_not_google_doc(self):
        assert _is_google_doc("https://example.com/some-article") is False

    def test_non_google_url_raises_on_export(self):
        with pytest.raises(ValueError):
            _google_doc_export_url("https://example.com/not-a-doc")


# ---------------------------------------------------------------------------
# 4. _collect_urls
# ---------------------------------------------------------------------------

class TestCollectUrls:
    def test_single_url_argument(self):
        result = _collect_urls("https://example.com/article", None, False)
        assert result == ["https://example.com/article"]

    def test_no_args_returns_empty(self):
        result = _collect_urls(None, None, False)
        assert result == []


# ---------------------------------------------------------------------------
# 5. _title_from_text
# ---------------------------------------------------------------------------

class TestTitleFromText:
    def test_extracts_first_non_empty_line(self):
        assert _title_from_text("\n\n  Hello World  \nmore") == "Hello World"

    def test_truncates_at_120_chars(self):
        long_line = "A" * 200
        assert len(_title_from_text(long_line)) == 120

    def test_returns_empty_for_blank_text(self):
        assert _title_from_text("") == ""
        assert _title_from_text("\n\n  \n") == ""


# ---------------------------------------------------------------------------
# 6. _collect_text
# ---------------------------------------------------------------------------

class TestCollectText:
    def test_reads_from_file(self, tmp_path):
        f = tmp_path / "input.txt"
        f.write_text("hello world")
        assert _collect_text(str(f), False) == "hello world"

    def test_returns_empty_when_no_source(self):
        assert _collect_text(None, False) == ""


# ---------------------------------------------------------------------------
# 7. _collect_urls with file
# ---------------------------------------------------------------------------

class TestCollectUrlsFile:
    def test_reads_file_skips_comments_and_blanks(self, tmp_path):
        f = tmp_path / "urls.txt"
        f.write_text("# comment\nhttps://a.com\n\nhttps://b.com\n# another\n")
        result = _collect_urls(None, str(f), False)
        assert result == ["https://a.com", "https://b.com"]


# ---------------------------------------------------------------------------
# 8. config.load() and config.save()
# ---------------------------------------------------------------------------

class TestConfigLoad:
    def test_returns_empty_dict_when_auth_file_missing(self, tmp_path):
        fake_auth_file = tmp_path / "auth.json"
        # Patch AUTH_FILE to a path that does not exist
        with patch.object(speechify_config, "AUTH_FILE", fake_auth_file):
            result = speechify_config.load()
        assert result == {}

    def test_returns_empty_dict_on_corrupt_json(self, tmp_path):
        fake_auth_file = tmp_path / "auth.json"
        fake_auth_file.write_text("NOT VALID JSON{{{")
        with patch.object(speechify_config, "AUTH_FILE", fake_auth_file):
            result = speechify_config.load()
        assert result == {}


class TestConfigSave:
    def test_creates_file_with_restricted_permissions(self, tmp_path):
        fake_config_dir = tmp_path / "cfg"
        fake_auth_file = fake_config_dir / "auth.json"
        with (
            patch.object(speechify_config, "CONFIG_DIR", fake_config_dir),
            patch.object(speechify_config, "AUTH_FILE", fake_auth_file),
        ):
            speechify_config.save({"token": "secret"})
        assert fake_auth_file.exists()
        mode = stat.S_IMODE(os.stat(fake_auth_file).st_mode)
        assert mode == 0o600
        data = json.loads(fake_auth_file.read_text())
        assert data == {"token": "secret"}

    def test_atomic_overwrite(self, tmp_path):
        fake_config_dir = tmp_path / "cfg"
        fake_config_dir.mkdir()
        fake_auth_file = fake_config_dir / "auth.json"
        fake_auth_file.write_text('{"old": true}')
        with (
            patch.object(speechify_config, "CONFIG_DIR", fake_config_dir),
            patch.object(speechify_config, "AUTH_FILE", fake_auth_file),
        ):
            speechify_config.save({"new": True})
        data = json.loads(fake_auth_file.read_text())
        assert data == {"new": True}
