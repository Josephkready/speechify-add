"""
Unit and integration tests for speechify_add/api.py.

Covers: _upload_empty, _get_title, add_url, delete_item.
_user_id_from_token is already covered in test_pure_logic.py.
"""
import base64
import json
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from speechify_add import api


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_jwt(payload: dict) -> str:
    def b64(d):
        return base64.urlsafe_b64encode(json.dumps(d).encode()).rstrip(b"=").decode()
    return f"{b64({'alg': 'RS256'})}.{b64(payload)}.fakesig"


def _mock_response(status_code: int, json_body: dict = None, text: str = "") -> MagicMock:
    resp = MagicMock(spec=httpx.Response)
    resp.status_code = status_code
    resp.text = text
    if json_body is not None:
        resp.json.return_value = json_body
    return resp


# ---------------------------------------------------------------------------
# _upload_empty
# ---------------------------------------------------------------------------

class TestUploadEmpty:
    @pytest.mark.asyncio
    async def test_returns_download_token_on_success(self):
        """Happy path: Firebase returns 200 with downloadTokens."""
        client = AsyncMock(spec=httpx.AsyncClient)
        client.post.return_value = _mock_response(
            200, json_body={"downloadTokens": "abc-token-123"}
        )
        token = await api._upload_empty(client, "fake-id-token", "multiplatform/import/uid/docid")
        assert token == "abc-token-123"

    @pytest.mark.asyncio
    async def test_http_error_raises_runtime_error(self):
        """Storage returns non-200/201 → RuntimeError with status code."""
        client = AsyncMock(spec=httpx.AsyncClient)
        client.post.return_value = _mock_response(403, text="Forbidden")
        with pytest.raises(RuntimeError, match="403"):
            await api._upload_empty(client, "tok", "some/path")

    @pytest.mark.asyncio
    async def test_missing_download_token_raises(self):
        """Storage returns 200 but no downloadTokens key → RuntimeError."""
        client = AsyncMock(spec=httpx.AsyncClient)
        client.post.return_value = _mock_response(200, json_body={"name": "some/path"})
        with pytest.raises(RuntimeError, match="download token"):
            await api._upload_empty(client, "tok", "some/path")

    @pytest.mark.asyncio
    async def test_empty_download_token_raises(self):
        """Storage returns 200 with empty downloadTokens string → RuntimeError."""
        client = AsyncMock(spec=httpx.AsyncClient)
        client.post.return_value = _mock_response(200, json_body={"downloadTokens": ""})
        with pytest.raises(RuntimeError, match="download token"):
            await api._upload_empty(client, "tok", "some/path")

    @pytest.mark.asyncio
    async def test_upload_url_encodes_storage_path(self):
        """Verify the storage path is URL-encoded in the upload request."""
        client = AsyncMock(spec=httpx.AsyncClient)
        client.post.return_value = _mock_response(200, json_body={"downloadTokens": "t"})
        await api._upload_empty(client, "tok", "multi/import/uid/doc id with spaces")
        call_url = client.post.call_args[0][0]
        assert " " not in call_url
        assert "multi" in call_url


# ---------------------------------------------------------------------------
# _get_title
# ---------------------------------------------------------------------------

class TestGetTitle:
    @pytest.mark.asyncio
    async def test_returns_title_from_verify(self):
        """When verify.get_page_title returns a title, use it."""
        with patch("speechify_add.verify.get_page_title", new=AsyncMock(return_value="My Article Title")):
            title = await api._get_title("https://example.com/article")
        assert title == "My Article Title"

    @pytest.mark.asyncio
    async def test_falls_back_to_hostname_on_exception(self):
        """When verify.get_page_title raises, fall back to hostname."""
        with patch("speechify_add.verify.get_page_title", new=AsyncMock(side_effect=RuntimeError("browser fail"))):
            title = await api._get_title("https://example.com/some/path")
        assert title == "example.com"

    @pytest.mark.asyncio
    async def test_falls_back_to_hostname_when_title_empty(self):
        """When verify.get_page_title returns empty string, fall back to hostname."""
        with patch("speechify_add.verify.get_page_title", new=AsyncMock(return_value="")):
            title = await api._get_title("https://news.ycombinator.com/item?id=123")
        assert title == "news.ycombinator.com"

    @pytest.mark.asyncio
    async def test_falls_back_to_url_when_no_hostname(self):
        """Malformed URL with no hostname falls back to the raw URL."""
        with patch("speechify_add.verify.get_page_title", new=AsyncMock(side_effect=Exception("fail"))):
            title = await api._get_title("not-a-url")
        assert title == "not-a-url"


