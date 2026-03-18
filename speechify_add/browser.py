"""
Browser automation via Playwright persistent context.

Runs a headed (visible) Chromium window using the same persistent profile that
auth setup created, so it's already logged in. The window opens briefly,
performs the add, then closes automatically.

Headed mode is required because Speechify's "Paste Link" feature uses the real
system clipboard API, which only works correctly in a visible browser window.

BrowserSession keeps Playwright + Chromium alive across multiple operations
so that batch uploads don't pay the ~17s cold-start per file.
"""

import os
import subprocess
import time
from pathlib import Path

from . import config

SCREENSHOT_DIR = Path.home() / ".config" / "speechify-add" / "debug-screenshots"


# ---------------------------------------------------------------------------
# BrowserSession — reusable session for batch operations
# ---------------------------------------------------------------------------

class BrowserSession:
    """Async context manager that keeps a single Chromium instance alive.

    Usage:
        async with BrowserSession(debug=False) as session:
            await session.add_url("https://example.com/article1")
            await session.add_text("some text", title="My Doc")
            await session.add_url("https://example.com/article2")
    """

    def __init__(self, debug: bool = False):
        self.debug = debug
        self._playwright = None
        self._ctx = None
        self._page = None
        self._xvfb_proc = None
        self._console_errors: list[str] = []

    async def __aenter__(self):
        from playwright.async_api import async_playwright

        profile_dir = config.BROWSER_PROFILE_DIR
        if not profile_dir.exists():
            raise RuntimeError("No browser profile found. Run: speechify-add auth setup")

        _display, self._xvfb_proc = _ensure_display()

        self._pw_cm = async_playwright()
        self._playwright = await self._pw_cm.__aenter__()
        self._ctx = await self._playwright.chromium.launch_persistent_context(
            str(profile_dir),
            headless=False,
            args=["--disable-blink-features=AutomationControlled"],
            permissions=["clipboard-read", "clipboard-write"],
        )
        self._page = await self._ctx.new_page()
        self._page.on("pageerror", lambda err: self._console_errors.append(str(err)))

        await self._page.goto("https://app.speechify.com", wait_until="load", timeout=60_000)
        await self._page.locator('[data-testid="sidebar-import-button"]').wait_for(
            state="visible", timeout=15_000
        )
        await self._page.wait_for_timeout(1_000)
        _assert_logged_in(self._page)

        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        if self._ctx:
            await self._ctx.close()
        if self._pw_cm:
            await self._pw_cm.__aexit__(exc_type, exc_val, exc_tb)
        if self._xvfb_proc is not None:
            self._xvfb_proc.terminate()
            self._xvfb_proc.wait()
        return False

    async def _navigate_to_library(self):
        """Navigate back to the Speechify library between operations."""
        await self._page.goto("https://app.speechify.com", wait_until="load", timeout=60_000)
        await self._page.locator('[data-testid="sidebar-import-button"]').wait_for(
            state="visible", timeout=15_000
        )
        await self._page.wait_for_timeout(1_000)

    async def add_url(self, url: str) -> None:
        """Add a URL via the Paste Link flow, reusing the open browser."""
        self._console_errors.clear()

        if self.debug:
            await _save_screenshot(self._page, "batch-url-01-before")

        await _perform_add(self._page, url, debug=self.debug)

        crashed = await self._page.locator("text=Application error").count()
        if crashed > 0:
            raise RuntimeError(
                f"Speechify app crashed after Paste Link.\n"
                f"Page errors: {self._console_errors[:3]}"
            )

        # Navigate back to library for the next operation
        await self._navigate_to_library()

    async def delete_item(self, item_id: str) -> None:
        """Delete an item from the Speechify library by its UUID."""
        page = self._page

        if self.debug:
            await _save_screenshot(page, "delete-01-before")

        # Navigate to the item page
        await page.goto(
            f"https://app.speechify.com/item/{item_id}",
            wait_until="load",
            timeout=60_000,
        )
        await page.wait_for_timeout(2_000)

        if self.debug:
            await _save_screenshot(page, "delete-02-item-page")

        # Look for a three-dot / more menu button
        try:
            more_btn = await _find_first_visible(page, [
                '[data-testid*="more"]',
                '[data-testid*="menu"]',
                '[aria-label*="More"]',
                '[aria-label*="more"]',
                '[aria-label*="Options"]',
                '[aria-label*="options"]',
                'button[aria-haspopup]',
                'button[aria-haspopup="menu"]',
                '[data-testid*="kebab"]',
                '[data-testid*="ellipsis"]',
            ], step="more/menu button", timeout=8_000)
            await more_btn.click()
            await page.wait_for_timeout(1_000)

            if self.debug:
                await _save_screenshot(page, "delete-03-menu-open")
        except _StepSkipped:
            # No menu button found — delete button might be directly visible
            pass

        # Click the delete/remove option
        await _click_first_visible(page, [
            '[data-testid*="delete"]',
            '[data-testid*="Delete"]',
            '[data-testid*="trash"]',
            '[data-testid*="remove"]',
            'button:has-text("Delete")',
            'button:has-text("Remove")',
            '[role="menuitem"]:has-text("Delete")',
            '[role="menuitem"]:has-text("Remove")',
            'a:has-text("Delete")',
            'div:has-text("Delete"):not(:has(div:has-text("Delete")))',
        ], step="delete button", timeout=8_000)
        await page.wait_for_timeout(1_000)

        if self.debug:
            await _save_screenshot(page, "delete-04-after-delete-click")

        # Handle confirmation dialog if one appears
        try:
            await _click_first_visible(page, [
                '[data-testid*="confirm"]',
                '[data-testid*="delete-confirm"]',
                'button:has-text("Delete")',
                'button:has-text("Confirm")',
                'button:has-text("Yes")',
                '[role="dialog"] button:has-text("Delete")',
                '[role="dialog"] button:has-text("Confirm")',
                '[role="alertdialog"] button:has-text("Delete")',
            ], step="confirm deletion", timeout=5_000)
        except _StepSkipped:
            # No confirmation dialog — deletion may have proceeded directly
            pass

        await page.wait_for_timeout(2_000)

        if self.debug:
            await _save_screenshot(page, "delete-05-done")

        # Verify we got redirected back to library (or item is gone)
        # The page should no longer be on the item URL
        if f"/item/{item_id}" in page.url:
            # Check if there's an error or "not found" indicator
            not_found = await page.locator("text=not found").count()
            gone = await page.locator("text=deleted").count()
            if not_found == 0 and gone == 0:
                raise RuntimeError(
                    f"Deletion may have failed — still on item page: {page.url}"
                )

        # Navigate back to library for any subsequent operations
        await self._navigate_to_library()

    async def add_text(self, text: str, title: str = "") -> str:
        """Add raw text via the Paste Text flow, reusing the open browser."""
        page = self._page

        if self.debug:
            await _save_screenshot(page, "batch-text-01-before")

        # Open "New" menu
        await page.locator('[data-testid="sidebar-import-button"]').click()
        await page.wait_for_timeout(600)

        # Click "Paste Text"
        await page.locator('[data-testid="library-menu-item-paste-text"]').click()
        await page.wait_for_timeout(1_000)

        if self.debug:
            await _save_screenshot(page, "batch-text-02-modal")

        # Fill title and text
        if title:
            await page.locator('input[placeholder="Optional"]').fill(title)
        # Use React-compatible JS setter — .fill() times out on large text (>100K chars)
        textarea = page.locator('textarea[placeholder="Type or paste text here"]')
        await textarea.evaluate(
            """(el, val) => {
                const setter = Object.getOwnPropertyDescriptor(
                    window.HTMLTextAreaElement.prototype, 'value'
                ).set;
                setter.call(el, val);
                el.dispatchEvent(new Event('input', {bubbles: true}));
            }""",
            text,
        )
        await page.wait_for_timeout(500)

        # Click "Save File"
        await page.locator('[data-testid="add-text-save-button"]').click()

        # Wait for redirect to /item/<uuid>
        doc_url = ""
        for _ in range(30):
            await page.wait_for_timeout(1_000)
            if "/item/" in page.url:
                doc_url = page.url
                break

        if not doc_url:
            raise RuntimeError(
                "Timed out waiting for Speechify to process the text. "
                f"Final URL: {page.url}"
            )

        if self.debug:
            await _save_screenshot(page, "batch-text-03-done")

        # Navigate back to library for the next operation
        await self._navigate_to_library()

        return doc_url


