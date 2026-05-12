"""
Verify that an article is present in the Speechify library.

Uses the library's search feature to find items matching a query.
If a URL is passed, fetches the page title to use as the search term.
"""

import html
import logging
import re
import time

import httpx
from playwright.async_api import async_playwright

from chrome_hub import async_new_page
from chrome_hub.browser import CDP_URL

log = logging.getLogger(__name__)


# JS evaluator that extracts library-item rows from the rendered DOM.
# Issue #45: Speechify rolled out a Library UI redesign — the old
# `<button>` rows with inline "73% · web" innerText are gone. The new
# rows are `<div role="button">` containing structural testids
# (library-item-title / library-item-progress / library-item-date /
# library-item-type). Items at 0% don't render the progress div, so
# we synthesize "0%" for backward-compat with parse_progress_pct.
_LIBRARY_ITEMS_JS = """
() => {
    const titles = document.querySelectorAll(
        '[data-testid="library-item-title"]'
    );
    return Array.from(titles).map(t => {
        const row = t.closest('[role="button"]')
            || t.closest('button')
            || t.parentElement;
        const progEl = row?.querySelector(
            '[data-testid="library-item-progress"]'
        );
        const dateEl = row?.querySelector(
            '[data-testid="library-item-date"]'
        );
        const typeEl = row?.querySelector(
            '[data-testid="library-item-type"]'
        );
        const progress = progEl ? progEl.innerText.trim() : '0%';
        const date = dateEl ? dateEl.innerText.trim() : '';
        const type_ = typeEl ? typeEl.innerText.trim() : '';
        const meta = [progress, date, type_].filter(Boolean).join(' ∙ ');
        return { title: (t.innerText || '').trim(), meta };
    });
}
"""


async def search_library(query: str) -> list[dict]:
    """
    Search the Speechify library for items matching `query`.
    Returns a list of matching items: [{"title": ..., "meta": ...}, ...]
    """
    log.debug("search_library: query=%r", query)
    t0 = time.perf_counter()
    async with async_new_page() as page:
        log.debug("search_library: got page in %.2fs", time.perf_counter() - t0)

        t1 = time.perf_counter()
        await page.goto("https://app.speechify.com", wait_until="load", timeout=60_000)
        log.debug("search_library: page.goto done (%.2fs)", time.perf_counter() - t1)

        t2 = time.perf_counter()
        await page.locator('[data-testid="sidebar-import-button"]').wait_for(
            state="visible", timeout=15_000
        )
        await page.wait_for_timeout(2_000)
        log.debug("search_library: library ready (%.2fs)", time.perf_counter() - t2)

        t3 = time.perf_counter()
        await page.locator('[data-testid="library-search-toggle-button"]').click()
        await page.wait_for_timeout(500)

        search_input = page.locator('[data-testid="library-search-input"]')
        await search_input.wait_for(state="visible", timeout=5_000)
        await search_input.fill(query)
        await page.wait_for_timeout(2_000)  # wait for results to filter
        log.debug("search_library: search filled+filtered (%.2fs)", time.perf_counter() - t3)

        items = await page.evaluate(_LIBRARY_ITEMS_JS)
        return items


def parse_progress_pct(meta: str) -> int | None:
    """Parse listen progress from a Speechify library item meta string.

    Examples:
        "73% · web"  -> 73
        "0% · pdf"   -> 0
        "100% · txt" -> 100
        ""           -> None
    """
    m = re.search(r"(\d+)%", meta)
    return int(m.group(1)) if m else None


async def search_library_batch(queries: list[str]) -> list[int | None]:
    """
    Search the Speechify library for multiple queries in a single browser session.

    Returns a list of listen percentages (0-100) in the same order as queries.
    Returns None for any query where no result was found.
    """
    async with async_new_page() as page:
        await page.goto("https://app.speechify.com", wait_until="load", timeout=60_000)
        await page.locator('[data-testid="sidebar-import-button"]').wait_for(
            state="visible", timeout=15_000
        )
        await page.wait_for_timeout(2_000)

        results: list[int | None] = []
        for query in queries:
            # Open search (toggle closes and re-opens cleanly between searches)
            await page.locator('[data-testid="library-search-toggle-button"]').click()
            await page.wait_for_timeout(300)
            search_input = page.locator('[data-testid="library-search-input"]')
            await search_input.wait_for(state="visible", timeout=5_000)
            await search_input.fill(query)
            await page.wait_for_timeout(2_000)

            items = await page.evaluate(_LIBRARY_ITEMS_JS)

            if items:
                pct = parse_progress_pct(items[0]["meta"])
                results.append(pct)
            else:
                results.append(None)

            # Close search bar before next iteration
            await page.locator('[data-testid="library-search-toggle-button"]').click()
            await page.wait_for_timeout(200)

        return results