# ---------------------------------------------------------------------------
# add_url
# ---------------------------------------------------------------------------

class TestAddUrl:
    def _make_token(self, uid: str = "user-123") -> str:
        return _make_jwt({"user_id": uid})

    @pytest.mark.asyncio
    async def test_success_no_exception(self):
        """Full happy path: storage upload + cloud function both succeed."""
        fake_token = self._make_token()
        upload_resp = _mock_response(200, json_body={"downloadTokens": "dl-tok"})
        cloud_resp = _mock_response(200, text="ok")

        mock_client = AsyncMock(spec=httpx.AsyncClient)
        mock_client.post.side_effect = [upload_resp, cloud_resp]
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        with patch("speechify_add.api.auth") as mock_auth, \
             patch("speechify_add.api._get_title", new=AsyncMock(return_value="My Title")), \
             patch("httpx.AsyncClient", return_value=mock_client):
            mock_auth.get_id_token = AsyncMock(return_value=fake_token)
            await api.add_url("https://example.com/article")

        # Two POSTs: one to storage, one to cloud function
        assert mock_client.post.call_count == 2

    @pytest.mark.asyncio
    async def test_cloud_function_error_raises_with_status_code(self):
        """Cloud function returns 500 → RuntimeError with HTTP status in message."""
        fake_token = self._make_token()
        upload_resp = _mock_response(200, json_body={"downloadTokens": "dl-tok"})
        cloud_resp = _mock_response(500, text="Internal Server Error")

        mock_client = AsyncMock(spec=httpx.AsyncClient)
        mock_client.post.side_effect = [upload_resp, cloud_resp]
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        with patch("speechify_add.api.auth") as mock_auth, \
             patch("speechify_add.api._get_title", new=AsyncMock(return_value="Title")), \
             patch("httpx.AsyncClient", return_value=mock_client):
            mock_auth.get_id_token = AsyncMock(return_value=fake_token)
            with pytest.raises(RuntimeError, match="500"):
                await api.add_url("https://example.com/article")

    @pytest.mark.asyncio
    async def test_storage_upload_failure_raises_before_cloud_fn(self):
        """If Firebase Storage upload fails (403), cloud function is never called."""
        fake_token = self._make_token()
        upload_resp = _mock_response(403, text="Permission denied")

        mock_client = AsyncMock(spec=httpx.AsyncClient)
        mock_client.post.return_value = upload_resp
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        with patch("speechify_add.api.auth") as mock_auth, \
             patch("speechify_add.api._get_title", new=AsyncMock(return_value="Title")), \
             patch("httpx.AsyncClient", return_value=mock_client):
            mock_auth.get_id_token = AsyncMock(return_value=fake_token)
            with pytest.raises(RuntimeError):
                await api.add_url("https://example.com/article")

        # Only one POST (storage); cloud function never called
        assert mock_client.post.call_count == 1

    @pytest.mark.asyncio
    async def test_cloud_fn_request_includes_url_and_user_id(self):
        """Verify the cloud function payload contains the URL and userId."""
        fake_token = self._make_token("my-uid-456")
        upload_resp = _mock_response(200, json_body={"downloadTokens": "dl-tok"})
        cloud_resp = _mock_response(200, text="ok")

        mock_client = AsyncMock(spec=httpx.AsyncClient)
        mock_client.post.side_effect = [upload_resp, cloud_resp]
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        with patch("speechify_add.api.auth") as mock_auth, \
             patch("speechify_add.api._get_title", new=AsyncMock(return_value="Title")), \
             patch("httpx.AsyncClient", return_value=mock_client):
            mock_auth.get_id_token = AsyncMock(return_value=fake_token)
            await api.add_url("https://target.com/page")

        # Second call is the cloud function
        cloud_call_kwargs = mock_client.post.call_args_list[1][1]
        payload = cloud_call_kwargs["json"]
        assert payload["url"] == "https://target.com/page"
        assert payload["userId"] == "my-uid-456"
        assert payload["type"] == "WEB"


# ---------------------------------------------------------------------------
# delete_item
# ---------------------------------------------------------------------------

