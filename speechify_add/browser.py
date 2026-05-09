"""
Browser automation via chrome-hub shared Chrome instance.

Uses chrome-hub's ``async_new_page()`` to get a page in the shared Chrome
process (connected via CDP). This eliminates the ~17s cold-start that
launching a fresh Chromium instance required, and removes the need for
Xvfb display management — chrome-hub handles all of that.

BrowserSession keeps a single page open across multiple operations so that
batch uploads don't re-navigate between items.
"""

import asyncio
import logging
import re
import time
from pathlib import Path

from chrome_hub import async_new_page

log = logging.getLogger(__name__)

SCREENSHOT_DIR = Path.home() / ".config" / "speechify-add" / "debug-screenshots"

SUPPORTED_FILE_EXTS = frozenset({".pdf", ".epub", ".html", ".htm", ".txt"})

# Wait this long for the "Paste Text" menu item to become visible (legacy
# UI). The implicit Playwright default (30s) was too tight under load —
# see issue #39.
PASTE_TEXT_MENU_TIMEOUT_MS = 60_000

# How long to wait for the new-UI toolbar button before falling back to
# the legacy "+ New" menu flow. Short — if it's there, it's there.
ADD_TEXT_BUTTON_TIMEOUT_MS = 5_000

# New Speechify Library UI (issue #41): direct top-bar buttons replace
# the old "+ New → Paste Text" dropdown. `add-text-button` is "Create
# Note" and opens the same paste-text modal we already drive.
ADD_TEXT_BUTTON_SELECTORS = [
    '[data-testid="add-text-button"]',
    'button:has-text("Create Note")',
]

# Legacy Speechify UI: "+ New" sidebar button opens a dropdown menu,
# then a menu item opens the paste-text modal. Kept as a fallback during
# rollout — sessions still on the old UI will hit this path.
PASTE_TEXT_MENU_SELECTORS = [
    '[data-testid="library-menu-item-paste-text"]',
    '[role="menuitem"]:has-text("Paste Text")',
    '[role="menuitem"]:has-text("Paste text")',
    'button:has-text("Paste Text")',
    'button:has-text("Paste text")',
]

_ITEM_ID_RE = re.compile(
    r"/item/([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})",
    re.IGNORECASE,
)


def _extract_item_id(url: str | None) -> str | None:
    """Return the Speechify item UUID from a URL, or None if absent."""
    if not url:
        return None
    m = _ITEM_ID_RE.search(url)
    return m.group(1) if m else None


def _validate_file_path(path: Path) -> Path:
    """Raise if `path` isn't a readable file with a Speechify-supported extension."""
    if not path.exists():
        raise FileNotFoundError(f"File not found: {path}")
    if not path.is_file():
        raise ValueError(f"Not a file: {path}")
    if path.suffix.lower() not in SUPPORTED_FILE_EXTS:
        supported = ", ".join(sorted(SUPPORTED_FILE_EXTS))
        raise ValueError(
            f"Unsupported file type: {path.suffix}. Supported: {supported}"
        )
    return path


# ---------------------------------------------------------------------------
# Helpers for page initialization
# ---------------------------------------------------------------------------

async def _init_speechify_page(page):
    """Navigate to Speechify and wait for the app to be ready."""
    t0 = time.perf_counter()
    await page.goto("https://app.speechify.com", wait_until="load", timeout=60_000)
    log.debug("init: page.goto(load) done in %.2fs", time.perf_counter() - t0)
    t1 = time.perf_counter()
    await page.locator('[data-testid="sidebar-import-button"]').wait_for(
        state="visible", timeout=15_000
    )
    log.debug("init: sidebar visible in %.2fs", time.perf_counter() - t1)
    await page.wait_for_timeout(1_000)
    _assert_logged_in(page)
    log.debug("init: total %.2fs", time.perf_counter() - t0)


# ---------------------------------------------------------------------------
# BrowserSession — reusable session for batch operations
# ---------------------------------------------------------------------------

