"""
Live tests for speechify_add/verify.py.

These tests hit real external services and are gated by @pytest.mark.live.
Run with: pytest -m live tests/test_verify_live.py

NOTE: search_library and search_library_batch require a real authenticated
Speechify browser session (chrome-hub + Xvfb). Per repo notes, do not run
Playwright/browser tests. Those functions have no live tests here.
"""

import pytest

from speechify_add.verify import get_page_title, parse_progress_pct


@pytest.mark.live
async def test_live_get_page_title_returns_string():
    """
    Hits a stable public URL (example.com) and verifies a non-empty title is returned.

    External services: HTTP GET to example.com
    Estimated cost/time: free, ~1s
    Requirements: outbound HTTP access
    """
    result = await get_page_title("https://example.com")
    assert result is not None
    assert len(result) > 0


@pytest.mark.live
async def test_live_get_page_title_invalid_domain_returns_none():
    """
    Requests a non-existent domain and verifies None is returned (no exception raised).

    External services: attempted HTTP GET to invalid host
    Estimated cost/time: free, ~5s (DNS timeout)
    Requirements: outbound network access
    """
    result = await get_page_title("https://this-domain-does-not-exist-xyz.invalid")
    assert result is None


@pytest.mark.live
def test_live_parse_progress_pct_against_known_format():
    """
    Validates parse_progress_pct against the exact format returned by the
    Speechify library JS evaluate block (e.g. "73% · web").

    External services: none (pure function — confirms format contract)
    Estimated cost/time: instant
    Requirements: none
    """
    # These strings are produced verbatim by the browser JS in search_library
    assert parse_progress_pct("73% · web") == 73
    assert parse_progress_pct("0% · pdf") == 0
    assert parse_progress_pct("100% · epub") == 100