def _ensure_display():
    """
    Return (display_str, xvfb_proc).

    If DISPLAY or WAYLAND_DISPLAY is already set, return it with proc=None.
    Otherwise start a virtual Xvfb display and return its :N string and the
    process handle (so the caller can clean it up).
    """
    display = os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY")
    if display:
        return display, None

    # No display — spin up Xvfb
    display_num = 99
    proc = subprocess.Popen(
        ["Xvfb", f":{display_num}", "-screen", "0", "1920x1080x24", "-ac"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    time.sleep(0.8)  # Give Xvfb time to initialise
    os.environ["DISPLAY"] = f":{display_num}"
    return f":{display_num}", proc


async def add_text(text: str, title: str = "", debug: bool = False) -> str:
    """
    Add raw text to Speechify via the "Paste Text" UI flow.
    Returns the Speechify document URL (e.g. https://app.speechify.com/item/<uuid>).
    """
    async with BrowserSession(debug=debug) as session:
        return await session.add_text(text, title=title)


async def add_url(url: str, debug: bool = False) -> None:
    """Add a URL to Speechify via the browser "Paste Link" flow."""
    async with BrowserSession(debug=debug) as session:
        await session.add_url(url)


async def delete_item(item_id: str, debug: bool = False) -> None:
    """Delete an item from the Speechify library by its UUID."""
    async with BrowserSession(debug=debug) as session:
        await session.delete_item(item_id)


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
    if any(s in page.url for s in ("/login", "/signin", "/sign-in", "/auth")):
        raise RuntimeError("Session expired. Run: speechify-add auth setup")


async def _perform_add(page, url: str, debug: bool = False) -> None:
    # ── Write URL to the real system clipboard ────────────────────────────
    # In headed mode the real clipboard API works; Speechify's "Paste Link"
    # reads from it.  We write here AND keep it in window.__clipboardUrl as
    # a belt-and-suspenders fallback.
    await page.evaluate("val => navigator.clipboard.writeText(val)", url)
    await page.evaluate("val => { window.__clipboardUrl = val }", url)

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