class TestDeleteItem:
    @pytest.mark.asyncio
    async def test_success_no_exception(self):
        """Archive endpoint returns 200 → no exception raised."""
        fake_token = _make_jwt({"user_id": "uid-1"})
        resp = _mock_response(200, text="ok")

        mock_client = AsyncMock(spec=httpx.AsyncClient)
        mock_client.post.return_value = resp
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        with patch("speechify_add.api.auth") as mock_auth, \
             patch("httpx.AsyncClient", return_value=mock_client):
            mock_auth.get_id_token = AsyncMock(return_value=fake_token)
            await api.delete_item("some-item-uuid")

    @pytest.mark.asyncio
    async def test_204_accepted_no_exception(self):
        """204 No Content is also a valid success response."""
        fake_token = _make_jwt({"user_id": "uid-1"})
        resp = _mock_response(204, text="")

        mock_client = AsyncMock(spec=httpx.AsyncClient)
        mock_client.post.return_value = resp
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        with patch("speechify_add.api.auth") as mock_auth, \
             patch("httpx.AsyncClient", return_value=mock_client):
            mock_auth.get_id_token = AsyncMock(return_value=fake_token)
            await api.delete_item("some-item-uuid")

    @pytest.mark.asyncio
    async def test_http_error_raises_runtime_error_with_status(self):
        """Archive endpoint returns 404 → RuntimeError mentioning status code."""
        fake_token = _make_jwt({"user_id": "uid-1"})
        resp = _mock_response(404, text="Not Found")

        mock_client = AsyncMock(spec=httpx.AsyncClient)
        mock_client.post.return_value = resp
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        with patch("speechify_add.api.auth") as mock_auth, \
             patch("httpx.AsyncClient", return_value=mock_client):
            mock_auth.get_id_token = AsyncMock(return_value=fake_token)
            with pytest.raises(RuntimeError, match="404"):
                await api.delete_item("missing-item-uuid")

    @pytest.mark.asyncio
    async def test_request_payload_contains_item_id(self):
        """Verify the archive request body contains the rootItemId."""
        fake_token = _make_jwt({"user_id": "uid-1"})
        resp = _mock_response(200, text="ok")

        mock_client = AsyncMock(spec=httpx.AsyncClient)
        mock_client.post.return_value = resp
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        with patch("speechify_add.api.auth") as mock_auth, \
             patch("httpx.AsyncClient", return_value=mock_client):
            mock_auth.get_id_token = AsyncMock(return_value=fake_token)
            await api.delete_item("target-uuid-789")

        payload = mock_client.post.call_args[1]["json"]
        assert payload["rootItemId"] == "target-uuid-789"


# ---------------------------------------------------------------------------
# Integration tests
# ---------------------------------------------------------------------------

class TestIntegrationAddUrl:
    """Exercise add_url end-to-end with minimal mocking (only auth + HTTP)."""

    @pytest.mark.asyncio
    async def test_integration_source_stored_url_contains_download_token(self):
        """
        The sourceStoredURL sent to the cloud function must embed the download
        token returned by Firebase Storage — verifies data flows through correctly.
        """
        fake_token = _make_jwt({"user_id": "uid-99"})
        upload_resp = _mock_response(200, json_body={"downloadTokens": "secret-dl-token"})
        cloud_resp = _mock_response(200, text="ok")

        mock_client = AsyncMock(spec=httpx.AsyncClient)
        mock_client.post.side_effect = [upload_resp, cloud_resp]
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        with patch("speechify_add.api.auth") as mock_auth, \
             patch("speechify_add.api._get_title", new=AsyncMock(return_value="Title")), \
             patch("httpx.AsyncClient", return_value=mock_client):
            mock_auth.get_id_token = AsyncMock(return_value=fake_token)
            await api.add_url("https://example.com/page")

        cloud_payload = mock_client.post.call_args_list[1][1]["json"]
        assert "secret-dl-token" in cloud_payload["sourceStoredURL"]

    @pytest.mark.asyncio
    async def test_integration_doc_id_consistent_across_payload(self):
        """
        The same recordUid must appear in both storagePath and recordUid fields —
        verifies UUID isn't generated twice independently.
        """
        fake_token = _make_jwt({"user_id": "uid-99"})
        upload_resp = _mock_response(200, json_body={"downloadTokens": "tok"})
        cloud_resp = _mock_response(200, text="ok")

        mock_client = AsyncMock(spec=httpx.AsyncClient)
        mock_client.post.side_effect = [upload_resp, cloud_resp]
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        with patch("speechify_add.api.auth") as mock_auth, \
             patch("speechify_add.api._get_title", new=AsyncMock(return_value="Title")), \
             patch("httpx.AsyncClient", return_value=mock_client):
            mock_auth.get_id_token = AsyncMock(return_value=fake_token)
            await api.add_url("https://example.com/page")

        cloud_payload = mock_client.post.call_args_list[1][1]["json"]
        record_uid = cloud_payload["recordUid"]
        storage_path = cloud_payload["storagePath"]
        # The doc ID must appear in the storage path
        assert record_uid in storage_path
        # And storage path must contain the user ID too
        assert "uid-99" in storage_path
