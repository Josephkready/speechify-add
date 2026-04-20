"""
Unit and integration tests for speechify_add/auth.py.

Untestable without infrastructure (skipped with comments):
  - setup(): requires Playwright headed browser
  - _read_firebase_indexeddb(): requires a live Playwright page object
  - _refresh_from_chrome_hub(): tested only for ImportError path here;
    the full path requires chrome_hub + a live browser session
"""

import time
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from speechify_add import auth


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_data(**kwargs):
    base = {
        "firebase_api_key": "fake-api-key",
        "refresh_token": "fake-refresh-token",
        "id_token": "fake-id-token",
        "id_token_expires_at": time.time() + 7200,
    }
    base.update(kwargs)
    return base


# ---------------------------------------------------------------------------
# _extract_firebase_tokens — pure logic, no I/O
# ---------------------------------------------------------------------------

class TestExtractFirebaseTokens:
    def test_extracts_api_key_from_value_dict(self):
        records = [{"value": {"apiKey": "my-api-key", "stsTokenManager": {}}}]
        captured = {}
        auth._extract_firebase_tokens(records, captured)
        assert captured["firebase_api_key"] == "my-api-key"

    def test_extracts_refresh_token_from_sts_manager(self):
        records = [{"value": {"stsTokenManager": {"refreshToken": "rtoken123"}}}]
        captured = {}
        auth._extract_firebase_tokens(records, captured)
        assert captured["refresh_token"] == "rtoken123"

    def test_extracts_id_token_from_sts_access_token(self):
        records = [{"value": {"stsTokenManager": {"accessToken": "idtoken456"}}}]
        captured = {}
        auth._extract_firebase_tokens(records, captured)
        assert captured["id_token"] == "idtoken456"

    def test_expiration_time_divided_by_1000(self):
        exp_ms = 1_700_000_000_000  # milliseconds
        records = [{"value": {"stsTokenManager": {"expirationTime": exp_ms}}}]
        captured = {}
        auth._extract_firebase_tokens(records, captured)
        assert captured["id_token_expires_at"] == exp_ms / 1000

    def test_extracts_top_level_refresh_token(self):
        records = [{"value": {"refreshToken": "top-level-rtoken"}}]
        captured = {}
        auth._extract_firebase_tokens(records, captured)
        assert captured["refresh_token"] == "top-level-rtoken"

    def test_value_as_json_string_is_parsed(self):
        import json
        value_dict = {"apiKey": "json-str-key"}
        records = [{"value": json.dumps(value_dict)}]
        captured = {}
        auth._extract_firebase_tokens(records, captured)
        assert captured["firebase_api_key"] == "json-str-key"

    def test_does_not_overwrite_already_captured_api_key(self):
        records = [
            {"value": {"apiKey": "first-key"}},
            {"value": {"apiKey": "second-key"}},
        ]
        captured = {}
        auth._extract_firebase_tokens(records, captured)
        assert captured["firebase_api_key"] == "first-key"

    def test_does_not_overwrite_already_captured_refresh_token(self):
        records = [
            {"value": {"stsTokenManager": {"refreshToken": "first"}}},
            {"value": {"stsTokenManager": {"refreshToken": "second"}}},
        ]
        captured = {}
        auth._extract_firebase_tokens(records, captured)
        assert captured["refresh_token"] == "first"

    def test_empty_records_list(self):
        captured = {}
        auth._extract_firebase_tokens([], captured)
        assert captured == {}

    def test_non_dict_value_skipped(self):
        records = [{"value": 42}, {"value": None}]
        captured = {}
        auth._extract_firebase_tokens(records, captured)
        assert captured == {}

    def test_invalid_json_string_value_skipped(self):
        records = [{"value": "NOT{json"}]
        captured = {}
        auth._extract_firebase_tokens(records, captured)
        assert captured == {}

    def test_record_without_value_key_skipped(self):
        # record is not a dict with "value" — falls through safely
        records = [{"someOtherKey": "irrelevant"}]
        captured = {}
        auth._extract_firebase_tokens(records, captured)
        assert captured == {}

    def test_bare_dict_record_used_directly(self):
        # When a record itself is not a dict, record.get("value") returns None -> skipped
        # When record is a dict but value is also a dict with apiKey
        records = [{"value": {"apiKey": "direct-key", "stsTokenManager": {"refreshToken": "rk"}}}]
        captured = {}
        auth._extract_firebase_tokens(records, captured)
        assert captured["firebase_api_key"] == "direct-key"
        assert captured["refresh_token"] == "rk"


