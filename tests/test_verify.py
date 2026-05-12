"""
Unit and integration tests for speechify_add/verify.py.

Covers: parse_progress_pct, get_page_title, search_library_batch.
search_library is skipped — it is a thin wrapper around async_new_page browser
automation with no testable logic beyond what search_library_batch already covers.
"""

from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from speechify_add.verify import (
    get_page_title,
    parse_progress_pct,
    search_library_batch,
    verify_item_url,
    verify_item_url_fresh_context,
)


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


# ---------------------------------------------------------------------------
# verify_item_url polling (issue #47)
# ---------------------------------------------------------------------------

def _mock_item_page_cm(*, url: str, body_sequence: list[str]):
    """Make an async-context-manager that yields a page whose `evaluate(...)`
    returns successive bodies from `body_sequence`. Lets us simulate "the page
    starts with the Oops! overlay then settles to real content" scenarios.
    """
    page = MagicMock()
    page.url = url
    page.goto = AsyncMock()
    page.wait_for_timeout = AsyncMock()

    # Each call to page.evaluate(...) returns the next body in the sequence.
    # When the sequence is exhausted, repeat the last body forever (so a
    # caller polling past the deadline sees the same final state).
    iterator = iter(body_sequence)
    last = body_sequence[-1] if body_sequence else ""

    async def _eval(_js):
        nonlocal last
        try:
            last = next(iterator)
        except StopIteration:
            pass
        return last

    page.evaluate = _eval

    cm = MagicMock()
    cm.__aenter__ = AsyncMock(return_value=page)
    cm.__aexit__ = AsyncMock(return_value=None)
    return cm, page


class TestVerifyItemUrl:
    UUID = "cff1772b-7603-4d46-966c-97b4b4566443"
    GOOD_BODY = (
        "Real Article Title\nShare\n\nA full body of meaningful content "
        "going on for hundreds of characters because this is what a real "
        "Speechify item page looks like with title, summary, and player "
        "controls all rendered." * 2
    )
    OOPS_BODY = (
        "Oops! Something went wrong\n\nRefresh the page or try again later.\n"
        "\nReturn to My Library\nNeed help? Contact support"
    )

    @pytest.mark.asyncio
    async def test_passes_immediately_for_settled_item(self):
        """A healthy item returns True on the first poll."""
        cm, _ = _mock_item_page_cm(
            url=f"https://app.speechify.com/item/{self.UUID}",
            body_sequence=[self.GOOD_BODY],
        )
        with patch("speechify_add.verify.async_new_page", return_value=cm):
            ok, info = await verify_item_url(self.UUID, max_wait=10)
        assert ok is True
        assert "1 poll" in info  # settled on first poll

    @pytest.mark.asyncio
    async def test_settles_after_initial_oops(self):
        """A fresh upload that briefly shows the Oops! overlay still passes
        once the page settles within the budget (issue #47 core scenario)."""
        cm, _ = _mock_item_page_cm(
            url=f"https://app.speechify.com/item/{self.UUID}",
            body_sequence=[
                self.OOPS_BODY,           # poll 1: still rendering
                self.OOPS_BODY,           # poll 2: still rendering
                self.GOOD_BODY,           # poll 3: done
            ],
        )
        with patch("speechify_add.verify.async_new_page", return_value=cm):
            ok, info = await verify_item_url(self.UUID, max_wait=10)
        assert ok is True
        assert "3 poll" in info

    @pytest.mark.asyncio
    async def test_settles_after_short_body(self):
        """Same idea but the partial state is a near-empty body, not the
        Oops! overlay."""
        cm, _ = _mock_item_page_cm(
            url=f"https://app.speechify.com/item/{self.UUID}",
            body_sequence=["…", "loading…", self.GOOD_BODY],
        )
        with patch("speechify_add.verify.async_new_page", return_value=cm):
            ok, info = await verify_item_url(self.UUID, max_wait=10)
        assert ok is True

    @pytest.mark.asyncio
    async def test_fails_when_overlay_persists(self):
        """A truly missing item shows the Oops! overlay across every poll —
        we should fail with a clear message after the deadline."""
        cm, _ = _mock_item_page_cm(
            url=f"https://app.speechify.com/item/{self.UUID}",
            body_sequence=[self.OOPS_BODY],  # repeats forever
        )
        with patch("speechify_add.verify.async_new_page", return_value=cm):
            ok, info = await verify_item_url(self.UUID, max_wait=4)
        assert ok is False
        assert "Oops" in info
        assert "never became playable" in info

    @pytest.mark.asyncio
    async def test_fails_when_redirected_away(self):
        """If the page redirects away from /item/<uuid> (e.g. to /login),
        bail immediately."""
        cm, _ = _mock_item_page_cm(
            url="https://app.speechify.com/login",
            body_sequence=["irrelevant"],
        )
        with patch("speechify_add.verify.async_new_page", return_value=cm):
            ok, info = await verify_item_url(self.UUID, max_wait=10)
        assert ok is False
        assert "redirected" in info.lower()