class BrowserSession:
    """Async context manager that keeps a single chrome-hub page alive.

    Usage:
        async with BrowserSession(debug=False) as session:
            await session.add_url("https://example.com/article1")
            await session.add_text("some text", title="My Doc")
            await session.add_url("https://example.com/article2")
    """

    def __init__(self, debug: bool = False):
        self.debug = debug
        self._page = None
        self._page_cm = None
        self._console_errors: list[str] = []

    async def __aenter__(self):
        self._page_cm = async_new_page()
        self._page = await self._page_cm.__aenter__()
        self._page.on("pageerror", lambda err: self._console_errors.append(str(err)))

        try:
            await _init_speechify_page(self._page)
        except Exception:
            await self._page_cm.__aexit__(None, None, None)
            raise
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        if self._page_cm:
            await self._page_cm.__aexit__(exc_type, exc_val, exc_tb)
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
        await _perform_delete(self._page, item_id, debug=self.debug)
        await self._navigate_to_library()

    async def add_text(self, text: str, title: str = "") -> str:
        """Add raw text via the Paste Text flow, reusing the open browser.

        On any failure mid-flow, attempts to delete a partial item if one was
        already created (URL is on /item/<uuid>) before re-raising — this
        keeps the user's library clean and stops the caller's retry path
        from picking up an orphaned half-state item (issue #39).
        """
        doc_url = await _add_text_with_cleanup(self._page, text, title, self.debug)
        await self._navigate_to_library()
        return doc_url


# ---------------------------------------------------------------------------
# Standalone functions (one operation per page)
# ---------------------------------------------------------------------------

async def add_text(text: str, title: str = "", debug: bool = False) -> str:
    """
    Add raw text to Speechify via the "Paste Text" UI flow.
    Returns the Speechify document URL (e.g. https://app.speechify.com/item/<uuid>).

    On any failure mid-flow, attempts to delete a partial item if one
    was already created (URL is on /item/<uuid>) before re-raising
    (issue #39).
    """
    async with async_new_page() as page:
        await _init_speechify_page(page)

        if debug:
            await _save_screenshot(page, "text-01-page-loaded")

        doc_url = await _add_text_with_cleanup(page, text, title, debug)

        if debug:
            await _save_screenshot(page, "text-04-done")

        return doc_url


async def add_url(url: str, debug: bool = False) -> str:
    """Add a URL to Speechify via the Paste Link flow.

    Returns the Speechify item URL if observable (page redirected to
    /item/<uuid> within the timeout). Returns an empty string if Speechify
    accepted the URL but didn't redirect — the URL was still queued, we just
    couldn't observe its id.
    """
    async with async_new_page() as page:
        console_errors = []
        page.on("pageerror", lambda err: console_errors.append(str(err)))

        await _init_speechify_page(page)

        if debug:
            await _save_screenshot(page, "01-page-loaded")

        await _perform_add(page, url, debug=debug)

        crashed = await page.locator("text=Application error").count()
        if crashed > 0:
            raise RuntimeError(
                f"Speechify app crashed after Paste Link.\n"
                f"Page errors: {console_errors[:3]}"
            )

        try:
            return await _wait_for_item_redirect(page, timeout_seconds=15)
        except RuntimeError:
            return ""


async def add_file(path: Path, title: str = "", debug: bool = False) -> str:
    """Upload a file (.pdf/.epub/.html/.txt) via Speechify's Upload-File flow.

    Speechify's "New → Upload File" entry doesn't open a native chooser;
    it reveals a hidden ``<input type=file data-testid=library-dropzone-file-input>``
    that we drive directly via ``page.set_input_files``. Title is set
    post-upload via Speechify's own metadata extraction (no UI hook today).

    Returns the Speechify item URL on success.
    """
    path = _validate_file_path(Path(path))
    size_kb = path.stat().st_size / 1024
    log.debug("add_file: start path=%s size=%.1fKB title=%r", path, size_kb, title)
    t0 = time.perf_counter()

    async with async_new_page() as page:
        log.debug("add_file: got page in %.2fs", time.perf_counter() - t0)

        t1 = time.perf_counter()
        await _init_speechify_page(page)
        log.debug("add_file: speechify ready (%.2fs since page; %.2fs total)",
                  time.perf_counter() - t1, time.perf_counter() - t0)

        if debug:
            await _save_screenshot(page, "file-01-page-loaded")

        t2 = time.perf_counter()
        await page.locator('[data-testid="sidebar-import-button"]').click()
        await page.wait_for_timeout(600)
        log.debug("add_file: clicked New (%.2fs)", time.perf_counter() - t2)

        if debug:
            await _save_screenshot(page, "file-02-new-menu")

        t3 = time.perf_counter()
        await page.locator('[data-testid="library-menu-item-upload-file"]').click()
        await page.wait_for_timeout(800)
        log.debug("add_file: clicked Upload File (%.2fs)", time.perf_counter() - t3)

        if debug:
            await _save_screenshot(page, "file-03-after-upload-file-click")

        t4 = time.perf_counter()
        file_input = page.locator('[data-testid="library-dropzone-file-input"]')
        await file_input.wait_for(state="attached", timeout=10_000)
        await file_input.set_input_files(str(path))
        log.debug("add_file: set_input_files done (%.2fs)", time.perf_counter() - t4)

        if debug:
            await _save_screenshot(page, "file-04-after-set-files")

        # PDF/EPUB processing can take longer than text — bump the timeout.
        # `title` is accepted for API compatibility but not yet plumbed
        # through — the upload flow has no title field, and Speechify
        # extracts its own from file metadata (PDFs especially). A
        # post-upload rename via the item-page UI is a follow-up.
        t5 = time.perf_counter()
        doc_url = await _wait_for_item_redirect(page, timeout_seconds=180)
        log.debug("add_file: redirect observed in %.2fs", time.perf_counter() - t5)
        log.debug("add_file: TOTAL %.2fs -> %s", time.perf_counter() - t0, doc_url)
        return doc_url


