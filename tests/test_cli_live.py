"""Live tests for speechify_add/cli.py

These tests make real network calls. Run with:
    pytest -m live tests/test_cli_live.py

Repo note: Do NOT run Playwright or browser tests. Live tests here are
limited to pure-HTTP functions (_precheck_url, _fetch_google_doc_text).
"""
import asyncio

import pytest

from speechify_add.cli import _fetch_google_doc_text, _precheck_url


def run(coro):
    return asyncio.run(coro)


# ---------------------------------------------------------------------------
# _precheck_url — real HTTP HEAD request
# ---------------------------------------------------------------------------

@pytest.mark.live
def test_precheck_url_public_page_does_not_raise():
    """
    Hits example.com with a real HTTP HEAD request.
    No auth required, no cost. ~1s, requires network.
    Confirms that a publicly accessible URL passes the precheck without raising.
    """
    run(_precheck_url("https://example.com/"))


@pytest.mark.live
def test_precheck_url_returns_none_on_success():
    """
    Hits httpbin.org/status/200 (real HTTP 200 response).
    No cost, ~1s, requires network.
    Confirms the function returns None (not a truthy value) on success.
    """
    result = run(_precheck_url("https://httpbin.org/status/200"))
    assert result is None


# ---------------------------------------------------------------------------
# _fetch_google_doc_text — real Google Docs export
# ---------------------------------------------------------------------------

# Public Google Doc used for testing — read-only, publicly shared
_PUBLIC_GDOC = (
    "https://docs.google.com/document/d/"
    "1BxiMVs0XRA5nFMdKvBdBZjgmUUqptlbs74OgVE2upms/edit"
)


@pytest.mark.live
def test_fetch_google_doc_text_public_doc_returns_text():
    """
    Exports a real public Google Doc as plain text.
    Hits docs.google.com export endpoint — one free call, ~2s, requires network.
    The document (Google Sheets example) is publicly readable.
    Confirms the export endpoint returns non-empty text content.
    """
    text = run(_fetch_google_doc_text(_PUBLIC_GDOC))
    assert isinstance(text, str)
    assert len(text) > 0
