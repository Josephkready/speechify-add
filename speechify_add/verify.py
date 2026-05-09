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

from chrome_hub import async_new_page

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


async def verify_item_url(item_id: str) -> tuple[bool, str]:
    """Confirm the Speechify item at /item/<item_id> renders real content.

    Returns ``(ok, message)``. This is the reliable verification path for
    freshly-uploaded items: Speechify's library search has indexing
    latency (observed: 25+ minutes during the issue #45 investigation),
    so a search by title can return zero matches for an item that
    actually exists. Going directly to the item URL bypasses the search
    layer entirely.
    """
    item_url = f"https://app.speechify.com/item/{item_id}"
    log.debug("verify_item_url: %s", item_url)
    async with async_new_page() as page:
        await page.goto(item_url, wait_until="load", timeout=30_000)
        await page.wait_for_timeout(3_000)
        if "/item/" not in page.url:
            return False, (
                f"redirected away from item page to {page.url} — "
                "likely 404 / not logged in"
            )
        body = await page.evaluate("() => document.body.innerText || ''")
        if _ITEM_NOT_FOUND_PHRASE in body:
            return False, (
                f"page shows the {_ITEM_NOT_FOUND_PHRASE!r} overlay — "
                "item does not exist or is inaccessible"
            )
        if len(body) < 200:
            return False, (
                f"body content is only {len(body)} chars — "
                "item exists but appears empty / broken"
            )
        return True, f"body has {len(body)} chars of content"


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
