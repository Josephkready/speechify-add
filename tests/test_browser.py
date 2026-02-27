"""Tests for speechify_add.browser — login check, helpers, screenshot dir."""

import tests.conftest  # noqa: F401 — mock third-party deps

import asyncio
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

from speechify_add.browser import (
    _assert_logged_in,
    _click_first_visible,
    _find_first_visible,
    _save_screenshot,
    _StepSkipped,
)


class TestAssertLoggedIn(unittest.TestCase):
    """_assert_logged_in raises when the page URL indicates a login page."""

    def _make_page(self, url):
        page = MagicMock()
        page.url = url
        return page

    def test_passes_for_normal_url(self):
        _assert_logged_in(self._make_page("https://app.speechify.com/dashboard"))

    def test_raises_for_login(self):
        with self.assertRaises(RuntimeError) as ctx:
            _assert_logged_in(self._make_page("https://app.speechify.com/login"))
        self.assertIn("Session expired", str(ctx.exception))

    def test_raises_for_signin(self):
        with self.assertRaises(RuntimeError):
            _assert_logged_in(self._make_page("https://app.speechify.com/signin"))

    def test_raises_for_sign_in(self):
        with self.assertRaises(RuntimeError):
            _assert_logged_in(self._make_page("https://app.speechify.com/sign-in"))

    def test_raises_for_auth(self):
        with self.assertRaises(RuntimeError):
            _assert_logged_in(self._make_page("https://app.speechify.com/auth"))

    def test_passes_for_url_containing_login_in_path_segment(self):
        """e.g. /login-settings would trigger since current impl does substring match."""
        with self.assertRaises(RuntimeError):
            _assert_logged_in(self._make_page("https://app.speechify.com/login-settings"))


class TestStepSkipped(unittest.TestCase):

    def test_is_exception_subclass(self):
        self.assertTrue(issubclass(_StepSkipped, Exception))

    def test_carries_message(self):
        err = _StepSkipped("no element found")
        self.assertEqual(str(err), "no element found")


class TestFindFirstVisible(unittest.TestCase):
    """_find_first_visible tries selectors in order."""

    def test_returns_first_matching_locator(self):
        page = MagicMock()
        locator = MagicMock()
        locator.wait_for = AsyncMock()
        page.locator.return_value.first = locator

        result = asyncio.get_event_loop().run_until_complete(
            _find_first_visible(page, ['button[type="submit"]'], step="test", timeout=1000)
        )
        self.assertEqual(result, locator)

    def test_raises_step_skipped_when_none_found(self):
        page = MagicMock()
        locator = MagicMock()
        locator.wait_for = AsyncMock(side_effect=Exception("timeout"))
        page.locator.return_value.first = locator

        with self.assertRaises(_StepSkipped):
            asyncio.get_event_loop().run_until_complete(
                _find_first_visible(page, ["sel1", "sel2"], step="test", timeout=1000)
            )

    def test_tries_multiple_selectors(self):
        page = MagicMock()

        fail_loc = MagicMock()
        fail_loc.wait_for = AsyncMock(side_effect=Exception("timeout"))

        ok_loc = MagicMock()
        ok_loc.wait_for = AsyncMock()

        # First selector fails, second succeeds
        page.locator.side_effect = [
            MagicMock(first=fail_loc),
            MagicMock(first=ok_loc),
        ]

        result = asyncio.get_event_loop().run_until_complete(
            _find_first_visible(page, ["bad-sel", "good-sel"], step="test", timeout=2000)
        )
        self.assertEqual(result, ok_loc)


class TestClickFirstVisible(unittest.TestCase):

    def test_clicks_found_locator(self):
        page = MagicMock()
        locator = MagicMock()
        locator.wait_for = AsyncMock()
        locator.click = AsyncMock()
        page.locator.return_value.first = locator

        asyncio.get_event_loop().run_until_complete(
            _click_first_visible(page, ["button"], step="test", timeout=1000)
        )
        locator.click.assert_awaited_once()


class TestSaveScreenshot(unittest.TestCase):

    def test_creates_dir_and_calls_screenshot(self):
        with tempfile.TemporaryDirectory() as d:
            fake_dir = Path(d) / "screenshots"
            page = MagicMock()
            page.screenshot = AsyncMock()

            with patch("speechify_add.browser.SCREENSHOT_DIR", fake_dir):
                asyncio.get_event_loop().run_until_complete(
                    _save_screenshot(page, "test-shot")
                )

            self.assertTrue(fake_dir.exists())
            page.screenshot.assert_awaited_once()
            # Verify the correct filename was used
            call_kwargs = page.screenshot.call_args[1]
            self.assertIn("test-shot.png", call_kwargs["path"])


class TestAddUrlRaisesWithNoProfile(unittest.TestCase):
    """add_url raises RuntimeError when no browser profile exists."""

    def test_raises_when_profile_missing(self):
        from speechify_add import browser

        with tempfile.TemporaryDirectory() as d:
            missing_profile = Path(d) / "nonexistent"
            with patch("speechify_add.config.BROWSER_PROFILE_DIR", missing_profile):
                with self.assertRaises(RuntimeError) as ctx:
                    asyncio.get_event_loop().run_until_complete(
                        browser.add_url("http://test.com")
                    )
                self.assertIn("No browser profile", str(ctx.exception))


if __name__ == "__main__":
    unittest.main()