# ---------------------------------------------------------------------------
# verify_item_url_fresh_context (issue #51)
# ---------------------------------------------------------------------------

def _mock_fresh_context_playwright(*, url, body_sequence, cookies=None):
    """Build the mock pipeline ``verify_item_url_fresh_context`` walks
    through: ``async_playwright()`` → ``chromium.connect_over_cdp`` →
    ``browser.contexts[0].cookies`` (auth) → ``browser.new_context()`` →
    fresh ``page.goto`` + polling.

    ``cookies`` is the list returned by ``default_ctx.cookies(...)``; if
    None, defaults to a single ``session`` cookie. Tests that exercise
    the cookie filter pass a richer cookie list and inspect the
    ``add_cookies`` call afterward.

    Returns ``(playwright_cm, refs)`` where ``refs`` exposes the mocked
    page, fresh_ctx, default_ctx, and browser so tests can assert on
    call arguments (which cookies were transplanted, etc.).
    """
    if cookies is None:
        cookies = [{"name": "session", "value": "fake-jwt", "domain": ".speechify.com"}]

    page = MagicMock()
    page.url = url
    page.goto = AsyncMock()
    page.wait_for_timeout = AsyncMock()
    page.close = AsyncMock()

    # evaluate(...) yields successive bodies, then repeats the last one
    # forever — matching the existing _mock_item_page_cm semantics.
    iterator = iter(body_sequence)
    last_body = body_sequence[-1] if body_sequence else ""

    async def _eval(_js):
        nonlocal last_body
        try:
            last_body = next(iterator)
        except StopIteration:
            pass
        return last_body

    page.evaluate = _eval

    fresh_ctx = MagicMock()
    fresh_ctx.new_page = AsyncMock(return_value=page)
    fresh_ctx.add_cookies = AsyncMock()
    fresh_ctx.close = AsyncMock()

    default_ctx = MagicMock()
    default_ctx.cookies = AsyncMock(return_value=cookies)

    browser = MagicMock()
    browser.contexts = [default_ctx]
    browser.new_context = AsyncMock(return_value=fresh_ctx)

    chromium = MagicMock()
    chromium.connect_over_cdp = AsyncMock(return_value=browser)

    pw = MagicMock()
    pw.chromium = chromium

    playwright_cm = MagicMock()
    playwright_cm.__aenter__ = AsyncMock(return_value=pw)
    playwright_cm.__aexit__ = AsyncMock(return_value=None)

    refs = {
        "page": page,
        "fresh_ctx": fresh_ctx,
        "default_ctx": default_ctx,
        "browser": browser,
    }
    return playwright_cm, refs


