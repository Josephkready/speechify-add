"""
Unit and integration tests for speechify_add/verify.py.

Covers: parse_progress_pct, get_page_title, search_library_batch.
search_library is skipped — it is a thin wrapper around async_new_page browser
automation with no testable logic beyond what search_library_batch already covers.
"""

from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from speechify_add.verify import get_page_title, parse_progress_pct, search_library_batch


# ---------------------------------------------------------------------------
# parse_progress_pct
# ---------------------------------------------------------------------------


class TestParseProgressPct:
    @pytest.mark.parametrize(
        "meta,expected",
        [
            ("73% · web", 73),
            ("0% · pdf", 0),
            ("100% · txt", 100),
            ("50% · epub", 50),
            ("25% · mp3", 25),
            ("", None),
            ("no percentage here", None),
            ("web · pdf", None),
            ("just text", None),
        ],
    )
    def test_parse_various_inputs(self, meta, expected):
        assert parse_progress_pct(meta) == expected

    def test_extracts_first_percentage_when_multiple(self):
        """When multiple percentages appear in the string, the first is returned."""
        assert parse_progress_pct("25% · 50% · web") == 25

    def test_zero_progress_not_confused_with_none(self):
        """0% is a valid value and must not be treated as missing."""
        result = parse_progress_pct("0% · web")
        assert result == 0
        assert result is not None


# ---------------------------------------------------------------------------
# get_page_title
# ---------------------------------------------------------------------------


def _mock_httpx_client(response_text: str):
    """Return a mock httpx async context manager whose .get() returns response_text."""
    mock_response = MagicMock()
    mock_response.text = response_text
    mock_client = AsyncMock()
    mock_client.get.return_value = mock_response
    cm = MagicMock()
    cm.__aenter__ = AsyncMock(return_value=mock_client)
    cm.__aexit__ = AsyncMock(return_value=None)
    return cm


class TestGetPageTitle:
    @pytest.mark.asyncio
    async def test_returns_title_from_html(self):
        cm = _mock_httpx_client("<html><head><title>My Article</title></head></html>")
        with patch("speechify_add.verify.httpx.AsyncClient", return_value=cm):
            result = await get_page_title("https://example.com/article")
        assert result == "My Article"

    @pytest.mark.asyncio
    async def test_strips_whitespace_from_title(self):
        cm = _mock_httpx_client("<title>  Spaced Title  </title>")
        with patch("speechify_add.verify.httpx.AsyncClient", return_value=cm):
            result = await get_page_title("https://example.com")
        assert result == "Spaced Title"

    @pytest.mark.asyncio
    async def test_title_match_is_case_insensitive(self):
        cm = _mock_httpx_client("<TITLE>Upper Title</TITLE>")
        with patch("speechify_add.verify.httpx.AsyncClient", return_value=cm):
            result = await get_page_title("https://example.com")
        assert result == "Upper Title"

    @pytest.mark.asyncio
    async def test_multiline_title(self):
        cm = _mock_httpx_client("<title>\n  Multiline Title\n</title>")
        with patch("speechify_add.verify.httpx.AsyncClient", return_value=cm):
            result = await get_page_title("https://example.com")
        assert result == "Multiline Title"

    @pytest.mark.asyncio
    async def test_returns_none_when_no_title_tag(self):
        cm = _mock_httpx_client("<html><body>No title here</body></html>")
        with patch("speechify_add.verify.httpx.AsyncClient", return_value=cm):
            result = await get_page_title("https://example.com")
        assert result is None

    @pytest.mark.asyncio
    async def test_returns_none_on_network_error(self):
        mock_client = AsyncMock()
        mock_client.get.side_effect = httpx.ConnectError("timeout")
        cm = MagicMock()
        cm.__aenter__ = AsyncMock(return_value=mock_client)
        cm.__aexit__ = AsyncMock(return_value=None)
        with patch("speechify_add.verify.httpx.AsyncClient", return_value=cm):
            result = await get_page_title("https://example.com")
        assert result is None

    @pytest.mark.asyncio
    async def test_returns_none_on_context_manager_error(self):
        cm = MagicMock()
        cm.__aenter__ = AsyncMock(side_effect=Exception("Connection refused"))
        cm.__aexit__ = AsyncMock(return_value=None)
        with patch("speechify_add.verify.httpx.AsyncClient", return_value=cm):
            result = await get_page_title("https://example.com")
        assert result is None

    @pytest.mark.asyncio
    async def test_passes_user_agent_header(self):
        """Requests must include a User-Agent to avoid being blocked."""
        mock_response = MagicMock()
        mock_response.text = "<title>OK</title>"
        mock_client = AsyncMock()
        mock_client.get.return_value = mock_response
        cm = MagicMock()
        cm.__aenter__ = AsyncMock(return_value=mock_client)
        cm.__aexit__ = AsyncMock(return_value=None)
        with patch("speechify_add.verify.httpx.AsyncClient", return_value=cm):
            await get_page_title("https://example.com")
        call_kwargs = mock_client.get.call_args[1]
        assert "User-Agent" in call_kwargs.get("headers", {})


