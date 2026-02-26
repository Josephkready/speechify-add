"""
Approach 2: Browser automation via Playwright.

Drives a persistent Chromium context (logged in after auth setup) to add
a URL through the actual Speechify UI. Slower than API replay (~5-15s) but
robust to backend API changes — it uses the same interface a human would.

Selector strategy (most → least stable):
  1. data-testid attributes
  2. ARIA role + accessible name
  3. Placeholder / label text
  4. CSS class (last resort — avoided where possible)
"""

import asyncio

from . import config


async def add_url(url: str) -> None:
    from playwright.async_api import async_playwright

    profile_dir = config.BROWSER_PROFILE_DIR
    if not profile_dir.exists():
        raise RuntimeError(
            "No browser profile found. Run: speechify-add auth setup"
        )

    async with async_playwright() as p:
        ctx = await p.chromium.launch_persistent_context(
            str(profile_dir),
            headless=True,
            args=["--disable-blink-features=AutomationControlled"],
        )
        try:
            page = await ctx.new_page()
            await page.goto(
                "https://app.speechify.com",
                wait_until="networkidle",
                timeout=30_000,
            )
            _assert_logged_in(page)
            await _perform_add(page, url)
        finally:
            await ctx.close()


def _assert_logged_in(page):
    if any(fragment in page.url for fragment in ("/login", "/signin", "/sign-in", "/auth")):
        raise RuntimeError(
            "Browser session has expired. Run: speechify-add auth setup"
        )


async def _perform_add(page, url: str) -> None:
    """
    Walk through the add-URL flow. Each step tries a list of selectors in
    order, stopping at the first one that's visible. This makes the code
    resilient to minor UI changes — only a single selector needs to still
    work for the step to succeed.
    """

    # ── Step 1: Open the add / import dialog ────────────────────────────
    await _click_first_visible(page, [
        '[data-testid*="add"]',
        '[data-testid*="import"]',
        '[data-testid*="create"]',
        'button[aria-label*="Add"]',
        'button[aria-label*="add"]',
        'button[aria-label*="Import"]',
        'button:has-text("Add")',
        'button:has-text("+")',
        'button:has-text("New")',
    ], step="open add dialog")

    await page.wait_for_timeout(600)

    # ── Step 2: If there's a "URL" option to select, click it ───────────
    # Some UIs show a picker (URL / File / Text / etc.) after the first click.
    # We try to click a "URL" option but don't fail if it's not found —
    # maybe the dialog lands directly on the URL input.
    try:
        await _click_first_visible(page, [
            '[data-testid*="url"]',
            '[data-testid*="link"]',
            'button:has-text("URL")',
            'button:has-text("Link")',
            'button:has-text("Web URL")',
            'text="URL"',
            'text="From URL"',
        ], step="select URL option", timeout=2_000)
        await page.wait_for_timeout(400)
    except _StepSkipped:
        pass

    # ── Step 3: Fill the URL input ───────────────────────────────────────
    input_locator = await _find_first_visible(page, [
        'input[placeholder*="URL"]',
        'input[placeholder*="url"]',
        'input[placeholder*="http"]',
        'input[placeholder*="paste"]',
        'input[placeholder*="Paste"]',
        'input[placeholder*="Enter"]',
        'input[type="url"]',
        'textarea[placeholder*="URL"]',
    ], step="URL input field")

    await input_locator.fill(url)
    await page.wait_for_timeout(300)

    # ── Step 4: Submit ───────────────────────────────────────────────────
    try:
        await _click_first_visible(page, [
            '[data-testid*="submit"]',
            '[data-testid*="confirm"]',
            'button[type="submit"]',
            'button:has-text("Add")',
            'button:has-text("Save")',
            'button:has-text("Import")',
            'button:has-text("Confirm")',
            'button:has-text("Done")',
        ], step="submit button")
    except _StepSkipped:
        # No visible submit button found — try pressing Enter
        await input_locator.press("Enter")

    # Brief pause to let the request fire before we close the context
    await page.wait_for_timeout(1_500)


# ---------------------------------------------------------------------------
# Selector helpers
# ---------------------------------------------------------------------------

class _StepSkipped(Exception):
    pass


async def _click_first_visible(page, selectors: list, step: str, timeout: int = 4_000):
    locator = await _find_first_visible(page, selectors, step, timeout)
    await locator.click()


async def _find_first_visible(page, selectors: list, step: str, timeout: int = 4_000):
    for selector in selectors:
        try:
            loc = page.locator(selector).first
            await loc.wait_for(state="visible", timeout=timeout // len(selectors))
            return loc
        except Exception:
            continue

    raise _StepSkipped(
        f"Could not find a visible element for step '{step}'. "
        f"Tried: {selectors}"
    )