# ---------------------------------------------------------------------------
# get_id_token — token caching and refresh dispatch
# ---------------------------------------------------------------------------

class TestGetIdToken:
    @pytest.mark.asyncio
    async def test_raises_when_not_authenticated(self):
        with patch("speechify_add.auth.config") as mock_config:
            mock_config.load.return_value = {}
            with pytest.raises(RuntimeError, match="Not authenticated"):
                await auth.get_id_token()

    @pytest.mark.asyncio
    async def test_returns_cached_token_when_not_expired(self):
        data = _make_data(id_token="cached-token", id_token_expires_at=time.time() + 7200)
        with patch("speechify_add.auth.config") as mock_config:
            mock_config.load.return_value = data
            result = await auth.get_id_token()
        assert result == "cached-token"

    @pytest.mark.asyncio
    async def test_refreshes_when_token_within_5min_buffer(self):
        # expires_at is only 4 minutes away — should trigger refresh
        data = _make_data(id_token="stale-token", id_token_expires_at=time.time() + 240)
        with patch("speechify_add.auth.config") as mock_config, \
             patch("speechify_add.auth._refresh_id_token", new_callable=AsyncMock) as mock_refresh:
            mock_config.load.return_value = data
            mock_refresh.return_value = "fresh-token"
            result = await auth.get_id_token()
        assert result == "fresh-token"
        mock_refresh.assert_called_once_with(data)

    @pytest.mark.asyncio
    async def test_refreshes_when_token_is_missing(self):
        data = _make_data(id_token_expires_at=time.time() + 7200)
        del data["id_token"]
        with patch("speechify_add.auth.config") as mock_config, \
             patch("speechify_add.auth._refresh_id_token", new_callable=AsyncMock) as mock_refresh:
            mock_config.load.return_value = data
            mock_refresh.return_value = "new-token"
            result = await auth.get_id_token()
        assert result == "new-token"

    @pytest.mark.asyncio
    async def test_refreshes_when_expires_at_missing(self):
        # No id_token_expires_at — defaults to 0, always expired
        data = {"firebase_api_key": "k", "refresh_token": "r", "id_token": "t"}
        with patch("speechify_add.auth.config") as mock_config, \
             patch("speechify_add.auth._refresh_id_token", new_callable=AsyncMock) as mock_refresh:
            mock_config.load.return_value = data
            mock_refresh.return_value = "refreshed"
            await auth.get_id_token()
        mock_refresh.assert_called_once()


# ---------------------------------------------------------------------------
# _refresh_id_token — Firebase REST API path
# ---------------------------------------------------------------------------