# ---------------------------------------------------------------------------
# search_library_batch — integration tests
# ---------------------------------------------------------------------------


def _mock_page_cm(evaluate_side_effect):
    """
    Build a mock async_new_page context manager whose page.evaluate()
    returns values from evaluate_side_effect (list of return values, one per call).
    """
    mock_locator = AsyncMock()
    mock_page = AsyncMock()
    # locator() is a sync method returning an object with awaitable methods
    mock_page.locator = MagicMock(return_value=mock_locator)
    mock_page.evaluate.side_effect = evaluate_side_effect

    cm = MagicMock()
    cm.__aenter__ = AsyncMock(return_value=mock_page)
    cm.__aexit__ = AsyncMock(return_value=None)
    return cm


class TestSearchLibraryBatchIntegration:
    @pytest.mark.asyncio
    async def test_integration_empty_query_list_returns_empty(self):
        """Empty input produces empty output without touching the browser."""
        cm = _mock_page_cm([])
        with patch("speechify_add.verify.async_new_page", return_value=cm):
            results = await search_library_batch([])
        assert results == []

    @pytest.mark.asyncio
    async def test_integration_returns_none_for_missing_item(self):
        """A query that produces no browser results yields None in output."""
        cm = _mock_page_cm([[]])  # evaluate returns empty list
        with patch("speechify_add.verify.async_new_page", return_value=cm):
            results = await search_library_batch(["nonexistent article"])
        assert results == [None]

    @pytest.mark.asyncio
    async def test_integration_returns_pct_for_found_item(self):
        """A query that produces a result yields the parsed listen percentage."""
        cm = _mock_page_cm([[{"title": "My Article", "meta": "73% · web"}]])
        with patch("speechify_add.verify.async_new_page", return_value=cm):
            results = await search_library_batch(["my article"])
        assert results == [73]

    @pytest.mark.asyncio
    async def test_integration_partial_results_preserve_order(self):
        """
        Mixed found/not-found results maintain the same order as the input queries.
        This catches regressions where results are appended out of order.
        """
        cm = _mock_page_cm([
            [{"title": "Article A", "meta": "100% · pdf"}],  # query 1 found
            [],                                               # query 2 not found
            [{"title": "Article C", "meta": "0% · web"}],   # query 3 found
        ])
        with patch("speechify_add.verify.async_new_page", return_value=cm):
            results = await search_library_batch(["article a", "missing", "article c"])
        assert results == [100, None, 0]

    @pytest.mark.asyncio
    async def test_integration_uses_first_result_when_multiple_matches(self):
        """When multiple items match, only the first is used."""
        cm = _mock_page_cm([[
            {"title": "Best Match", "meta": "55% · web"},
            {"title": "Second Match", "meta": "10% · pdf"},
        ]])
        with patch("speechify_add.verify.async_new_page", return_value=cm):
            results = await search_library_batch(["match"])
        assert results == [55]

    @pytest.mark.asyncio
    async def test_integration_zero_pct_not_treated_as_missing(self):
        """0% listen progress is a valid result and must not be collapsed to None."""
        cm = _mock_page_cm([[{"title": "Unread Article", "meta": "0% · epub"}]])
        with patch("speechify_add.verify.async_new_page", return_value=cm):
            results = await search_library_batch(["unread"])
        assert results == [0]
        assert results[0] is not None
