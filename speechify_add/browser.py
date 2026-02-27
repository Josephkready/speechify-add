"""
Browser automation via Playwright persistent context.

The persistent Chromium profile (written during auth setup) stays logged in
indefinitely — no token expiry, no refresh logic. This is the primary and
default add method.

Run `speechify-add debug` to take screenshots at each step and diagnose
selector issues when the UI changes.
"""

import asyncio
from pathlib import Path

from . import config

SCREENSHOT_DIR = Path.home() / ".config" / "speechify-add" / "debug-screenshots"


async def add_url(url: str, debug: bool = False) -> None:
    from playwright.async_api import async_playwright

    profile_dir = config.BROWSER_PROFILE_DIR
    if not profile_dir.exists():
        raise RuntimeError("No browser profile found. Run: speechify-add auth setup")

    async with async_playwright() as p:
        ctx = await p.chromium.launch_persistent_context(
            str(profile_dir),
            headless=True,
            args=["--disable-blink-features=AutomationControlled"],
            permissions=["clipboard-read", "clipboard-write"],
        )
        try:
            page = await ctx.new_page()
            await page.goto("https://app.speechify.com", wait_until="load", timeout=60_000)
            # TODO: Replace fixed wait_for_timeout with event-driven waits
            # (e.g., wait for a known dashboard element to appear)
            await page.wait_for_timeout(3_000)

            if debug:
                await _save_screenshot(page, "01-page-loaded")

            _assert_logged_in(page)
            await _perform_add(page, url, debug=debug)
        except Exception:
            if debug:
                await _save_screenshot(page, "error-state")
            raise
        finally:
            await ctx.close()


async def screenshot_walkthrough() -> Path:
    """
    Open the browser, load Speechify, take screenshots of the initial state
    and every element on the page useful for selector debugging.
    Returns the screenshot directory path.
    """
    from playwright.async_api import async_playwright

    SCREENSHOT_DIR.mkdir(parents=True, exist_ok=True)

    async with async_playwright() as p:
        ctx = await p.chromium.launch_persistent_context(
            str(config.BROWSER_PROFILE_DIR),
            headless=True,
            args=["--disable-blink-features=AutomationControlled"],
        )
        page = await ctx.new_page()
        await page.goto("https://app.speechify.com", wait_until="load", timeout=60_000)
        await page.wait_for_timeout(3_000)

        # Full page screenshot
        path1 = SCREENSHOT_DIR / "01-initial.png"
        await page.screenshot(path=str(path1), full_page=True)

        # Dump all buttons and inputs to a text file for selector analysis
        elements = await page.evaluate("""
            () => {
                const results = [];
                for (const el of document.querySelectorAll('button, input, [role="button"], a[href]')) {
                    results.push({
                        tag: el.tagName,
                        text: el.innerText?.trim().slice(0, 80),
                        ariaLabel: el.getAttribute('aria-label'),
                        testId: el.getAttribute('data-testid'),
                        placeholder: el.getAttribute('placeholder'),
                        type: el.getAttribute('type'),
                        id: el.id,
                        classes: el.className?.slice(0, 80),
                    });
                }
                return results;
            }
        """)

        dump_path = SCREENSHOT_DIR / "elements.txt"
        with open(dump_path, "w") as f:
            for el in elements:
                if any(el.get(k) for k in ("text", "ariaLabel", "testId", "placeholder")):
                    f.write(str(el) + "\n")

        async def _dump_elements(filename):
            els = await page.evaluate("""
                () => {
                    const results = [];
                    for (const el of document.querySelectorAll('button, input, textarea, [role="button"], [role="dialog"] *')) {
                        const text = el.innerText?.trim().slice(0, 80);
                        const ai = el.getAttribute('aria-label');
                        const ti = el.getAttribute('data-testid');
                        const ph = el.getAttribute('placeholder');
                        if (text || ai || ti || ph) {
                            results.push({ tag: el.tagName, text, ariaLabel: ai, testId: ti, placeholder: ph });
                        }
                    }
                    return results;
                }
            """)
            with open(SCREENSHOT_DIR / filename, "w") as f:
                for el in els:
                    f.write(str(el) + "\n")

        # Step 1: click "New"
        try:
            loc = page.locator('[data-testid="sidebar-import-button"]').first
            await loc.wait_for(state="visible", timeout=3_000)
            await loc.click()
            await page.wait_for_timeout(800)
            await page.screenshot(path=str(SCREENSHOT_DIR / "02-after-click-new.png"), full_page=True)
            await _dump_elements("elements-post-click.txt")
        except Exception as e:
            with open(SCREENSHOT_DIR / "elements-post-click.txt", "w") as f:
                f.write(f"ERROR clicking sidebar-import-button: {e}\n")

        # Step 2: click "Paste Link"
        try:
            loc2 = page.locator('[data-testid="library-menu-item-paste-link"]').first
            await loc2.wait_for(state="visible", timeout=3_000)
            await loc2.click()
            await page.wait_for_timeout(800)
            await page.screenshot(path=str(SCREENSHOT_DIR / "03-after-paste-link.png"), full_page=True)
            await _dump_elements("elements-after-paste-link.txt")
        except Exception as e:
            with open(SCREENSHOT_DIR / "elements-after-paste-link.txt", "w") as f:
                f.write(f"ERROR clicking paste-link: {e}\n")

        await ctx.close()

    return SCREENSHOT_DIR