class TestRefreshIdToken:
    def _make_httpx_response(self, status_code, json_body):
        resp = MagicMock()
        resp.status_code = status_code
        resp.json.return_value = json_body
        resp.raise_for_status = MagicMock()
        if status_code >= 400:
            import httpx
            resp.raise_for_status.side_effect = httpx.HTTPStatusError(
                "error", request=MagicMock(), response=resp
            )
        return resp

    @pytest.mark.asyncio
    async def test_successful_refresh_saves_new_token(self):
        data = {"firebase_api_key": "k", "refresh_token": "old-rt"}
        firebase_resp = {
            "id_token": "new-id-token",
            "refresh_token": "new-rt",
            "expires_in": "3600",
        }
        mock_resp = self._make_httpx_response(200, firebase_resp)
        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.post = AsyncMock(return_value=mock_resp)

        with patch("speechify_add.auth.config") as mock_config, \
             patch("httpx.AsyncClient", return_value=mock_client):
            mock_config.load.return_value = data
            result = await auth._refresh_id_token(data)

        assert result == "new-id-token"
        assert data["id_token"] == "new-id-token"
        assert data["refresh_token"] == "new-rt"
        mock_config.save.assert_called_once_with(data)

    @pytest.mark.asyncio
    async def test_sends_referer_header(self):
        """Firebase API key requires Referer: https://app.speechify.com/ to avoid 403."""
        data = {"firebase_api_key": "k", "refresh_token": "old-rt"}
        firebase_resp = {"id_token": "new-id", "refresh_token": "new-rt", "expires_in": "3600"}
        mock_resp = self._make_httpx_response(200, firebase_resp)
        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.post = AsyncMock(return_value=mock_resp)

        with patch("speechify_add.auth.config"), \
             patch("httpx.AsyncClient", return_value=mock_client):
            await auth._refresh_id_token(data)

        _, kwargs = mock_client.post.call_args
        headers = kwargs.get("headers", {})
        assert headers.get("Referer") == "https://app.speechify.com/"

    @pytest.mark.asyncio
    async def test_preserves_old_refresh_token_if_not_in_response(self):
        data = {"firebase_api_key": "k", "refresh_token": "old-rt"}
        firebase_resp = {"id_token": "new-id", "expires_in": "3600"}
        mock_resp = self._make_httpx_response(200, firebase_resp)
        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.post = AsyncMock(return_value=mock_resp)

        with patch("speechify_add.auth.config"), \
             patch("httpx.AsyncClient", return_value=mock_client):
            await auth._refresh_id_token(data)

        assert data["refresh_token"] == "old-rt"

    @pytest.mark.asyncio
    async def test_400_response_falls_back_to_chrome_hub(self):
        data = {"firebase_api_key": "k", "refresh_token": "rt"}
        mock_resp = self._make_httpx_response(400, {})
        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.post = AsyncMock(return_value=mock_resp)

        with patch("httpx.AsyncClient", return_value=mock_client), \
             patch("speechify_add.auth._refresh_from_chrome_hub", new_callable=AsyncMock) as mock_hub:
            mock_hub.return_value = "hub-token"
            result = await auth._refresh_id_token(data)

        assert result == "hub-token"
        mock_hub.assert_called_once_with(data)

    @pytest.mark.asyncio
    async def test_403_response_falls_back_to_chrome_hub(self):
        data = {"firebase_api_key": "k", "refresh_token": "rt"}
        mock_resp = self._make_httpx_response(403, {})
        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.post = AsyncMock(return_value=mock_resp)

        with patch("httpx.AsyncClient", return_value=mock_client), \
             patch("speechify_add.auth._refresh_from_chrome_hub", new_callable=AsyncMock) as mock_hub:
            mock_hub.return_value = "hub-token"
            result = await auth._refresh_id_token(data)

        assert result == "hub-token"

    @pytest.mark.asyncio
    async def test_missing_refresh_token_falls_back_to_chrome_hub(self):
        data = {"firebase_api_key": "k"}  # no refresh_token
        with patch("speechify_add.auth._refresh_from_chrome_hub", new_callable=AsyncMock) as mock_hub:
            mock_hub.return_value = "hub-token"
            result = await auth._refresh_id_token(data)
        assert result == "hub-token"
        mock_hub.assert_called_once_with(data)

    @pytest.mark.asyncio
    async def test_missing_api_key_falls_back_to_chrome_hub(self):
        data = {"refresh_token": "rt"}  # no api_key
        with patch("speechify_add.auth._refresh_from_chrome_hub", new_callable=AsyncMock) as mock_hub:
            mock_hub.return_value = "hub-token"
            result = await auth._refresh_id_token(data)
        assert result == "hub-token"

    @pytest.mark.asyncio
    async def test_non_400_http_error_propagates(self):
        import httpx
        data = {"firebase_api_key": "k", "refresh_token": "rt"}
        mock_resp = MagicMock()
        mock_resp.status_code = 500
        mock_resp.raise_for_status.side_effect = httpx.HTTPStatusError(
            "Server error", request=MagicMock(), response=mock_resp
        )
        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.post = AsyncMock(return_value=mock_resp)

        with patch("httpx.AsyncClient", return_value=mock_client):
            with pytest.raises(httpx.HTTPStatusError):
                await auth._refresh_id_token(data)

    @pytest.mark.asyncio
    async def test_expires_at_set_to_future(self):
        data = {"firebase_api_key": "k", "refresh_token": "rt"}
        firebase_resp = {"id_token": "tok", "expires_in": "3600"}
        mock_resp = self._make_httpx_response(200, firebase_resp)
        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.post = AsyncMock(return_value=mock_resp)

        before = time.time()
        with patch("speechify_add.auth.config"), \
             patch("httpx.AsyncClient", return_value=mock_client):
            await auth._refresh_id_token(data)
        after = time.time()

        assert before + 3600 <= data["id_token_expires_at"] <= after + 3600