class TestVerifyItemUrlFreshContext:
    """Issue #51: fresh-context verify reads the item page from a
    BrowserContext that didn't do the upload — no shared IndexedDB,
    so it sees what every other browser sees (not the cached version).
    """

    UUID = "abcdef01-2345-6789-abcd-ef0123456789"
    GOOD_BODY = (
        "Real Article Title\nShare\n\nA full body of meaningful content "
        "going on for hundreds of characters because this is what a real "
        "Speechify item page looks like with title, summary, and player "
        "controls all rendered." * 2
    )
    OOPS_BODY = (
        "Oops! Something went wrong\n\nRefresh the page or try again later.\n"
        "\nReturn to My Library\nNeed help? Contact support"
    )

    @pytest.mark.asyncio
    async def test_passes_immediately_for_healthy_item(self):
        """Body has real content on first poll → returns True with a
        ``settled after 1 poll`` message."""
        cm, _refs = _mock_fresh_context_playwright(
            url=f"https://app.speechify.com/item/{self.UUID}",
            body_sequence=[self.GOOD_BODY],
        )
        with patch("speechify_add.verify.async_playwright", return_value=cm):
            ok, info = await verify_item_url_fresh_context(self.UUID, max_wait=10)
        assert ok is True
        assert "settled after 1 poll" in info

    @pytest.mark.asyncio
    async def test_settles_after_initial_oops(self):
        """Mirrors the scenario where the item page briefly shows the
        Oops! overlay right after navigation before settling to real
        content. The poller must keep going across overlay polls and
        return True once the body becomes real."""
        cm, _refs = _mock_fresh_context_playwright(
            url=f"https://app.speechify.com/item/{self.UUID}",
            body_sequence=[self.OOPS_BODY, self.OOPS_BODY, self.GOOD_BODY],
        )
        with patch("speechify_add.verify.async_playwright", return_value=cm):
            ok, info = await verify_item_url_fresh_context(self.UUID, max_wait=10)
        assert ok is True
        assert "settled after 3 polls" in info

    @pytest.mark.asyncio
    async def test_fails_when_oops_persists(self):
        """The exact issue #51 failure mode: content blob isn't on the
        server, so the Oops! overlay never clears. Must return False
        with a clear message after the deadline."""
        cm, _refs = _mock_fresh_context_playwright(
            url=f"https://app.speechify.com/item/{self.UUID}",
            body_sequence=[self.OOPS_BODY],  # repeats forever
        )
        with patch("speechify_add.verify.async_playwright", return_value=cm):
            ok, info = await verify_item_url_fresh_context(self.UUID, max_wait=4)
        assert ok is False
        assert "Oops" in info
        assert "never became playable" in info
        assert "issue #51" in info

    @pytest.mark.asyncio
    async def test_fails_on_short_body(self):
        """A non-Oops but near-empty body (e.g. auth flash, partial
        render) must NOT pass — too short to be real content."""
        cm, _refs = _mock_fresh_context_playwright(
            url=f"https://app.speechify.com/item/{self.UUID}",
            body_sequence=["loading…"],  # 8 chars, well below threshold
        )
        with patch("speechify_add.verify.async_playwright", return_value=cm):
            ok, info = await verify_item_url_fresh_context(self.UUID, max_wait=4)
        assert ok is False
        assert "never became playable" in info

    @pytest.mark.asyncio
    async def test_fails_when_redirected_off_item_page(self):
        """If the fresh context lands somewhere other than /item/<uuid>
        (auth bounce, item-not-found redirect), bail immediately."""
        cm, _refs = _mock_fresh_context_playwright(
            url="https://app.speechify.com/auth/web/",
            body_sequence=["irrelevant"],
        )
        with patch("speechify_add.verify.async_playwright", return_value=cm):
            ok, info = await verify_item_url_fresh_context(self.UUID, max_wait=10)
        assert ok is False
        assert "redirected" in info.lower()

    @pytest.mark.asyncio
    async def test_fails_when_no_auth_cookies(self):
        """If chrome-hub's default context has no Speechify session
        cookie, fresh-context verify cannot authenticate. Return a
        clear False rather than try-and-redirect-to-login."""
        cm, _refs = _mock_fresh_context_playwright(
            url=f"https://app.speechify.com/item/{self.UUID}",
            body_sequence=[self.GOOD_BODY],
            cookies=[
                # All non-auth tracking cookies — should be filtered out
                {"name": "_ga", "value": "GA1.x", "domain": ".speechify.com"},
                {"name": "_fbp", "value": "fb.1.y", "domain": ".speechify.com"},
            ],
        )
        with patch("speechify_add.verify.async_playwright", return_value=cm):
            ok, info = await verify_item_url_fresh_context(self.UUID, max_wait=10)
        assert ok is False
        assert "no Speechify auth cookies" in info

    @pytest.mark.asyncio
    async def test_only_transplants_recognised_auth_cookies(self):
        """Only cookies in ``_FRESH_AUTH_COOKIE_NAMES`` should reach the
        fresh context — analytics / ad cookies stay out so the fresh
        context renders cleanly without tracker interference."""
        cm, refs = _mock_fresh_context_playwright(
            url=f"https://app.speechify.com/item/{self.UUID}",
            body_sequence=[self.GOOD_BODY],
            cookies=[
                {"name": "session", "value": "AUTH-JWT", "domain": ".speechify.com"},
                {"name": "axwrt", "value": "AUTH-AXWRT", "domain": "speechify.com"},
                {"name": "cf_clearance", "value": "CF", "domain": ".speechify.com"},
                {"name": "_ga", "value": "tracker", "domain": ".speechify.com"},
                {"name": "intercom-session-fix72gk8", "value": "im", "domain": ".speechify.com"},
                {"name": "ajs_user_id", "value": "uid", "domain": ".app.speechify.com"},
            ],
        )
        with patch("speechify_add.verify.async_playwright", return_value=cm):
            ok, _info = await verify_item_url_fresh_context(self.UUID, max_wait=10)
        assert ok is True

        refs["fresh_ctx"].add_cookies.assert_awaited_once()
        transplanted = refs["fresh_ctx"].add_cookies.await_args[0][0]
        transplanted_names = {c["name"] for c in transplanted}
        assert transplanted_names == {"session", "axwrt", "cf_clearance"}
        # Trackers must not leak into the fresh context
        assert "_ga" not in transplanted_names
        assert "ajs_user_id" not in transplanted_names

    @pytest.mark.asyncio
    async def test_closes_fresh_context_even_on_failure(self):
        """Resource cleanup: the fresh ``BrowserContext`` and ``page``
        must close whether verify passes or fails. Without this we'd
        leak Chrome tabs across retry loops in ``_verify_or_cleanup_fresh_context``.
        """
        cm, refs = _mock_fresh_context_playwright(
            url=f"https://app.speechify.com/item/{self.UUID}",
            body_sequence=[self.OOPS_BODY],
        )
        with patch("speechify_add.verify.async_playwright", return_value=cm):
            await verify_item_url_fresh_context(self.UUID, max_wait=2)

        refs["page"].close.assert_awaited_once()
        refs["fresh_ctx"].close.assert_awaited_once()
