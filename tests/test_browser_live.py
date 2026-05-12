"""
Live tests for the speechify-add upload + verify pipeline.

Gated behind ``@pytest.mark.live`` and skipped by default — run with:

    pytest -m live tests/test_browser_live.py

Requirements:
  - chrome-hub running on localhost:9222 with a logged-in Speechify session
  - The Speechify Firebase token captured via ``speechify-add auth setup``
    (the API delete in cleanup uses it)

Why this exists
---------------
Speechify's Library UI is rolling out a redesign that has already
broken two layers of speechify-add this week:

  - issue #41 — paste-text menu item replaced by a top-bar button
  - issue #45 — library search rows changed structure, breaking verify

Unit tests catch logic regressions but can't catch DOM changes. This
roundtrip test is the canary: if any link in upload → verify → delete
breaks, the test fails immediately on the next manual run, instead of
silently shipping unverifiable URLs to dailybrief.
"""
import logging
import time
import uuid as _uuid

import pytest

from speechify_add import api, browser, verify

log = logging.getLogger(__name__)


def _unique_marker() -> str:
    """A short, unique-per-run string to embed in the test article so it
    can't be confused with anything else in the user's library."""
    return f"{int(time.time())}-{_uuid.uuid4().hex[:8]}"


@pytest.mark.live
async def test_live_upload_verify_delete_roundtrip():
    """End-to-end: add text → verify the resulting item URL → delete.

    Issue #51: text now routes through the file-upload path because the
    paste-text path doesn't persist content blobs to Firebase Storage.
    This test exercises that routing (write tempfile → add_file →
    fresh-context verify happens internally → URL returned). A failure
    here means either the file-upload path itself broke (UI rotation)
    or the fresh-context verify caught a content-persistence regression.

    Cleanup uses ``api.delete_item`` (Firebase archive endpoint), which
    is independent of the browser-automation flow being tested.
    """
    marker = _unique_marker()
    title = f"speechify-add live roundtrip {marker}"
    text = (
        f"Automated speechify-add roundtrip test — marker {marker}. "
        "If you see this in your library after a test run, the live "
        "test failed during cleanup. Safe to delete."
    )

    log.info("upload: %s", title)
    # add_text now performs fresh-context verify internally and only
    # returns a URL after confirming the item is fetchable by sessions
    # that didn't do the upload. A successful return is the assertion.
    item_url = await browser.add_text(text, title=title)
    item_id = browser._extract_item_id(item_url)
    assert item_id, f"upload returned a URL with no item UUID: {item_url}"

    try:
        # Belt-and-braces: also run the in-session verify so any future
        # regression in the chrome-hub render path is visible too.
        ok, info = await verify.verify_item_url(item_id)
        assert ok, (
            f"verify_item_url returned False for {item_id} "
            f"(test article we just uploaded): {info}"
        )
    finally:
        try:
            await api.delete_item(item_id)
        except Exception as cleanup_err:
            pytest.fail(
                f"cleanup failed for {item_id}: {cleanup_err}. "
                "The test article is still in the library; delete it "
                f"manually with: speechify-add delete {item_id} --mode api"
            )


@pytest.mark.live
async def test_live_fresh_context_verify_catches_paste_text_regression():
    """Issue #51 regression guard: items uploaded via the deprecated
    paste-text path render only in the upload session's IndexedDB cache;
    fresh-context verify must detect this and return False.

    If this test ever returns True, paste-text has either been fixed
    upstream by Speechify or our cache-bypass detection has broken —
    investigate before claiming the bug is gone.
    """
    marker = _unique_marker()
    title = f"speechify-add paste-text regression check {marker}"
    text = (
        f"Marker {marker}. This article is uploaded via the deprecated "
        "paste-text path explicitly to confirm fresh-context verify "
        "catches the issue-51 failure mode. Safe to delete."
    )

    log.info("paste-text upload: %s", title)
    async with browser.async_new_page() as page:
        await browser._init_speechify_page(page)
        item_url = await browser._do_add_text(page, text, title=title)

    item_id = browser._extract_item_id(item_url)
    assert item_id, f"paste-text upload returned no UUID: {item_url}"

    try:
        ok, info = await verify.verify_item_url_fresh_context(
            item_id, max_wait=20.0,
        )
        assert not ok, (
            f"Fresh-context verify unexpectedly PASSED for paste-text item "
            f"{item_id}. Either issue #51 has been fixed upstream (great — "
            "remove this regression guard and the file-upload routing in "
            "add_text) or our cache-bypass detection broke. Verify info: "
            f"{info}"
        )
        # Sanity: the in-session verify should still pass — proves we're
        # comparing apples-to-apples (item exists, cache hit makes it look
        # fine from chrome-hub).
        ok_cached, info_cached = await verify.verify_item_url(item_id)
        assert ok_cached, (
            f"In-session verify also failed for paste-text item — "
            f"upload may not have completed at all: {info_cached}"
        )
    finally:
        try:
            await api.delete_item(item_id)
        except Exception as cleanup_err:
            pytest.fail(
                f"cleanup failed for {item_id}: {cleanup_err}. "
                f"Manual cleanup: speechify-add delete {item_id} --mode api"
            )
