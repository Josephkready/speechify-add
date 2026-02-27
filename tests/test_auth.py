"""Tests for speechify_add.auth — token management and Firebase extraction."""

import tests.conftest  # noqa: F401 — mock third-party deps

import asyncio
import json
import time
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

from speechify_add import auth, config


def run_async(coro):
    """Helper to run async tests."""
    return asyncio.get_event_loop().run_until_complete(coro)


class TestGetIdToken(unittest.TestCase):
    """get_id_token returns cached or refreshed tokens."""

    def test_returns_cached_token_when_not_expired(self):
        future = time.time() + 3600
        data = {"id_token": "cached_tok", "id_token_expires_at": future}
        with patch.object(config, "load", return_value=data):
            result = run_async(auth.get_id_token())
        self.assertEqual(result, "cached_tok")

    def test_refreshes_when_token_expired(self):
        past = time.time() - 100
        data = {
            "id_token": "old_tok",
            "id_token_expires_at": past,
            "refresh_token": "rt",
            "firebase_api_key": "key",
        }
        with (
            patch.object(config, "load", return_value=data),
            patch.object(auth, "_refresh_id_token", new_callable=AsyncMock, return_value="new_tok"),
        ):
            result = run_async(auth.get_id_token())
        self.assertEqual(result, "new_tok")

    def test_refreshes_within_5_minute_buffer(self):
        # Token expires in 4 minutes — within the 5-minute buffer
        almost_expired = time.time() + 240
        data = {
            "id_token": "old_tok",
            "id_token_expires_at": almost_expired,
            "refresh_token": "rt",
            "firebase_api_key": "key",
        }
        with (
            patch.object(config, "load", return_value=data),
            patch.object(auth, "_refresh_id_token", new_callable=AsyncMock, return_value="refreshed"),
        ):
            result = run_async(auth.get_id_token())
        self.assertEqual(result, "refreshed")

    def test_raises_when_not_authenticated(self):
        with patch.object(config, "load", return_value={}):
            with self.assertRaises(RuntimeError) as ctx:
                run_async(auth.get_id_token())
            self.assertIn("Not authenticated", str(ctx.exception))

    def test_refreshes_when_no_id_token(self):
        data = {
            "id_token_expires_at": time.time() + 3600,
            "refresh_token": "rt",
            "firebase_api_key": "key",
        }
        with (
            patch.object(config, "load", return_value=data),
            patch.object(auth, "_refresh_id_token", new_callable=AsyncMock, return_value="new"),
        ):
            result = run_async(auth.get_id_token())
        self.assertEqual(result, "new")


class TestRefreshAndPrint(unittest.TestCase):

    def test_raises_when_not_authenticated(self):
        with patch.object(config, "load", return_value={}):
            with self.assertRaises(RuntimeError):
                run_async(auth.refresh_and_print())

    def test_calls_refresh(self):
        data = {"refresh_token": "rt", "firebase_api_key": "key"}
        mock_refresh = AsyncMock(return_value="new_tok")
        with (
            patch.object(config, "load", return_value=data),
            patch.object(auth, "_refresh_id_token", mock_refresh),
        ):
            run_async(auth.refresh_and_print())
        mock_refresh.assert_awaited_once_with(data)


class TestRefreshIdToken(unittest.TestCase):

    def test_raises_when_missing_refresh_token(self):
        with self.assertRaises(RuntimeError) as ctx:
            run_async(auth._refresh_id_token({"firebase_api_key": "key"}))
        self.assertIn("Missing refresh token", str(ctx.exception))

    def test_raises_when_missing_api_key(self):
        with self.assertRaises(RuntimeError):
            run_async(auth._refresh_id_token({"refresh_token": "rt"}))

    def test_raises_when_both_missing(self):
        with self.assertRaises(RuntimeError):
            run_async(auth._refresh_id_token({}))


class TestExtractFirebaseTokens(unittest.TestCase):
    """_extract_firebase_tokens parses IndexedDB records."""

    def test_extracts_from_sts_token_manager(self):
        records = [
            {
                "value": {
                    "apiKey": "my_api_key",
                    "stsTokenManager": {
                        "refreshToken": "rt_123",
                        "accessToken": "at_456",
                        "expirationTime": (time.time() + 3600) * 1000,
                    },
                }
            }
        ]
        captured = {}
        auth._extract_firebase_tokens(records, captured)

        self.assertEqual(captured["firebase_api_key"], "my_api_key")
        self.assertEqual(captured["refresh_token"], "rt_123")
        self.assertEqual(captured["id_token"], "at_456")
        self.assertIn("id_token_expires_at", captured)

    def test_extracts_from_json_string_value(self):
        inner = json.dumps({
            "apiKey": "k1",
            "refreshToken": "rt1",
            "stsTokenManager": {"refreshToken": "rt2", "accessToken": "at2"},
        })
        records = [{"value": inner}]
        captured = {}
        auth._extract_firebase_tokens(records, captured)

        self.assertEqual(captured["firebase_api_key"], "k1")
        self.assertEqual(captured["refresh_token"], "rt2")

    def test_skips_non_dict_values(self):
        records = [{"value": 12345}, {"value": None}, "just a string"]
        captured = {}
        auth._extract_firebase_tokens(records, captured)
        self.assertEqual(captured, {})

    def test_does_not_overwrite_existing_captured_values(self):
        records = [
            {"value": {"apiKey": "first", "stsTokenManager": {"refreshToken": "rt_first"}}},
            {"value": {"apiKey": "second", "stsTokenManager": {"refreshToken": "rt_second"}}},
        ]
        captured = {}
        auth._extract_firebase_tokens(records, captured)

        self.assertEqual(captured["firebase_api_key"], "first")
        self.assertEqual(captured["refresh_token"], "rt_first")

    def test_extracts_top_level_refresh_token(self):
        records = [{"value": {"refreshToken": "top_level_rt"}}]
        captured = {}
        auth._extract_firebase_tokens(records, captured)
        self.assertEqual(captured["refresh_token"], "top_level_rt")

    def test_handles_invalid_json_string_gracefully(self):
        records = [{"value": "{not valid json}"}]
        captured = {}
        auth._extract_firebase_tokens(records, captured)
        self.assertEqual(captured, {})


if __name__ == "__main__":
    unittest.main()
