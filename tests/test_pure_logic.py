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
from speechify_add.verify import parse_progress_pct


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
    def test_typical_web_item(self):
        assert parse_progress_pct("73% · web") == 73

    def test_zero_percent(self):
        assert parse_progress_pct("0% · pdf") == 0

    def test_hundred_percent(self):
        assert parse_progress_pct("100% · txt") == 100

    def test_no_percentage(self):
        assert parse_progress_pct("no progress here") is None

    def test_empty_string(self):
        assert parse_progress_pct("") is None

    def test_multiple_percentages_returns_first(self):
        assert parse_progress_pct("50% done, 80% quality · web") == 50

    def test_percentage_embedded_in_text(self):
        assert parse_progress_pct("Listen progress: 42% · epub") == 42


# ---------------------------------------------------------------------------
# 9. _collect_urls edge cases
# ---------------------------------------------------------------------------

class TestCollectUrlsEdgeCases:
    def test_comment_only_file(self, tmp_path):
        url_file = tmp_path / "urls.txt"
        url_file.write_text("# comment 1\n# comment 2\n")
        result = _collect_urls(None, str(url_file), False)
        assert result == []

    def test_file_with_whitespace_around_urls(self, tmp_path):
        url_file = tmp_path / "urls.txt"
        url_file.write_text("  https://example.com/a  \n  https://example.com/b  \n")
        result = _collect_urls(None, str(url_file), False)
        assert result == ["https://example.com/a", "https://example.com/b"]

    def test_inline_comment_not_stripped(self, tmp_path):
        """Lines starting with # are comments; inline # is kept as-is."""
        url_file = tmp_path / "urls.txt"
        url_file.write_text("https://example.com/a#section\n")
        result = _collect_urls(None, str(url_file), False)
        assert result == ["https://example.com/a#section"]


# ---------------------------------------------------------------------------
# 10. Batch progress JSON validation
# ---------------------------------------------------------------------------

class TestBatchProgressValidation:
    def test_valid_batch_json_structure(self):
        """Ensure _do_progress_batch validates required fields."""
        import asyncio
        from speechify_add.cli import _do_progress_batch

        # Missing "title" field should raise BadParameter
        with pytest.raises(click.BadParameter, match='missing required "title"'):
            asyncio.run(_do_progress_batch('[{"id": "abc"}]', None))

    def test_batch_non_array_raises(self):
        import asyncio
        from speechify_add.cli import _do_progress_batch

        with pytest.raises(click.BadParameter, match="must be a JSON array"):
            asyncio.run(_do_progress_batch('{"not": "an array"}', None))

    def test_batch_non_object_item_raises(self):
        import asyncio
        from speechify_add.cli import _do_progress_batch

        with pytest.raises(click.BadParameter, match="must be a JSON object"):
            asyncio.run(_do_progress_batch('["just a string"]', None))