# Phrase Speechify renders when an item URL is non-existent / inaccessible.
# Confirmed live: visiting /item/<bogus-uuid> shows
# "Oops! Something went wrong / Refresh the page or try again later /
# Return to My Library / Need help? Contact support" (~113 chars).
_ITEM_NOT_FOUND_PHRASE = "Oops! Something went wrong"


# Minimum body length we treat as "real content" on an item page. The
# "Oops! Something went wrong" stub is ~113 chars. Paywalled articles
# can produce short extractions (~185 chars rendered on the item page),
# so 150 gives a safe margin above the error stub while accepting them.
_PLAYABLE_MIN_BODY_CHARS = 150

# How long verify_item_url polls before giving up. Issue #47: the page
# can briefly render the "Oops!" overlay or a near-empty body for ~20s
# right after upload while Speechify finalizes the item server-side.
# 60s observed necessary for slow-rendering articles (185-char body at
# 30s that would settle to full content given more time).
_VERIFY_ITEM_MAX_WAIT_SEC = 60.0
# Interval between polls within that window.
_VERIFY_ITEM_POLL_INTERVAL_SEC = 2.0


async def verify_item_url(
    item_id: str, *, max_wait: float = _VERIFY_ITEM_MAX_WAIT_SEC,
) -> tuple[bool, str]:
    """Confirm the Speechify item at /item/<item_id> renders real content.

    Returns ``(ok, message)``. This is the reliable verification path for
    freshly-uploaded items: Speechify's library search has indexing
    latency (observed: 25+ minutes during the issue #45 investigation),
    so a search by title can return zero matches for an item that
    actually exists. Going directly to the item URL bypasses the search
    layer entirely.

    Issue #47: a fixed-wait probe races the post-upload render — fresh
    items can briefly show the "Oops!" overlay or a near-empty body
    before the page settles, especially under chrome-hub contention
    when verify_uploads runs in parallel. We poll instead of waiting a
    single fixed interval: healthy items pass on the first probe (~2s);
    fresh ones get up to ``max_wait`` to settle; truly missing items
    fail reliably because the overlay persists across every poll.
    """
    item_url = f"https://app.speechify.com/item/{item_id}"
    log.debug("verify_item_url: %s (max_wait=%.0fs)", item_url, max_wait)
    async with async_new_page() as page:
        await page.goto(item_url, wait_until="load", timeout=30_000)
        deadline = time.monotonic() + max_wait
        last_reason = "no checks completed before deadline"
        polls = 0
        while time.monotonic() < deadline:
            await page.wait_for_timeout(int(_VERIFY_ITEM_POLL_INTERVAL_SEC * 1000))
            polls += 1
            if "/item/" not in page.url:
                return False, (
                    f"redirected away from item page to {page.url} — "
                    "likely 404 / not logged in"
                )
            body = await page.evaluate("() => document.body.innerText || ''")
            if _ITEM_NOT_FOUND_PHRASE in body:
                last_reason = (
                    f"showing {_ITEM_NOT_FOUND_PHRASE!r} overlay "
                    f"(poll {polls})"
                )
                continue
            if len(body) < _PLAYABLE_MIN_BODY_CHARS:
                last_reason = (
                    f"body still only {len(body)} chars (poll {polls})"
                )
                continue
            return True, (
                f"body has {len(body)} chars of content "
                f"(settled after {polls} poll{'s' if polls != 1 else ''})"
            )
        return False, (
            f"item never became playable within {max_wait:.0f}s "
            f"({polls} polls; last: {last_reason})"
        )


# Issue #51: verify_item_url runs inside chrome-hub's default browser
# context, which also holds the IndexedDB caches written during upload.
# Broken items (Firestore record exists, Firebase Storage content blob
# missing) still render from those caches — so the in-session probe is
# blind to the bug class that produces unreadable URLs for other browsers.
# verify_item_url_fresh_context opens a clean BrowserContext on the same
# Chrome instance (no shared cookies / localStorage / IndexedDB), then
# transplants only the Speechify auth cookies so we can navigate as the
# logged-in user. This reproduces what a fresh tab on the user's phone
# sees: if the content blob isn't fetchable, the page renders the
# "Oops! Something went wrong" overlay and we fail closed.
_FRESH_AUTH_COOKIE_NAMES = {"session", "axwrt", "cf_clearance"}
_FRESH_CTX_MAX_WAIT_SEC = 30.0
_FRESH_CTX_POLL_INTERVAL_SEC = 2.0


