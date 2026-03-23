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
    _collect_urls, _collect_text, _extract_title_from_text,
)
from speechify_add import config as speechify_config
import importlib
import sys
import types

# verify.py and auth.py have top-level imports that may not be available
# in the test environment (chrome_hub, playwright). Stub them before importing.
_chrome_hub_stub = types.ModuleType("chrome_hub")
_chrome_hub_stub.async_new_page = None  # type: ignore[attr-defined]
sys.modules.setdefault("chrome_hub", _chrome_hub_stub)

from speechify_add.verify import parse_progress_pct
from speechify_add.auth import _extract_firebase_tokens


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

    def test_save_creates_dir_with_restricted_permissions(self, tmp_path):
        new_dir = tmp_path / "new_config"
        fake_auth_file = new_dir / "auth.json"
        with patch.object(speechify_config, "CONFIG_DIR", new_dir), \
             patch.object(speechify_config, "AUTH_FILE", fake_auth_file):
            speechify_config.save({"key": "val"})
        mode = stat.S_IMODE(os.stat(new_dir).st_mode)
        assert mode == 0o700

    def test_save_cleanup_on_write_error(self, tmp_path):
        """Temp files should not linger if json.dump raises."""
        fake_config_dir = tmp_path / "cfg"
        fake_config_dir.mkdir(mode=0o700)
        fake_auth_file = fake_config_dir / "auth.json"

        class BadObj:
            """Object that can't be serialized to JSON."""
            pass

        with patch.object(speechify_config, "CONFIG_DIR", fake_config_dir), \
             patch.object(speechify_config, "AUTH_FILE", fake_auth_file):
            with pytest.raises(TypeError):
                speechify_config.save({"bad": BadObj()})

        # No .tmp files should remain
        tmp_files = list(fake_config_dir.glob("*.tmp"))
        assert tmp_files == []


# ---------------------------------------------------------------------------
# 8. parse_progress_pct
# ---------------------------------------------------------------------------

class TestParseProgressPct:
    def test_typical_web(self):
        assert parse_progress_pct("73% · web") == 73

    def test_zero(self):
        assert parse_progress_pct("0% · pdf") == 0

    def test_hundred(self):
        assert parse_progress_pct("100% · txt") == 100

    def test_empty_string(self):
        assert parse_progress_pct("") is None

    def test_no_percent(self):
        assert parse_progress_pct("web · some title") is None

    def test_multiple_percents_picks_first(self):
        assert parse_progress_pct("50% · 75% · web") == 50


# ---------------------------------------------------------------------------
# 9. _extract_firebase_tokens
# ---------------------------------------------------------------------------

class TestExtractFirebaseTokens:
    def test_extracts_from_sts_token_manager(self):
        records = [
            {
                "value": {
                    "apiKey": "AIzaFakeKey",
                    "stsTokenManager": {
                        "refreshToken": "rt_abc",
                        "accessToken": "at_xyz",
                        "expirationTime": 1700000000000,
                    },
                }
            }
        ]
        captured = {}
        _extract_firebase_tokens(records, captured)
        assert captured["firebase_api_key"] == "AIzaFakeKey"
        assert captured["refresh_token"] == "rt_abc"
        assert captured["id_token"] == "at_xyz"
        assert captured["id_token_expires_at"] == 1700000000.0

    def test_extracts_top_level_refresh_token(self):
        records = [{"value": {"refreshToken": "rt_top"}}]
        captured = {}
        _extract_firebase_tokens(records, captured)
        assert captured["refresh_token"] == "rt_top"

    def test_skips_non_dict_values(self):
        records = [{"value": "not a dict"}, {"value": 42}, None]
        captured = {}
        _extract_firebase_tokens(records, captured)
        assert captured == {}

    def test_does_not_overwrite_existing(self):
        records = [
            {"value": {"apiKey": "key1"}},
            {"value": {"apiKey": "key2"}},
        ]
        captured = {}
        _extract_firebase_tokens(records, captured)
        assert captured["firebase_api_key"] == "key1"

    def test_json_string_value_parsed(self):
        records = [{"value": json.dumps({"apiKey": "from_json_str"})}]
        captured = {}
        _extract_firebase_tokens(records, captured)
        assert captured["firebase_api_key"] == "from_json_str"


# ---------------------------------------------------------------------------
# 10. _collect_urls comment handling
# ---------------------------------------------------------------------------

class TestCollectUrlsComments:
    def test_indented_comments_are_skipped(self, tmp_path):
        url_file = tmp_path / "urls.txt"
        url_file.write_text(
            "https://example.com/a\n"
            "  # indented comment\n"
            "\t# tab comment\n"
            "https://example.com/b\n"
        )
        result = _collect_urls(None, str(url_file), False)
        assert result == ["https://example.com/a", "https://example.com/b"]


# ---------------------------------------------------------------------------
# 11. _user_id_from_token via pyjwt
# ---------------------------------------------------------------------------

class TestUserIdFromTokenPyJWT:
    def test_token_with_extra_segments_still_works(self):
        """pyjwt handles malformed tokens more gracefully than manual decode."""
        token = _make_jwt({"user_id": "uid-ok"})
        assert _user_id_from_token(token) == "uid-ok"

    def test_empty_string_raises(self):
        with pytest.raises(RuntimeError):
            _user_id_from_token("")
