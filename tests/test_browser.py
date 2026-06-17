"""
Unit and integration tests for speechify_add/browser.py

Mocks only at I/O boundaries (Playwright page interactions).
Does NOT run a real browser.
"""
import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import pytest_asyncio

from speechify_add.browser import (
    _assert_logged_in,
    _extract_item_id,
    _find_first_visible,
    _click_first_visible,
    _maybe_delete_partial_item,
    _open_paste_text_modal,
    _StepSkipped,
    _verify_or_cleanup_fresh_context,
    BrowserSession,
    ADD_TEXT_BUTTON_SELECTORS,
    ADD_TEXT_BUTTON_TIMEOUT_MS,
    PASTE_TEXT_MENU_SELECTORS,
    PASTE_TEXT_MENU_TIMEOUT_MS,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_page(url="https://app.speechify.com"):
    """Return a MagicMock resembling a Playwright page."""
    page = MagicMock()
    page.url = url
    page.on = MagicMock()
    return page


def _make_locator(visible=True):
    """Return an AsyncMock locator that optionally appears visible."""
    loc = MagicMock()
    if visible:
        loc.wait_for = AsyncMock()
    else:
        loc.wait_for = AsyncMock(side_effect=Exception("timeout"))
    loc.click = AsyncMock()
    return loc


# ---------------------------------------------------------------------------
# 1. _assert_logged_in
# ---------------------------------------------------------------------------

class TestAssertLoggedIn:
    @pytest.mark.parametrize("login_url", [
        "https://app.speechify.com/login",
        "https://app.speechify.com/signin",
        "https://app.speechify.com/sign-in",
        "https://app.speechify.com/auth",
        "https://app.speechify.com/auth?redirect=/",
    ])
    def test_raises_on_login_urls(self, login_url):
        page = _make_page(url=login_url)
        with pytest.raises(RuntimeError, match="Session expired"):
            _assert_logged_in(page)

    @pytest.mark.parametrize("ok_url", [
        "https://app.speechify.com",
        "https://app.speechify.com/item/some-uuid",
        "https://app.speechify.com/library",
    ])
    def test_no_error_on_authenticated_urls(self, ok_url):
        page = _make_page(url=ok_url)
        _assert_logged_in(page)  # must not raise


# ---------------------------------------------------------------------------
# 2. _find_first_visible
# ---------------------------------------------------------------------------

class TestFindFirstVisible:
    def test_per_timeout_uses_max_500(self):
        """Timeout per selector is max(500, timeout // n_selectors)."""
        # With timeout=2000 and 5 selectors: 2000//5 = 400, so per = max(500,400) = 500
        # We test this indirectly by verifying wait_for is called with timeout=500
        page = MagicMock()
        loc = _make_locator(visible=True)
        page.locator.return_value.first = loc

        asyncio.get_event_loop().run_until_complete(
            _find_first_visible(page, ["sel1", "sel2", "sel3", "sel4", "sel5"],
                                step="test", timeout=2000)
        )
        # wait_for should have been called with timeout=500 (not 400)
        loc.wait_for.assert_awaited_once_with(state="visible", timeout=500)

    def test_per_timeout_divides_evenly(self):
        """With timeout=6000 and 2 selectors: per = max(500, 3000) = 3000."""
        page = MagicMock()
        loc = _make_locator(visible=True)
        page.locator.return_value.first = loc

        asyncio.get_event_loop().run_until_complete(
            _find_first_visible(page, ["sel1", "sel2"], step="test", timeout=6000)
        )
        loc.wait_for.assert_awaited_once_with(state="visible", timeout=3000)

    def test_raises_step_skipped_when_all_fail(self):
        """Raises _StepSkipped when no selector matches."""
        page = MagicMock()
        invisible = _make_locator(visible=False)
        page.locator.return_value.first = invisible

        with pytest.raises(_StepSkipped, match="No visible element for 'my-step'"):
            asyncio.get_event_loop().run_until_complete(
                _find_first_visible(page, ["#a", "#b"], step="my-step", timeout=1000)
            )

    def test_returns_first_visible_and_skips_rest(self):
        """Returns first visible locator and does not try remaining selectors."""
        page = MagicMock()

        visible_loc = _make_locator(visible=True)
        never_loc = _make_locator(visible=True)

        call_count = 0

        def side_effect(selector):
            nonlocal call_count
            call_count += 1
            m = MagicMock()
            m.first = visible_loc if call_count == 1 else never_loc
            return m

        page.locator.side_effect = side_effect

        result = asyncio.get_event_loop().run_until_complete(
            _find_first_visible(page, ["#first", "#second"], step="s", timeout=1000)
        )
        assert result is visible_loc
        # Second locator should never have been tried
        never_loc.wait_for.assert_not_awaited()

    def test_skips_failing_selector_tries_next(self):
        """If first selector fails, tries the next one."""
        page = MagicMock()

        fail_loc = _make_locator(visible=False)
        ok_loc = _make_locator(visible=True)

        call_count = 0

        def side_effect(selector):
            nonlocal call_count
            call_count += 1
            m = MagicMock()
            m.first = fail_loc if call_count == 1 else ok_loc
            return m

        page.locator.side_effect = side_effect

        result = asyncio.get_event_loop().run_until_complete(
            _find_first_visible(page, ["#bad", "#good"], step="s", timeout=1000)
        )
        assert result is ok_loc


# ---------------------------------------------------------------------------
# 3. _click_first_visible
# ---------------------------------------------------------------------------

class TestClickFirstVisible:
    def test_clicks_the_found_locator(self):
        page = MagicMock()
        loc = _make_locator(visible=True)
        page.locator.return_value.first = loc

        asyncio.get_event_loop().run_until_complete(
            _click_first_visible(page, ["#btn"], step="btn", timeout=1000)
        )
        loc.click.assert_awaited_once()

    def test_propagates_step_skipped(self):
        page = MagicMock()
        invisible = _make_locator(visible=False)
        page.locator.return_value.first = invisible

        with pytest.raises(_StepSkipped):
            asyncio.get_event_loop().run_until_complete(
                _click_first_visible(page, ["#missing"], step="missing", timeout=500)
            )


# ---------------------------------------------------------------------------
# 4. BrowserSession.__init__
# ---------------------------------------------------------------------------

class TestBrowserSessionInit:
    def test_defaults(self):
        s = BrowserSession()
        assert s.debug is False
        assert s._page is None
        assert s._page_cm is None
        assert s._console_errors == []

    def test_debug_flag_stored(self):
        s = BrowserSession(debug=True)
        assert s.debug is True


# ---------------------------------------------------------------------------
# 5. BrowserSession.__aexit__ does not suppress exceptions
# ---------------------------------------------------------------------------

class TestBrowserSessionAexit:
    def test_aexit_returns_false(self):
        """Exceptions must propagate out of the context manager."""
        session = BrowserSession()
        cm = MagicMock()
        cm.__aexit__ = AsyncMock(return_value=None)
        session._page_cm = cm

        result = asyncio.get_event_loop().run_until_complete(
            session.__aexit__(ValueError, ValueError("boom"), None)
        )
        assert result is False

    def test_aexit_with_no_page_cm(self):
        """aexit is safe when _page_cm was never set."""
        session = BrowserSession()
        # Should not raise
        asyncio.get_event_loop().run_until_complete(
            session.__aexit__(None, None, None)
        )


# ---------------------------------------------------------------------------
# 6. BrowserSession.add_url — crash detection
# ---------------------------------------------------------------------------

class TestBrowserSessionAddUrl:
    def _make_session(self):
        session = BrowserSession()
        page = _make_page()
        session._page = page
        return session, page

    def test_raises_runtime_error_on_crash(self):
        """Raises RuntimeError when 'Application error' text appears on page."""
        session, page = self._make_session()
        page.locator = MagicMock()

        # _perform_add mock
        perform_mock = AsyncMock()
        # crash count
        crash_locator = MagicMock()
        crash_locator.count = AsyncMock(return_value=1)

        def locator_side_effect(selector):
            m = MagicMock()
            if "Application error" in selector:
                m.count = AsyncMock(return_value=1)
            else:
                m.count = AsyncMock(return_value=0)
                m.wait_for = AsyncMock()
                m.click = AsyncMock()
                m.first = m
            return m

        page.locator.side_effect = locator_side_effect
        page.wait_for_timeout = AsyncMock()
        page.goto = AsyncMock()

        with patch("speechify_add.browser._perform_add", perform_mock):
            with pytest.raises(RuntimeError, match="crashed"):
                asyncio.get_event_loop().run_until_complete(
                    session.add_url("https://example.com")
                )

    def test_no_error_when_no_crash(self):
        """No exception when app does not crash."""
        session, page = self._make_session()

        perform_mock = AsyncMock()
        page.wait_for_timeout = AsyncMock()
        page.goto = AsyncMock()

        def locator_side_effect(selector):
            m = MagicMock()
            m.count = AsyncMock(return_value=0)
            m.wait_for = AsyncMock()
            m.click = AsyncMock()
            m.first = m
            return m

        page.locator.side_effect = locator_side_effect

        with patch("speechify_add.browser._perform_add", perform_mock):
            asyncio.get_event_loop().run_until_complete(
                session.add_url("https://example.com")
            )

    def test_console_errors_cleared_before_add(self):
        """_console_errors list is cleared before each add_url call."""
        session, page = self._make_session()
        session._console_errors = ["stale error from previous call"]

        perform_mock = AsyncMock()
        page.wait_for_timeout = AsyncMock()
        page.goto = AsyncMock()

        def locator_side_effect(selector):
            m = MagicMock()
            m.count = AsyncMock(return_value=0)
            m.wait_for = AsyncMock()
            m.click = AsyncMock()
            m.first = m
            return m

        page.locator.side_effect = locator_side_effect

        with patch("speechify_add.browser._perform_add", perform_mock):
            asyncio.get_event_loop().run_until_complete(
                session.add_url("https://example.com")
            )

        assert session._console_errors == []


# ---------------------------------------------------------------------------
# 7. BrowserSession.add_text — timeout raises RuntimeError
# ---------------------------------------------------------------------------

class TestBrowserSessionAddText:
    """BrowserSession.add_text routes text through the file-upload path
    (issue #51): write a temp .txt and call add_file. We assert the
    routing contract here; add_file itself has separate coverage.
    """

    def test_routes_through_add_file_with_tempfile(self):
        """Text is written to a .txt file and passed to add_file."""
        session = BrowserSession()
        session._page = _make_page()
        # _navigate_to_library is called after the upload — stub it.
        session._navigate_to_library = AsyncMock()

        captured = {}

        async def fake_add_file(path, title="", debug=False):
            # The tempfile must still exist while add_file is running
            # so Speechify's Firebase upload can read its bytes.
            assert path.exists(), f"temp file gone before add_file ran: {path}"
            captured["path"] = path
            captured["title"] = title
            captured["text"] = path.read_text(encoding="utf-8")
            return "https://app.speechify.com/item/abcdef01-2345-6789-abcd-ef0123456789"

        async def run():
            with patch("speechify_add.browser.add_file", new=fake_add_file):
                return await session.add_text(
                    "the body text", title="My Title",
                )

        result = asyncio.get_event_loop().run_until_complete(run())
        assert result.endswith("abcdef01-2345-6789-abcd-ef0123456789")
        assert captured["text"] == "the body text"
        assert captured["title"] == "My Title"
        assert captured["path"].suffix == ".txt"
        # And the temp file is cleaned up after add_file returns.
        assert not captured["path"].exists()

    def test_propagates_add_file_exceptions(self):
        """RuntimeError from add_file (e.g. fresh-context verify failure)
        propagates so callers see real failures instead of broken URLs."""
        session = BrowserSession()
        session._page = _make_page()
        session._navigate_to_library = AsyncMock()

        async def run():
            with patch(
                "speechify_add.browser.add_file",
                new=AsyncMock(side_effect=RuntimeError("content blob never persisted")),
            ):
                with pytest.raises(RuntimeError, match="content blob never persisted"):
                    await session.add_text("x")

        asyncio.get_event_loop().run_until_complete(run())


# ---------------------------------------------------------------------------
# 8. BrowserSession.delete_item — still on item page raises RuntimeError
# ---------------------------------------------------------------------------

class TestBrowserSessionDeleteItem:
    def test_raises_if_still_on_item_page_with_no_indicator(self):
        """RuntimeError raised if still on /item/<id> and no 'not found'/'deleted' text."""
        item_id = "abc-123"
        session = BrowserSession()
        page = _make_page(url=f"https://app.speechify.com/item/{item_id}")
        session._page = page

        page.goto = AsyncMock()
        page.wait_for_timeout = AsyncMock()

        def locator_side_effect(selector):
            m = MagicMock()
            m.wait_for = AsyncMock()
            m.click = AsyncMock()
            m.count = AsyncMock(return_value=0)
            m.first = m
            return m

        page.locator.side_effect = locator_side_effect

        with patch("speechify_add.browser._find_first_visible", AsyncMock(return_value=MagicMock(click=AsyncMock()))), \
             patch("speechify_add.browser._click_first_visible", AsyncMock()):
            with pytest.raises(RuntimeError, match="Deletion may have failed"):
                asyncio.get_event_loop().run_until_complete(
                    session.delete_item(item_id)
                )

    def test_no_error_when_redirected_away_from_item(self):
        """No error if page navigated away from /item/<id> after deletion."""
        item_id = "abc-123"
        session = BrowserSession()
        page = _make_page(url="https://app.speechify.com")  # already redirected
        session._page = page

        page.goto = AsyncMock()
        page.wait_for_timeout = AsyncMock()

        def locator_side_effect(selector):
            m = MagicMock()
            m.wait_for = AsyncMock()
            m.click = AsyncMock()
            m.count = AsyncMock(return_value=0)
            m.first = m
            return m

        page.locator.side_effect = locator_side_effect

        with patch("speechify_add.browser._find_first_visible", AsyncMock(return_value=MagicMock(click=AsyncMock()))), \
             patch("speechify_add.browser._click_first_visible", AsyncMock()):
            asyncio.get_event_loop().run_until_complete(
                session.delete_item(item_id)
            )  # must not raise


# ---------------------------------------------------------------------------
# Integration: multi-step workflows
# ---------------------------------------------------------------------------

class TestIntegrationBrowserSessionLifecycle:
    def test_integration_aexit_called_on_exception(self):
        """__aexit__ is called even when body of context manager raises."""
        async def run():
            cm = MagicMock()
            inner_page = _make_page()
            inner_page.wait_for_timeout = AsyncMock()

            cm.__aenter__ = AsyncMock(return_value=inner_page)
            cm.__aexit__ = AsyncMock(return_value=None)

            with patch("speechify_add.browser.tracked_page", return_value=cm), \
                 patch("speechify_add.browser._init_speechify_page", AsyncMock()):
                try:
                    async with BrowserSession() as session:
                        raise ValueError("intentional error")
                except ValueError:
                    pass

            # __aexit__ must have been called despite the exception
            cm.__aexit__.assert_awaited_once()
            exc_type = cm.__aexit__.call_args[0][0]
            assert exc_type is ValueError

        asyncio.get_event_loop().run_until_complete(run())

    def test_integration_add_url_navigates_back_after_success(self):
        """After a successful add_url, session navigates back to library."""
        async def run():
            session = BrowserSession()
            page = _make_page()
            session._page = page

            page.wait_for_timeout = AsyncMock()
            page.goto = AsyncMock()

            def locator_side_effect(selector):
                m = MagicMock()
                m.count = AsyncMock(return_value=0)
                m.wait_for = AsyncMock()
                m.click = AsyncMock()
                m.first = m
                return m

            page.locator.side_effect = locator_side_effect

            with patch("speechify_add.browser._perform_add", AsyncMock()):
                await session.add_url("https://example.com/article")

            # _navigate_to_library calls page.goto with speechify URL
            goto_calls = [str(c) for c in page.goto.call_args_list]
            assert any("speechify.com" in c for c in goto_calls)

        asyncio.get_event_loop().run_until_complete(run())

    def test_integration_add_text_filename_derived_from_title(self):
        """Title becomes the temp file's basename so Speechify uses it as
        the item title (issue #51 routes text through file-upload).
        """
        async def run():
            session = BrowserSession()
            session._page = _make_page()
            session._navigate_to_library = AsyncMock()
            captured = {}

            async def fake_add_file(path, title="", debug=False):
                captured["path"] = path
                captured["title"] = title
                return "https://app.speechify.com/item/abcdef01-2345-6789-abcd-ef0123456789"

            with patch("speechify_add.browser.add_file", new=fake_add_file):
                await session.add_text("body text", title="My Custom Title")

            assert "My-Custom-Title" in captured["path"].name
            assert captured["title"] == "My Custom Title"

        asyncio.get_event_loop().run_until_complete(run())


# ---------------------------------------------------------------------------
# 9. _verify_or_cleanup_fresh_context (issue #51)
# ---------------------------------------------------------------------------


class TestVerifyOrCleanupFreshContext:
    """Post-upload fresh-context verification hook.

    Stubs out ``verify.verify_item_url_fresh_context`` and asserts the
    retry/cleanup contract: succeed on first OK, retry on transient
    False until budget exhaustion, then delete + raise.
    """

    _ITEM_URL = "https://app.speechify.com/item/abcdef01-2345-6789-abcd-ef0123456789"
    _ITEM_ID = "abcdef01-2345-6789-abcd-ef0123456789"

    def test_returns_quietly_when_first_attempt_passes(self):
        """If fresh-context verify returns True on attempt 1, exit without delete."""
        verify_mock = AsyncMock(return_value=(True, "passed"))
        delete_mock = AsyncMock()
        sleep_mock = AsyncMock()

        async def run():
            page = _make_page()
            with patch(
                "speechify_add.verify.verify_item_url_fresh_context",
                new=verify_mock,
            ), patch(
                "speechify_add.browser._perform_delete", new=delete_mock,
            ), patch(
                "speechify_add.browser.asyncio.sleep", new=sleep_mock,
            ):
                await _verify_or_cleanup_fresh_context(
                    self._ITEM_URL, self._ITEM_ID, page, debug=False,
                )

        asyncio.get_event_loop().run_until_complete(run())
        assert verify_mock.await_count == 1
        delete_mock.assert_not_awaited()

    def test_raises_after_budget_when_verify_keeps_failing(self):
        """If verify never succeeds, _perform_delete is called and RuntimeError raised."""
        verify_mock = AsyncMock(return_value=(False, "Oops! persists"))
        delete_mock = AsyncMock()
        # Skip real sleeps so the test doesn't take 90s — the loop will
        # still terminate because monotonic() ticks forward inside verify_mock.
        sleep_mock = AsyncMock()

        async def run():
            page = _make_page(url=self._ITEM_URL)
            with patch(
                "speechify_add.verify.verify_item_url_fresh_context",
                new=verify_mock,
            ), patch(
                "speechify_add.browser._perform_delete", new=delete_mock,
            ), patch(
                "speechify_add.browser.asyncio.sleep", new=sleep_mock,
            ), patch(
                "speechify_add.browser.POST_UPLOAD_VERIFY_BUDGET_SEC", 0.5,
            ):
                with pytest.raises(RuntimeError, match="content blob never persisted"):
                    await _verify_or_cleanup_fresh_context(
                        self._ITEM_URL, self._ITEM_ID, page, debug=False,
                    )

        asyncio.get_event_loop().run_until_complete(run())
        assert verify_mock.await_count >= 1
        delete_mock.assert_awaited_once()

    def test_cleanup_failure_does_not_mask_root_error(self):
        """If _perform_delete itself raises, the RuntimeError about
        non-persistence is still what reaches the caller — cleanup
        failure is logged but secondary."""
        verify_mock = AsyncMock(return_value=(False, "Oops! persists"))
        delete_mock = AsyncMock(side_effect=RuntimeError("delete also failed"))
        sleep_mock = AsyncMock()

        async def run():
            page = _make_page(url=self._ITEM_URL)
            with patch(
                "speechify_add.verify.verify_item_url_fresh_context",
                new=verify_mock,
            ), patch(
                "speechify_add.browser._perform_delete", new=delete_mock,
            ), patch(
                "speechify_add.browser.asyncio.sleep", new=sleep_mock,
            ), patch(
                "speechify_add.browser.POST_UPLOAD_VERIFY_BUDGET_SEC", 0.5,
            ):
                with pytest.raises(RuntimeError, match="content blob never persisted"):
                    await _verify_or_cleanup_fresh_context(
                        self._ITEM_URL, self._ITEM_ID, page, debug=False,
                    )

        asyncio.get_event_loop().run_until_complete(run())

    def test_retries_on_exception_from_verify(self):
        """Transient exceptions during fresh-context verify (e.g. Chrome's
        ``net::ERR_NETWORK_CHANGED`` during ``page.goto``, or
        ``TargetClosedError`` from chrome-hub orphan cleanup) should be
        caught and counted as a failed attempt, then retried within the
        budget — not propagated as upload failure.
        """
        verify_mock = AsyncMock(side_effect=[
            RuntimeError("net::ERR_NETWORK_CHANGED transient"),
            (True, "passed on retry"),
        ])
        delete_mock = AsyncMock()
        sleep_mock = AsyncMock()

        async def run():
            page = _make_page()
            with patch(
                "speechify_add.verify.verify_item_url_fresh_context",
                new=verify_mock,
            ), patch(
                "speechify_add.browser._perform_delete", new=delete_mock,
            ), patch(
                "speechify_add.browser.asyncio.sleep", new=sleep_mock,
            ):
                await _verify_or_cleanup_fresh_context(
                    self._ITEM_URL, self._ITEM_ID, page, debug=False,
                )

        asyncio.get_event_loop().run_until_complete(run())
        # Two attempts: one that raised, one that succeeded
        assert verify_mock.await_count == 2
        # Item was eventually verified, so no cleanup
        delete_mock.assert_not_awaited()

    def test_exhausts_budget_when_verify_keeps_raising(self):
        """If every attempt raises (transient errors all the way down),
        the budget eventually expires, cleanup runs, and a RuntimeError
        with the exception class name in the reason is propagated."""
        verify_mock = AsyncMock(side_effect=RuntimeError("ERR_NETWORK_CHANGED"))
        delete_mock = AsyncMock()
        sleep_mock = AsyncMock()

        async def run():
            page = _make_page(url=self._ITEM_URL)
            with patch(
                "speechify_add.verify.verify_item_url_fresh_context",
                new=verify_mock,
            ), patch(
                "speechify_add.browser._perform_delete", new=delete_mock,
            ), patch(
                "speechify_add.browser.asyncio.sleep", new=sleep_mock,
            ), patch(
                "speechify_add.browser.POST_UPLOAD_VERIFY_BUDGET_SEC", 0.5,
            ):
                with pytest.raises(RuntimeError, match="content blob never persisted"):
                    await _verify_or_cleanup_fresh_context(
                        self._ITEM_URL, self._ITEM_ID, page, debug=False,
                    )

        asyncio.get_event_loop().run_until_complete(run())
        assert verify_mock.await_count >= 1
        delete_mock.assert_awaited_once()


# ---------------------------------------------------------------------------
# 10. _extract_item_id — pure logic
# ---------------------------------------------------------------------------

class TestExtractItemId:
    @pytest.mark.parametrize("url,expected", [
        ("https://app.speechify.com/item/cff1772b-7603-4d46-966c-97b4b4566443",
         "cff1772b-7603-4d46-966c-97b4b4566443"),
        ("https://app.speechify.com/item/CFF1772B-7603-4D46-966C-97B4B4566443?x=1",
         "CFF1772B-7603-4D46-966C-97B4B4566443"),
        ("/item/cff1772b-7603-4d46-966c-97b4b4566443",
         "cff1772b-7603-4d46-966c-97b4b4566443"),
    ])
    def test_extracts_uuid(self, url, expected):
        assert _extract_item_id(url) == expected

    @pytest.mark.parametrize("url", [
        None,
        "",
        "https://app.speechify.com",
        "https://app.speechify.com/library",
        "https://app.speechify.com/item/not-a-uuid",
        "https://app.speechify.com/item/abc-123",  # short fake form
    ])
    def test_returns_none_for_non_item_urls(self, url):
        assert _extract_item_id(url) is None


# ---------------------------------------------------------------------------
# 11. _maybe_delete_partial_item — orphan cleanup
# ---------------------------------------------------------------------------

class TestMaybeDeletePartialItem:
    def test_no_op_when_not_on_item_page(self):
        """If page.url is not /item/<uuid>, do nothing (no exceptions)."""
        page = _make_page(url="https://app.speechify.com/library")
        with patch("speechify_add.browser._perform_delete", AsyncMock()) as pd:
            asyncio.get_event_loop().run_until_complete(
                _maybe_delete_partial_item(page)
            )
        pd.assert_not_awaited()

    def test_calls_perform_delete_when_on_item_page(self):
        """When stranded on /item/<uuid>, _perform_delete is invoked with that UUID."""
        uuid = "cff1772b-7603-4d46-966c-97b4b4566443"
        page = _make_page(url=f"https://app.speechify.com/item/{uuid}")
        with patch("speechify_add.browser._perform_delete", AsyncMock()) as pd:
            asyncio.get_event_loop().run_until_complete(
                _maybe_delete_partial_item(page)
            )
        pd.assert_awaited_once()
        assert pd.await_args[0][1] == uuid

    def test_swallows_cleanup_errors(self):
        """A failing cleanup must not raise — we're already in an error path."""
        uuid = "cff1772b-7603-4d46-966c-97b4b4566443"
        page = _make_page(url=f"https://app.speechify.com/item/{uuid}")
        boom = AsyncMock(side_effect=RuntimeError("cleanup failed"))
        with patch("speechify_add.browser._perform_delete", boom):
            # Must not raise
            asyncio.get_event_loop().run_until_complete(
                _maybe_delete_partial_item(page)
            )


# ---------------------------------------------------------------------------
# 12. BrowserSession.add_text — orphan-cleanup path (issue #39)
# ---------------------------------------------------------------------------

class TestFilenameFromTitle:
    """``_filename_from_title`` derives a filesystem-safe filename from
    the item title so Speechify shows a human-readable name in the
    library (its file-upload flow uses filename as the displayed title).
    """

    @pytest.mark.parametrize(
        "title,expected",
        [
            ("Simple Title", "Simple-Title"),
            ("With / slashes \\ and *bad* chars", "With-slashes-and-bad-chars"),
            ("Unicode — café", "Unicode-caf"),  # non-ASCII stripped
            ("Extra   whitespace  ", "Extra-whitespace"),
            ("...leading and trailing...", "leading-and-trailing"),
        ],
    )
    def test_sanitizes(self, title, expected):
        from speechify_add.browser import _filename_from_title

        assert _filename_from_title(title) == expected

    def test_falls_back_when_title_is_unprintable(self):
        from speechify_add.browser import _filename_from_title

        # Pure punctuation strips to empty — fallback to a sane default.
        assert _filename_from_title("////") == "speechify-add"
        assert _filename_from_title("") == "speechify-add"

    def test_truncates_long_titles(self):
        from speechify_add.browser import _MAX_FILENAME_LEN, _filename_from_title

        long_title = "a" * 200
        result = _filename_from_title(long_title)
        assert len(result) <= _MAX_FILENAME_LEN


# ---------------------------------------------------------------------------
# 13. Paste Text menu uses the bumped timeout (issue #39 resilience)
# ---------------------------------------------------------------------------

class TestPasteTextMenuTimeout:
    def test_timeout_constant_is_at_least_60s(self):
        """Issue #39: Playwright's implicit 30s click timeout was too tight.
        Ensure we use a generous explicit timeout."""
        assert PASTE_TEXT_MENU_TIMEOUT_MS >= 60_000


# ---------------------------------------------------------------------------
# 14. _open_paste_text_modal — entry-point selection (issue #41)
# ---------------------------------------------------------------------------

class TestOpenPasteTextModal:
    """The new Speechify Library UI replaces the "+ New" dropdown with a
    direct top-bar button. We try the new selector first; if it isn't
    visible, we fall back to the legacy "+ New" → menu flow.
    """

    def _run(self, coro):
        return asyncio.get_event_loop().run_until_complete(coro)

    def test_new_ui_first_skips_sidebar_click(self):
        """When add-text-button is visible, we click it directly and
        do NOT touch the legacy '+ New' sidebar button."""
        page = MagicMock()
        page.wait_for_timeout = AsyncMock()
        sidebar_clicked = []

        def locator_side_effect(selector):
            m = MagicMock()
            m.click = AsyncMock(
                side_effect=lambda: sidebar_clicked.append(selector),
            )
            m.wait_for = AsyncMock()
            m.first = m
            return m

        page.locator.side_effect = locator_side_effect

        result = self._run(_open_paste_text_modal(page))
        assert result == "toolbar"
        # Must not have clicked the legacy "+ New" sidebar button
        assert not any("sidebar-import-button" in s for s in sidebar_clicked)

    def test_falls_back_to_legacy_when_toolbar_missing(self):
        """When add-text-button never appears, we click '+ New' and
        then the menu item — preserving compatibility with the old UI."""
        page = MagicMock()
        page.wait_for_timeout = AsyncMock()

        clicked = []

        def locator_side_effect(selector):
            m = MagicMock()
            # Hide all add-text-* selectors (new UI)
            if any(s in selector for s in ("add-text-button", "Create Note")):
                m.wait_for = AsyncMock(side_effect=Exception("not visible"))
            else:
                m.wait_for = AsyncMock()
            m.click = AsyncMock(side_effect=lambda: clicked.append(selector))
            m.first = m
            return m

        page.locator.side_effect = locator_side_effect

        result = self._run(_open_paste_text_modal(page))
        assert result == "menu"
        assert any("sidebar-import-button" in s for s in clicked), \
            f"Expected '+ New' click in legacy fallback, got: {clicked}"

    def test_raises_step_skipped_when_neither_works(self):
        """Both new and legacy entry points missing → raise _StepSkipped
        so the caller dumps the DOM and surfaces a clear error."""
        page = MagicMock()
        page.wait_for_timeout = AsyncMock()

        def locator_side_effect(selector):
            m = MagicMock()
            # Sidebar click works (no error), but every paste-text
            # selector is invisible.
            if "sidebar-import-button" in selector:
                m.click = AsyncMock()
                m.wait_for = AsyncMock()
            else:
                m.wait_for = AsyncMock(side_effect=Exception("not visible"))
                m.click = AsyncMock()
            m.first = m
            return m

        page.locator.side_effect = locator_side_effect

        with pytest.raises(_StepSkipped):
            self._run(_open_paste_text_modal(page))


class TestEntryPointConstants:
    def test_add_text_selectors_have_data_testid_first(self):
        """The new-UI testid is the most reliable signal — try it first."""
        assert ADD_TEXT_BUTTON_SELECTORS[0] == '[data-testid="add-text-button"]'

    def test_add_text_timeout_is_short(self):
        """The toolbar visibility check must fail fast so the legacy
        fallback doesn't sit on it for 60s on every old-UI session."""
        assert ADD_TEXT_BUTTON_TIMEOUT_MS <= 10_000

    def test_paste_text_menu_selectors_kept(self):
        """Legacy menu selectors must still be present for fallback."""
        assert any("library-menu-item-paste-text" in s for s in PASTE_TEXT_MENU_SELECTORS)