async def verify_item_url_fresh_context(
    item_id: str, *, max_wait: float = _FRESH_CTX_MAX_WAIT_SEC,
) -> tuple[bool, str]:
    """Confirm /item/<item_id> renders for a session that did NOT do the upload.

    Connects to the same chrome-hub Chrome via CDP, but creates a fresh
    BrowserContext with no shared storage. Copies the Speechify auth
    cookies from chrome-hub's default context so the new context is
    logged in as the same user, then polls the item page the same way
    verify_item_url does.

    A False return here proves the item is unfetchable by ordinary
    user sessions — even though verify_item_url (which sees the upload
    session's IndexedDB cache) may still return True. See issue #51.

    Returns ``(ok, message)``. The caller is responsible for retrying
    on transient False if it's appropriate (post-upload settle).
    """
    item_url = f"https://app.speechify.com/item/{item_id}"
    log.debug(
        "verify_item_url_fresh_context: %s (max_wait=%.0fs)",
        item_url, max_wait,
    )

    async with async_playwright() as pw:
        browser = await pw.chromium.connect_over_cdp(CDP_URL)

        # Pull auth cookies off whichever default context chrome-hub set up.
        # contexts[0] is the shared session that owns the upload-time state.
        auth_cookies = []
        if browser.contexts:
            default_ctx = browser.contexts[0]
            all_cookies = await default_ctx.cookies(
                ["https://app.speechify.com/", "https://speechify.com/"]
            )
            auth_cookies = [
                c for c in all_cookies if c.get("name") in _FRESH_AUTH_COOKIE_NAMES
            ]
        if not auth_cookies:
            return False, (
                "no Speechify auth cookies available in chrome-hub default "
                "context — fresh-context verify cannot authenticate"
            )

        fresh_ctx = await browser.new_context()
        try:
            await fresh_ctx.add_cookies(auth_cookies)
            page = await fresh_ctx.new_page()
            try:
                await page.goto(item_url, wait_until="load", timeout=30_000)
                deadline = time.monotonic() + max_wait
                last_reason = "no checks completed before deadline"
                polls = 0
                while time.monotonic() < deadline:
                    await page.wait_for_timeout(
                        int(_FRESH_CTX_POLL_INTERVAL_SEC * 1000)
                    )
                    polls += 1
                    if "/item/" not in page.url:
                        return False, (
                            f"fresh context redirected from item page to "
                            f"{page.url} — auth cookie was rejected or item "
                            "does not exist server-side"
                        )
                    body = await page.evaluate(
                        "() => document.body.innerText || ''"
                    )
                    if _ITEM_NOT_FOUND_PHRASE in body:
                        last_reason = (
                            f"{_ITEM_NOT_FOUND_PHRASE!r} overlay (poll {polls}) "
                            "— Firestore record exists but content blob is "
                            "unfetchable in a fresh session (issue #51)"
                        )
                        continue
                    if len(body) < _PLAYABLE_MIN_BODY_CHARS:
                        last_reason = (
                            f"body still only {len(body)} chars (poll {polls})"
                        )
                        continue
                    return True, (
                        f"fresh-context body has {len(body)} chars of content "
                        f"(settled after {polls} poll{'s' if polls != 1 else ''})"
                    )
                return False, (
                    f"item never became playable in fresh context within "
                    f"{max_wait:.0f}s ({polls} polls; last: {last_reason})"
                )
            finally:
                await page.close()
        finally:
            await fresh_ctx.close()


async def get_page_title(url: str) -> str | None:
    """Fetch the <title> of a URL to use as a search term."""
    try:
        async with httpx.AsyncClient(follow_redirects=True, timeout=10) as client:
            resp = await client.get(url, headers={"User-Agent": "Mozilla/5.0"})
            match = re.search(r"<title[^>]*>(.*?)</title>", resp.text, re.IGNORECASE | re.DOTALL)
            if match:
                return html.unescape(match.group(1).strip())
    except Exception:
        pass
    return None
