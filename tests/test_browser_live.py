"""
Live tests for speechify_add/browser.py

These require a real chrome-hub instance and a logged-in Speechify session.
Run with: pytest -m live tests/test_browser_live.py

NOTE: Per repo notes, do NOT run Playwright or browser tests in CI.
      These are intentionally skipped in normal test runs.
"""
import pytest

# ---------------------------------------------------------------------------
# _assert_logged_in is pure logic — no live tests needed; covered in unit tests.
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# BrowserSession live smoke test
# ---------------------------------------------------------------------------

@pytest.mark.live
class TestBrowserSessionLive:
    def test_browser_session_requires_chrome_hub(self):
        """
        Documents that BrowserSession depends on chrome-hub being available.

        External services: chrome-hub (local CDP proxy), Speechify (app.speechify.com)
        Cost/time: ~5s, no API charges, requires logged-in Chrome profile
        Environment: chrome-hub must be running; Speechify session must be active

        This is a placeholder — actual session tests require a live browser
        and are not safe to automate without a sandboxed test account.
        """
        # The real test would be:
        #   async with BrowserSession() as session:
        #       assert session._page is not None
        #
        # Skipped here because:
        # 1. Requires chrome-hub to be running (local service)
        # 2. Requires a logged-in Speechify session
        # 3. Per repo notes: do not run Playwright/browser tests
        pytest.skip(
            "Requires chrome-hub + live Speechify session — run manually only"
        )