async def _wait_for_item_redirect(page, timeout_seconds: int) -> str:
    """Poll page.url once per second for `/item/<uuid>` and return it."""
    t0 = time.perf_counter()
    last_url = None
    for i in range(timeout_seconds):
        await page.wait_for_timeout(1_000)
        if page.url != last_url:
            log.debug("wait_for_item_redirect: t=%ds url=%s", i + 1, page.url)
            last_url = page.url
        if "/item/" in page.url:
            log.debug("wait_for_item_redirect: hit /item/ after %.2fs",
                      time.perf_counter() - t0)
            return page.url
    raise RuntimeError(
        f"Timed out waiting for Speechify to process the upload. "
        f"Final URL: {page.url}"
    )


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
    SCREENSHOT_DIR.mkdir(parents=True, exist_ok=True)

    async with async_new_page() as page:
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


async def _dump_failure(page, name: str) -> None:
    """Save a screenshot + raw HTML of the page for selector-failure debugging.

    Best-effort: never raises. Output goes to ``SCREENSHOT_DIR`` with a
    timestamp suffix so successive failures don't overwrite each other.
    """
    SCREENSHOT_DIR.mkdir(parents=True, exist_ok=True)
    ts = time.strftime("%Y%m%d-%H%M%S")
    try:
        await page.screenshot(
            path=str(SCREENSHOT_DIR / f"failure-{name}-{ts}.png"),
            full_page=True,
        )
    except Exception as e:
        log.debug("dump_failure: screenshot failed: %s", e)
    try:
        html = await page.content()
        (SCREENSHOT_DIR / f"failure-{name}-{ts}.html").write_text(html)
    except Exception as e:
        log.debug("dump_failure: html dump failed: %s", e)


async def _click_first_visible(page, selectors, step, timeout=5_000):
    loc = await _find_first_visible(page, selectors, step, timeout)
    await loc.click()