# ---------------------------------------------------------------------------
# _refresh_from_chrome_hub — ImportError path
# ---------------------------------------------------------------------------

class TestRefreshFromChromeHub:
    @pytest.mark.asyncio
    async def test_raises_runtime_error_when_chrome_hub_not_installed(self):
        data = {"firebase_api_key": "k", "refresh_token": "rt"}
        import builtins
        real_import = builtins.__import__

        def mock_import(name, *args, **kwargs):
            if name == "chrome_hub":
                raise ImportError("No module named 'chrome_hub'")
            return real_import(name, *args, **kwargs)

        with patch("builtins.__import__", side_effect=mock_import):
            with pytest.raises(RuntimeError, match="chrome-hub is not installed"):
                await auth._refresh_from_chrome_hub(data)

    @pytest.mark.asyncio
    async def test_raises_when_chrome_hub_lands_on_auth_page(self):
        data = {"firebase_api_key": "k", "refresh_token": "rt"}
        mock_page = AsyncMock()
        mock_page.url = "https://app.speechify.com/auth/login"
        mock_page.goto = AsyncMock()
        mock_page.wait_for_timeout = AsyncMock()

        mock_ctx_manager = AsyncMock()
        mock_ctx_manager.__aenter__ = AsyncMock(return_value=mock_page)
        mock_ctx_manager.__aexit__ = AsyncMock(return_value=False)

        mock_chrome_hub = MagicMock()
        mock_chrome_hub.async_new_page.return_value = mock_ctx_manager

        with patch.dict("sys.modules", {"chrome_hub": mock_chrome_hub}), \
             patch("speechify_add.auth._read_firebase_indexeddb", new_callable=AsyncMock) as mock_read:
            mock_read.return_value = []
            with pytest.raises(RuntimeError, match="not logged into Speechify"):
                await auth._refresh_from_chrome_hub(data)

    @pytest.mark.asyncio
    async def test_raises_when_no_id_token_extracted(self):
        data = {"firebase_api_key": "k", "refresh_token": "rt"}
        mock_page = AsyncMock()
        mock_page.url = "https://app.speechify.com/home"  # not auth page
        mock_page.goto = AsyncMock()
        mock_page.wait_for_timeout = AsyncMock()

        mock_ctx_manager = AsyncMock()
        mock_ctx_manager.__aenter__ = AsyncMock(return_value=mock_page)
        mock_ctx_manager.__aexit__ = AsyncMock(return_value=False)

        mock_chrome_hub = MagicMock()
        mock_chrome_hub.async_new_page.return_value = mock_ctx_manager

        with patch.dict("sys.modules", {"chrome_hub": mock_chrome_hub}), \
             patch("speechify_add.auth._read_firebase_indexeddb", new_callable=AsyncMock) as mock_read, \
             patch("speechify_add.auth._extract_firebase_tokens"):
            mock_read.return_value = []
            with pytest.raises(RuntimeError, match="Could not extract Firebase token"):
                await auth._refresh_from_chrome_hub(data)

    @pytest.mark.asyncio
    async def test_updates_data_and_saves_on_success(self):
        data = {"firebase_api_key": "k", "refresh_token": "old-rt"}
        mock_page = AsyncMock()
        mock_page.url = "https://app.speechify.com/home"
        mock_page.goto = AsyncMock()
        mock_page.wait_for_timeout = AsyncMock()

        mock_ctx_manager = AsyncMock()
        mock_ctx_manager.__aenter__ = AsyncMock(return_value=mock_page)
        mock_ctx_manager.__aexit__ = AsyncMock(return_value=False)

        mock_chrome_hub = MagicMock()
        mock_chrome_hub.async_new_page.return_value = mock_ctx_manager

        fresh_tokens = {
            "id_token": "hub-fresh-token",
            "refresh_token": "hub-new-rt",
            "firebase_api_key": "hub-key",
            "id_token_expires_at": time.time() + 3600,
        }

        def fake_extract(records, captured):
            captured.update(fresh_tokens)

        with patch.dict("sys.modules", {"chrome_hub": mock_chrome_hub}), \
             patch("speechify_add.auth._read_firebase_indexeddb", new_callable=AsyncMock) as mock_read, \
             patch("speechify_add.auth._extract_firebase_tokens", side_effect=fake_extract), \
             patch("speechify_add.auth.config") as mock_config:
            mock_read.return_value = []
            result = await auth._refresh_from_chrome_hub(data)

        assert result == "hub-fresh-token"
        assert data["id_token"] == "hub-fresh-token"
        assert data["refresh_token"] == "hub-new-rt"
        mock_config.save.assert_called_once_with(data)

    @pytest.mark.asyncio
    async def test_sets_default_expiry_when_not_extracted(self):
        data = {}
        mock_page = AsyncMock()
        mock_page.url = "https://app.speechify.com/home"
        mock_page.goto = AsyncMock()
        mock_page.wait_for_timeout = AsyncMock()

        mock_ctx_manager = AsyncMock()
        mock_ctx_manager.__aenter__ = AsyncMock(return_value=mock_page)
        mock_ctx_manager.__aexit__ = AsyncMock(return_value=False)

        mock_chrome_hub = MagicMock()
        mock_chrome_hub.async_new_page.return_value = mock_ctx_manager

        def fake_extract(records, captured):
            captured["id_token"] = "tok"
            # no id_token_expires_at

        before = time.time()
        with patch.dict("sys.modules", {"chrome_hub": mock_chrome_hub}), \
             patch("speechify_add.auth._read_firebase_indexeddb", new_callable=AsyncMock) as mock_read, \
             patch("speechify_add.auth._extract_firebase_tokens", side_effect=fake_extract), \
             patch("speechify_add.auth.config"):
            mock_read.return_value = []
            await auth._refresh_from_chrome_hub(data)
        after = time.time()

        assert before + 3600 <= data["id_token_expires_at"] <= after + 3600


