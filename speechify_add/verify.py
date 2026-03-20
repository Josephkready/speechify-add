"""
Verify that an article is present in the Speechify library.

Uses the library's search feature to find items matching a query.
If a URL is passed, fetches the page title to use as the search term.
"""

import html as _html
import re

import httpx

from . import config


async def search_library(query: str) -> list[dict]:
    """
    Search the Speechify library for items matching `query`.
    Returns a list of matching items: [{"title": ..., "meta": ...}, ...]
    """
    from playwright.async_api import async_playwright

    profile_dir = config.BROWSER_PROFILE_DIR
    if not profile_dir.exists():
        raise RuntimeError("No browser profile found. Run: speechify-add auth setup")

    async with async_playwright() as p:
        ctx = await p.chromium.launch_persistent_context(
            str(profile_dir),
            headless=True,
            args=["--disable-blink-features=AutomationControlled"],
        )
        try:
            page = await ctx.new_page()
            await page.goto("https://app.speechify.com", wait_until="load", timeout=60_000)

            # Wait for the library to finish loading (real items, not skeletons)
            await page.locator('[data-testid="sidebar-import-button"]').wait_for(
                state="visible", timeout=15_000
            )
            await page.wait_for_timeout(2_000)

            # Open the search bar
            await page.locator('[data-testid="library-search-toggle-button"]').click()
            await page.wait_for_timeout(500)

            search_input = page.locator('[data-testid="library-search-input"]')
            await search_input.wait_for(state="visible", timeout=5_000)
            await search_input.fill(query)
            await page.wait_for_timeout(2_000)  # wait for results to filter

            # Collect all visible library items
            items = await page.evaluate("""
                () => {
                    const results = [];
                    for (const btn of document.querySelectorAll('button')) {
                        const text = btn.innerText?.trim();
                        // Library items contain "0%" or "100%" progress + a type tag
                        if (text && /\\d+%/.test(text) && /(web|pdf|txt|epub|mp3)/.test(text)) {
                            const lines = text.split('\\n').map(s => s.trim()).filter(Boolean);
                            results.push({
                                title: lines[0] || '',
                                meta: lines.slice(1).join(' · ')
                            });
                        }
                    }
                    return results;
                }
            """)

            return items

        finally:
            await ctx.close()


async def get_page_title(url: str) -> str | None:
    """Fetch the <title> of a URL to use as a search term."""
    try:
        async with httpx.AsyncClient(follow_redirects=True, timeout=10) as client:
            resp = await client.get(url, headers={"User-Agent": "Mozilla/5.0"})
            match = re.search(r"<title[^>]*>(.*?)</title>", resp.text, re.IGNORECASE | re.DOTALL)
            if match:
                title = " ".join(match.group(1).split())
                return _html.unescape(title)
    except Exception:
        pass
    return None