async def _find_first_visible(page, selectors, step, timeout=5_000):
    if not selectors:
        raise _StepSkipped(f"No visible element for '{step}'. Tried: {selectors}")
    per = max(500, timeout // len(selectors))
    for selector in selectors:
        try:
            loc = page.locator(selector).first
            await loc.wait_for(state="visible", timeout=per)
            return loc
        except Exception:
            continue
    raise _StepSkipped(f"No visible element for '{step}'. Tried: {selectors}")


# ---------------------------------------------------------------------------
# Paste-text upload internals (shared by BrowserSession.add_text and the
# standalone add_text). Pulled out so the resilience + orphan-cleanup
# logic isn't duplicated in two places (issue #39).
# ---------------------------------------------------------------------------

async def _add_text_with_cleanup(
    page, text: str, title: str, debug: bool
) -> str:
    """Run ``_do_add_text``, deleting any partial item before re-raising
    on failure (issue #39). Shared by ``BrowserSession.add_text`` and the
    standalone ``add_text``.
    """
    try:
        return await _do_add_text(page, text, title=title, debug=debug)
    except Exception:
        await _maybe_delete_partial_item(page, debug=debug)
        raise


async def _open_paste_text_modal(page) -> str:
    """Open the paste-text modal by whichever entry point Speechify is
    serving this session. Returns the entry name used (``"toolbar"`` or
    ``"menu"``). Raises ``_StepSkipped`` if neither flow finds the entry.

    Issue #41: Speechify is rolling out a redesigned Library UI. The new
    flow has a top-bar ``[data-testid="add-text-button"]`` ("Create
    Note") that opens the modal directly — no "+ New" step. The legacy
    flow ("+ New" sidebar → ``library-menu-item-paste-text``) is kept
    as a fallback for sessions still on the old UI during rollout.
    """
    # New UI first: a quick visibility check, then a single click.
    try:
        await _click_first_visible(
            page,
            ADD_TEXT_BUTTON_SELECTORS,
            step="add-text toolbar button",
            timeout=ADD_TEXT_BUTTON_TIMEOUT_MS,
        )
        log.debug("paste-text: opened via add-text-button (new UI)")
        return "toolbar"
    except _StepSkipped:
        log.debug(
            "paste-text: add-text-button not visible after %.1fs; "
            "trying legacy '+ New' menu flow",
            ADD_TEXT_BUTTON_TIMEOUT_MS / 1000,
        )

    # Legacy UI: open "+ New" dropdown, then click the Paste Text item.
    # Only do the "+ New" click in this branch — on the new UI it opens
    # an unrelated dialog (`aria-haspopup="dialog"`) which we don't want.
    await page.locator('[data-testid="sidebar-import-button"]').click()
    await page.wait_for_timeout(600)
    await _click_first_visible(
        page,
        PASTE_TEXT_MENU_SELECTORS,
        step="paste-text menu item",
        timeout=PASTE_TEXT_MENU_TIMEOUT_MS,
    )
    log.debug("paste-text: opened via library-menu-item-paste-text (legacy UI)")
    return "menu"


async def _do_add_text(page, text: str, title: str = "", debug: bool = False) -> str:
    """Drive the Paste Text modal end-to-end and return the /item/ URL.

    Raises ``RuntimeError`` if neither entry point opens the modal or
    Speechify never redirects to /item/<uuid> within the timeout. On
    entry-point failure, dumps the page screenshot + HTML so we can see
    what changed.
    """
    if debug:
        await _save_screenshot(page, "text-01-before-entry")

    try:
        await _open_paste_text_modal(page)
    except _StepSkipped:
        await _dump_failure(page, "paste-text-entry")
        raise RuntimeError(
            f"Could not open the Paste Text modal — neither the "
            f"add-text-button toolbar (new UI) nor the '+ New' menu "
            f"(legacy UI) responded within "
            f"{PASTE_TEXT_MENU_TIMEOUT_MS // 1000}s. DOM dumped to "
            f"{SCREENSHOT_DIR}. Speechify's UI may have changed."
        )
    await page.wait_for_timeout(1_000)

    if debug:
        await _save_screenshot(page, "text-02-paste-text-modal")

    # Fill title (optional) and text
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

    if debug:
        await _save_screenshot(page, "text-03-filled")

    await page.locator('[data-testid="add-text-save-button"]').click()

    # Wait for processing — page redirects to /item/<uuid> when done
    for _ in range(30):
        await page.wait_for_timeout(1_000)
        if "/item/" in page.url:
            return page.url

    raise RuntimeError(
        "Timed out waiting for Speechify to process the text. "
        f"Final URL: {page.url}"
    )


async def _maybe_delete_partial_item(page, debug: bool = False) -> None:
    """If ``page.url`` is on a Speechify item, delete it. Best-effort.

    Used to clean up after a failed paste-text flow so the user's library
    isn't left with half-state items.
    """
    item_id = _extract_item_id(getattr(page, "url", None))
    if not item_id:
        return
    log.warning(
        "Paste-text flow failed mid-upload on /item/%s; attempting cleanup",
        item_id,
    )
    try:
        await _perform_delete(page, item_id, debug=debug)
    except Exception as e:
        log.warning("Cleanup of partial item %s failed: %s", item_id, e)


async def _perform_delete(page, item_id: str, debug: bool = False) -> None:
    """Delete a Speechify item using the existing page. Raises on failure."""
    if debug:
        await _save_screenshot(page, "delete-01-before")

    await page.goto(
        f"https://app.speechify.com/item/{item_id}",
        wait_until="load",
        timeout=60_000,
    )
    await page.wait_for_timeout(2_000)

    if debug:
        await _save_screenshot(page, "delete-02-item-page")

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

        if debug:
            await _save_screenshot(page, "delete-03-menu-open")
    except _StepSkipped:
        # No menu button found — delete button might be directly visible
        pass

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

    if debug:
        await _save_screenshot(page, "delete-04-after-delete-click")

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

    if debug:
        await _save_screenshot(page, "delete-05-done")

    if f"/item/{item_id}" in page.url:
        not_found = await page.locator("text=not found").count()
        gone = await page.locator("text=deleted").count()
        if not_found == 0 and gone == 0:
            raise RuntimeError(
                f"Deletion may have failed — still on item page: {page.url}"
            )