# ---------------------------------------------------------------------------
# refresh_and_print
# ---------------------------------------------------------------------------

class TestRefreshAndPrint:
    @pytest.mark.asyncio
    async def test_raises_when_not_authenticated(self):
        with patch("speechify_add.auth.config") as mock_config:
            mock_config.load.return_value = {}
            with pytest.raises(RuntimeError, match="Not authenticated"):
                await auth.refresh_and_print()

    @pytest.mark.asyncio
    async def test_calls_refresh_with_loaded_data(self):
        data = _make_data()
        with patch("speechify_add.auth.config") as mock_config, \
             patch("speechify_add.auth._refresh_id_token", new_callable=AsyncMock) as mock_refresh:
            mock_config.load.return_value = data
            mock_refresh.return_value = "token"
            await auth.refresh_and_print()
        mock_refresh.assert_called_once_with(data)


# ---------------------------------------------------------------------------
# Integration: get_id_token → _refresh_id_token → config.save
# ---------------------------------------------------------------------------

class TestIntegrationGetIdTokenRefreshFlow:
    @pytest.mark.asyncio
    async def test_integration_full_refresh_updates_config(self, tmp_path):
        """Expired token triggers Firebase call; config is updated with new token."""
        import json
        import os
        from speechify_add import config as speechify_config

        auth_file = tmp_path / "auth.json"
        data = {
            "firebase_api_key": "real-key",
            "refresh_token": "real-rt",
            "id_token": "old-token",
            "id_token_expires_at": time.time() - 100,  # expired
        }
        auth_file.write_text(json.dumps(data))
        os.chmod(auth_file, 0o600)

        firebase_resp = {"id_token": "brand-new-token", "expires_in": "3600"}
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.raise_for_status = MagicMock()
        mock_resp.json.return_value = firebase_resp
        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.post = AsyncMock(return_value=mock_resp)

        with patch.object(speechify_config, "AUTH_FILE", auth_file), \
             patch.object(speechify_config, "CONFIG_DIR", tmp_path), \
             patch("httpx.AsyncClient", return_value=mock_client):
            result = await auth.get_id_token()

        assert result == "brand-new-token"
        saved = json.loads(auth_file.read_text())
        assert saved["id_token"] == "brand-new-token"

    @pytest.mark.asyncio
    async def test_integration_cached_token_no_http_call(self, tmp_path):
        """Valid unexpired token returned without any network call."""
        import json
        import os
        from speechify_add import config as speechify_config

        auth_file = tmp_path / "auth.json"
        data = {
            "firebase_api_key": "k",
            "refresh_token": "rt",
            "id_token": "still-valid",
            "id_token_expires_at": time.time() + 7200,
        }
        auth_file.write_text(json.dumps(data))
        os.chmod(auth_file, 0o600)

        with patch.object(speechify_config, "AUTH_FILE", auth_file), \
             patch.object(speechify_config, "CONFIG_DIR", tmp_path), \
             patch("httpx.AsyncClient") as mock_httpx:
            result = await auth.get_id_token()

        assert result == "still-valid"
        mock_httpx.assert_not_called()

    @pytest.mark.asyncio
    async def test_integration_400_then_chrome_hub_raises_without_chrome_hub(self, tmp_path):
        """Firebase 400 → chrome_hub fallback → ImportError → RuntimeError."""
        import json
        import os
        from speechify_add import config as speechify_config

        auth_file = tmp_path / "auth.json"
        data = {
            "firebase_api_key": "k",
            "refresh_token": "rt",
            "id_token": "old",
            "id_token_expires_at": time.time() - 1,
        }
        auth_file.write_text(json.dumps(data))
        os.chmod(auth_file, 0o600)

        mock_resp = MagicMock()
        mock_resp.status_code = 400
        mock_resp.raise_for_status = MagicMock()
        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.post = AsyncMock(return_value=mock_resp)

        import builtins
        real_import = builtins.__import__

        def mock_import(name, *args, **kwargs):
            if name == "chrome_hub":
                raise ImportError("not installed")
            return real_import(name, *args, **kwargs)

        with patch.object(speechify_config, "AUTH_FILE", auth_file), \
             patch.object(speechify_config, "CONFIG_DIR", tmp_path), \
             patch("httpx.AsyncClient", return_value=mock_client), \
             patch("builtins.__import__", side_effect=mock_import):
            with pytest.raises(RuntimeError, match="chrome-hub is not installed"):
                await auth.get_id_token()
