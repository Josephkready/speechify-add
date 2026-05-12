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


# NOTE: A regression test attempting to assert "paste-text ALWAYS fails
# to persist content blobs" was dropped from this PR. Empirical evidence
# shows the failure is *intermittent*, not consistent — paste-text
# sometimes persists content correctly. The architectural fix (route
# add_text through add_file) still stands because file-upload is
# observed to be reliable where paste-text is not. A statistical
# reliability test (paste-text persistence rate over N attempts) would
# be the right shape for a follow-up regression guard but is out of
# scope here.