def _assert_logged_in(page):
    # TODO: Use exact path-segment matching instead of substring search to avoid
    # false positives on URLs like /login-settings or /authorize-device
    if any(s in page.url for s in ("/login", "/signin", "/sign-in", "/auth")):
        raise RuntimeError("Session expired. Run: speechify-add auth setup")


async def _perform_add(page, url: str, debug: bool = False) -> None:
    # ── Pre-load the URL into the clipboard ──────────────────────────────
    # "Paste Link" reads from the clipboard — we put the URL there first.
    await page.evaluate("(u) => navigator.clipboard.writeText(u)", url)

    # TODO: These data-testid selectors are fragile — Speechify UI changes
    # could break them. Consider adding a fallback selector chain or using
    # the debug command to update selectors when breakage occurs.

    # ── Step 1: open the "New" dropdown ──────────────────────────────────
    await page.locator('[data-testid="sidebar-import-button"]').click()
    await page.wait_for_timeout(600)

    if debug:
        await _save_screenshot(page, "02-after-new-click")

    # ── Step 2: click "Paste Link" ───────────────────────────────────────
    await page.locator('[data-testid="library-menu-item-paste-link"]').click()
    await page.wait_for_timeout(2_000)

    if debug:
        await _save_screenshot(page, "03-after-paste-link")

    # ── Step 3: if an input appears (not auto-submitted), fill and submit ─
    # Speechify may auto-submit if clipboard has a valid URL, or may show
    # a text field pre-filled with the clipboard content.
    try:
        input_loc = await _find_first_visible(page, [
            'input[placeholder*="URL"]',
            'input[placeholder*="url"]',
            'input[placeholder*="http"]',
            'input[placeholder*="paste"]',
            'input[placeholder*="Paste"]',
            'input[placeholder*="link"]',
            'input[type="url"]',
        ], step="URL input (optional)", timeout=3_000)

        # Clear and re-fill in case it didn't read from clipboard
        await input_loc.fill(url)
        await page.wait_for_timeout(300)

        if debug:
            await _save_screenshot(page, "04-url-filled")

        try:
            await _click_first_visible(page, [
                'button[type="submit"]',
                'button:has-text("Add")',
                'button:has-text("Save")',
                'button:has-text("Import")',
                'button:has-text("Confirm")',
                'button:has-text("Done")',
            ], step="submit", timeout=3_000)
        except _StepSkipped:
            await input_loc.press("Enter")

    except _StepSkipped:
        # Auto-submitted — nothing more to do
        pass

    # TODO: Detect success/failure from the UI (e.g., a toast notification or
    # new item appearing) instead of a fixed 2-second wait.
    await page.wait_for_timeout(2_000)

    if debug:
        await _save_screenshot(page, "05-final")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

class _StepSkipped(Exception):
    pass


async def _save_screenshot(page, name: str):
    SCREENSHOT_DIR.mkdir(parents=True, exist_ok=True)
    path = SCREENSHOT_DIR / f"{name}.png"
    await page.screenshot(path=str(path), full_page=True)


async def _click_first_visible(page, selectors, step, timeout=5_000):
    loc = await _find_first_visible(page, selectors, step, timeout)
    await loc.click()


async def _find_first_visible(page, selectors, step, timeout=5_000):
    per = max(500, timeout // len(selectors))
    for selector in selectors:
        try:
            loc = page.locator(selector).first
            await loc.wait_for(state="visible", timeout=per)
            return loc
        except Exception:
            continue
    raise _StepSkipped(f"No visible element for '{step}'. Tried: {selectors}")
