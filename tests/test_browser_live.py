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
async def test_live_paste_text_reliability_sampler():
    """Sample the reliability of the deprecated paste-text path (issue #51).

    Uploads ``N`` items via the SPA paste-text flow, then for each checks
    whether the body marker round-trips to a fresh browser context (no
    shared IndexedDB with the uploader). Logs the success rate at WARNING
    so it shows up in pytest output.

    Why a sampler instead of a hard regression guard:
    - Paste-text persistence is *intermittent*. Some uploads do persist
      content to Firebase Storage, others don't. A "must fail" assertion
      flakes; a "must succeed" assertion is the bug we're trying to
      detect; only a rate is meaningful, and that needs N attempts.
    - The test is informational, not a release-blocker. ``add_text``
      already routes through the file-upload path (issue #51 fix); this
      sampler exists to surface when paste-text starts working reliably
      enough to be worth reconsidering that workaround.

    Tail of the contract: assert that ``len(results) == N`` (test ran
    end-to-end), but make no claim about the success rate itself. Loud
    log lines carry the signal.

    Cleanup: every uploaded item is archived via ``api.delete_item`` —
    even ones that didn't render content for the fresh context. The
    Firestore record exists either way, so archive succeeds.
    """
    from playwright.async_api import async_playwright

    from chrome_hub.browser import CDP_URL
    from speechify_add.verify import _FRESH_AUTH_COOKIE_NAMES

    N = 5  # ~30-60s per attempt → ~3-5 min total

    async def _body_for_fresh_context(item_id: str, timeout_s: float = 45.0) -> str:
        """Open ``/item/<item_id>`` in a fresh BrowserContext and return
        the rendered body. We use marker-presence (not the verify
        threshold heuristic) for content detection: the threshold check
        falsely passes on auth-form flashes that have >150 chars of
        Speechify chrome but none of our body text."""
        async with async_playwright() as pw:
            cdp_browser = await pw.chromium.connect_over_cdp(CDP_URL)
            auth_cookies = []
            if cdp_browser.contexts:
                all_cookies = await cdp_browser.contexts[0].cookies(
                    ["https://app.speechify.com/", "https://speechify.com/"]
                )
                auth_cookies = [
                    c for c in all_cookies
                    if c.get("name") in _FRESH_AUTH_COOKIE_NAMES
                ]
            assert auth_cookies, (
                "no Speechify auth cookies in chrome-hub default context — "
                "this test cannot run without an authenticated chrome-hub session"
            )

            fresh_ctx = await cdp_browser.new_context()
            try:
                await fresh_ctx.add_cookies(auth_cookies)
                page = await fresh_ctx.new_page()
                try:
                    await page.goto(
                        f"https://app.speechify.com/item/{item_id}",
                        wait_until="load", timeout=30_000,
                    )
                    await page.wait_for_timeout(int(timeout_s * 1000))
                    return await page.evaluate(
                        "() => document.body ? document.body.innerText : ''"
                    )
                finally:
                    await page.close()
            finally:
                await fresh_ctx.close()

    item_ids: list[str] = []
    results: list[bool] = []

    for i in range(N):
        marker = _unique_marker()
        body_marker = f"PASTE-TEXT-SAMPLER-MARKER-{marker}-{i}"
        title = f"speechify-add paste-text sampler {marker}-{i}"
        text = (
            f"{body_marker}\n\nPaste-text reliability sample {i + 1}/{N}. "
            "If you see this in your library, the test left behind an "
            "un-archived item — safe to delete manually. "
            + ("Padding text. " * 20)
        )

        log.warning("paste-text sample %d/%d: uploading", i + 1, N)
        try:
            async with browser.async_new_page() as page:
                await browser._init_speechify_page(page)
                item_url = await browser._do_add_text(page, text, title=title)
        except Exception as e:
            log.warning("paste-text sample %d/%d: upload failed: %s", i + 1, N, e)
            results.append(False)
            continue

        item_id = browser._extract_item_id(item_url)
        if not item_id:
            log.warning(
                "paste-text sample %d/%d: upload returned no UUID (%r)",
                i + 1, N, item_url,
            )
            results.append(False)
            continue
        item_ids.append(item_id)

        try:
            body = await _body_for_fresh_context(item_id)
        except Exception as e:
            log.warning(
                "paste-text sample %d/%d: fresh-context fetch raised %s",
                i + 1, N, e,
            )
            results.append(False)
            continue

        persisted = body_marker in body
        results.append(persisted)
        log.warning(
            "paste-text sample %d/%d: %s (body had %d chars)",
            i + 1, N, "PERSISTED" if persisted else "BROKEN", len(body),
        )

    # Cleanup — archive every item created, even broken ones.
    for item_id in item_ids:
        try:
            await api.delete_item(item_id)
        except Exception as cleanup_err:
            log.warning(
                "paste-text sampler: cleanup of %s failed: %s. "
                "Manual: speechify-add delete %s --mode api",
                item_id, cleanup_err, item_id,
            )

    # Headline log line — pytest -v shows this so a human can spot it.
    success_rate = (sum(results) / len(results)) if results else 0.0
    log.warning(
        "paste-text reliability sampler: %d/%d persisted (%.0f%%) — "
        "issue #51 workaround is %s",
        sum(results), len(results), success_rate * 100,
        "still warranted" if success_rate < 1.0
        else "potentially removable; investigate",
    )

    # We only assert the test ran end-to-end. No claim on the rate —
    # paste-text behaviour varies, and a single run isn't statistically
    # informative on its own. See docstring.
    assert len(results) == N, (
        f"sampler aborted early: collected {len(results)}/{N} attempts"
    )
