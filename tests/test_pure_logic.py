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
from speechify_add.auth import _extract_firebase_tokens
from speechify_add.cli import (
    _parse_item_id, _is_google_doc, _google_doc_export_url,
    _collect_urls, _collect_text, _extract_title_from_text,
)
from speechify_add.verify import parse_progress_pct
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

    def test_from_file(self, tmp_path):
        url_file = tmp_path / "urls.txt"
        url_file.write_text(
            "https://example.com/a\n"
            "# this is a comment\n"
            "\n"
            "https://example.com/b\n"
        )
        result = _collect_urls(None, str(url_file), False)
        assert result == ["https://example.com/a", "https://example.com/b"]

    def test_from_file_skips_blank_lines(self, tmp_path):
        url_file = tmp_path / "urls.txt"
        url_file.write_text("\n\n  \n")
        result = _collect_urls(None, str(url_file), False)
        assert result == []


# ---------------------------------------------------------------------------
# 5. _extract_title_from_text
# ---------------------------------------------------------------------------

class TestExtractTitleFromText:
    def test_title_from_first_line(self):
        assert _extract_title_from_text("My Title\nBody text") == "My Title"

    def test_skips_empty_lines(self):
        assert _extract_title_from_text("\n\n  \nActual Title\nBody") == "Actual Title"

    def test_empty_string(self):
        assert _extract_title_from_text("") == ""

    def test_whitespace_only(self):
        assert _extract_title_from_text("   \n  \n  ") == ""

    def test_truncates_at_120_chars(self):
        long_line = "A" * 200
        result = _extract_title_from_text(long_line)
        assert len(result) == 120
        assert result == "A" * 120


# ---------------------------------------------------------------------------
# 6. _collect_text
# ---------------------------------------------------------------------------

class TestCollectText:
    def test_from_file(self, tmp_path):
        text_file = tmp_path / "content.md"
        text_file.write_text("Hello world")
        result = _collect_text(str(text_file), False)
        assert result == "Hello world"

    def test_no_args_returns_empty(self):
        result = _collect_text(None, False)
        assert result == ""


# ---------------------------------------------------------------------------
# 7. config.load() and config.save()
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

    def test_save_and_load_round_trip(self, tmp_path):
        fake_auth_file = tmp_path / "auth.json"
        fake_config_dir = tmp_path
        data = {"firebase_api_key": "test-key", "refresh_token": "test-token"}
        with patch.object(speechify_config, "AUTH_FILE", fake_auth_file), \
             patch.object(speechify_config, "CONFIG_DIR", fake_config_dir):
            speechify_config.save(data)
            result = speechify_config.load()
        assert result == data

    def test_save_sets_file_permissions(self, tmp_path):
        fake_auth_file = tmp_path / "auth.json"
        fake_config_dir = tmp_path
        with patch.object(speechify_config, "AUTH_FILE", fake_auth_file), \
             patch.object(speechify_config, "CONFIG_DIR", fake_config_dir):
            speechify_config.save({"key": "val"})
        mode = stat.S_IMODE(os.stat(fake_auth_file).st_mode)
        assert mode == 0o600

    def test_atomic_overwrite(self, tmp_path):
        fake_config_dir = tmp_path / "cfg"
        fake_config_dir.mkdir()
        fake_auth_file = fake_config_dir / "auth.json"
        fake_auth_file.write_text('{"old": true}')
        with patch.object(speechify_config, "CONFIG_DIR", fake_config_dir), \
             patch.object(speechify_config, "AUTH_FILE", fake_auth_file):
            speechify_config.save({"new": True})
        data = json.loads(fake_auth_file.read_text())
        assert data == {"new": True}


# ---------------------------------------------------------------------------
# 8. parse_progress_pct
# ---------------------------------------------------------------------------

class TestParseProgressPct:
    def test_normal_percentage(self):
        assert parse_progress_pct("73% · web") == 73

    def test_zero_percent(self):
        assert parse_progress_pct("0% · pdf") == 0

    def test_hundred_percent(self):
        assert parse_progress_pct("100% · txt") == 100

    def test_no_percentage_returns_none(self):
        assert parse_progress_pct("web") is None

    def test_empty_string_returns_none(self):
        assert parse_progress_pct("") is None

    def test_percentage_only(self):
        assert parse_progress_pct("42%") == 42

    def test_multiple_percentages_takes_first(self):
        # re.search finds the first match
        assert parse_progress_pct("10% · 90%") == 10


# ---------------------------------------------------------------------------
# 9. _extract_firebase_tokens
# ---------------------------------------------------------------------------

class TestExtractFirebaseTokens:
    def test_extracts_api_key_and_tokens(self):
        records = [{
            "value": {
                "apiKey": "AIzaSy-test-key",
                "stsTokenManager": {
                    "refreshToken": "refresh-tok-abc",
                    "accessToken": "id-tok-xyz",
                    "expirationTime": 1700000000000,
                },
            }
        }]
        captured: dict = {}
        _extract_firebase_tokens(records, captured)
        assert captured["firebase_api_key"] == "AIzaSy-test-key"
        assert captured["refresh_token"] == "refresh-tok-abc"
        assert captured["id_token"] == "id-tok-xyz"
        assert captured["id_token_expires_at"] == 1700000000.0

    def test_json_string_value_is_parsed(self):
        value = json.dumps({
            "apiKey": "key-from-string",
            "stsTokenManager": {"refreshToken": "rt-string"},
        })
        records = [{"value": value}]
        captured: dict = {}
        _extract_firebase_tokens(records, captured)
        assert captured["firebase_api_key"] == "key-from-string"
        assert captured["refresh_token"] == "rt-string"

    def test_top_level_refresh_token_fallback(self):
        records = [{"value": {"refreshToken": "top-level-rt"}}]
        captured: dict = {}
        _extract_firebase_tokens(records, captured)
        assert captured["refresh_token"] == "top-level-rt"

    def test_empty_records_produces_empty_captured(self):
        captured: dict = {}
        _extract_firebase_tokens([], captured)
        assert captured == {}

    def test_does_not_overwrite_already_captured_values(self):
        records = [
            {"value": {"apiKey": "first-key", "stsTokenManager": {"refreshToken": "rt-1"}}},
            {"value": {"apiKey": "second-key", "stsTokenManager": {"refreshToken": "rt-2"}}},
        ]
        captured: dict = {}
        _extract_firebase_tokens(records, captured)
        # First record wins — subsequent records don't overwrite
        assert captured["firebase_api_key"] == "first-key"
        assert captured["refresh_token"] == "rt-1"

    def test_non_dict_value_is_skipped(self):
        records = [{"value": 12345}, {"value": None}]
        captured: dict = {}
        _extract_firebase_tokens(records, captured)
        assert captured == {}
