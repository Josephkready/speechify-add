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
    _find_first_visible,
    _click_first_visible,
    _StepSkipped,
    BrowserSession,
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
    def test_raises_on_timeout_no_item_url(self):
        """RuntimeError raised when page never redirects to /item/ URL."""
        session = BrowserSession()
        page = _make_page(url="https://app.speechify.com")
        session._page = page

        page.locator = MagicMock()
        page.wait_for_timeout = AsyncMock()
        page.goto = AsyncMock()

        def locator_side_effect(selector):
            m = MagicMock()
            m.wait_for = AsyncMock()
            m.click = AsyncMock()
            m.fill = AsyncMock()
            m.evaluate = AsyncMock()
            m.first = m
            return m

        page.locator.side_effect = locator_side_effect

        with pytest.raises(RuntimeError, match="Timed out waiting for Speechify"):
            asyncio.get_event_loop().run_until_complete(
                session.add_text("some text", title="My Title")
            )

    def test_returns_doc_url_when_redirected(self):
        """Returns the /item/ URL once page redirects there."""
        session = BrowserSession()
        page = _make_page(url="https://app.speechify.com")
        session._page = page

        call_count = 0

        async def wait_for_timeout_side(_ms):
            nonlocal call_count
            call_count += 1
            if call_count >= 2:
                page.url = "https://app.speechify.com/item/abc-123"

        page.wait_for_timeout = wait_for_timeout_side
        page.goto = AsyncMock()

        def locator_side_effect(selector):
            m = MagicMock()
            m.wait_for = AsyncMock()
            m.click = AsyncMock()
            m.fill = AsyncMock()
            m.evaluate = AsyncMock()
            m.first = m
            return m

        page.locator.side_effect = locator_side_effect

        result = asyncio.get_event_loop().run_until_complete(
            session.add_text("hello world")
        )
        assert "/item/" in result
        assert result == "https://app.speechify.com/item/abc-123"


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

            with patch("speechify_add.browser.async_new_page", return_value=cm), \
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

    def test_integration_add_text_title_passed_to_fill(self):
        """When a title is provided, it is passed to input.fill()."""
        async def run():
            session = BrowserSession()
            page = _make_page(url="https://app.speechify.com")
            session._page = page

            fill_calls = []

            async def wait_for_timeout_side(_ms):
                page.url = "https://app.speechify.com/item/doc-999"

            page.wait_for_timeout = wait_for_timeout_side
            page.goto = AsyncMock()

            def locator_side_effect(selector):
                m = MagicMock()
                m.wait_for = AsyncMock()
                m.click = AsyncMock()
                m.evaluate = AsyncMock()
                m.first = m

                async def fill_side(val):
                    fill_calls.append((selector, val))

                m.fill = fill_side
                return m

            page.locator.side_effect = locator_side_effect

            await session.add_text("body text", title="My Custom Title")

            title_fills = [(s, v) for s, v in fill_calls if 'Optional' in s]
            assert any("My Custom Title" in v for _, v in title_fills)

        asyncio.get_event_loop().run_until_complete(run())
