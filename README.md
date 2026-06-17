# speechify-add

A CLI tool for programmatically adding articles and text to your Speechify listening queue, and managing library items.

Speechify has no public API for library management, so this project reverse-engineers the consumer app's internal API and provides browser-automation — letting you add, search, and delete items from the command line, scripts, or automation pipelines.

---

## Setup

**Requirements:**
- Python 3.11+
- A Speechify account (Google or Apple login)
- Chromium (for auth setup and browser-based uploads)

**Install:**
```bash
git clone https://github.com/Josephkready/speechify-add
cd speechify-add
python3 -m venv .venv && source .venv/bin/activate
pip install -e .
playwright install chromium
```

**Authenticate:**
```bash
speechify-add auth setup
# Opens a browser — log in, then add any URL to capture the API endpoint
```

---

## Usage

### Add a URL

```bash
# Add a single URL
speechify-add add https://arstechnica.com/some-article/

# Batch add from a file (one URL per line, # for comments)
speechify-add add --file urls.txt

# Pipe in from stdin
echo "https://example.com/article" | speechify-add add --stdin

# Force browser-automation mode
speechify-add add --mode browser https://example.com/article
```

### Add raw text

Upload plain text or markdown directly:

```bash
# From a file
speechify-add text -f digest.md -t "HN Digest 2026-03-11"

# From stdin
cat summary.txt | speechify-add text --stdin -t "Daily Summary"
```

Returns the Speechify document URL on success. Handles large files (tested up to 700K chars).

If the upload flow fails mid-stream and the page is already on `/item/<uuid>`, the partial item is deleted before the error is re-raised so retries don't accumulate orphans in the library.

### Add a file (PDF / EPUB / HTML / TXT)

Upload an actual file via Speechify's "Import file" flow — preserves images and formatting that the `text` command flattens away:

```bash
speechify-add file article.pdf -t "On Re-reading LOTR"
speechify-add file book.epub
```

Supported extensions: `.pdf`, `.epub`, `.html`, `.htm`, `.txt`. The `--title` flag is best-effort: Speechify usually extracts the title from the file's own metadata (PDFs especially), and the import dialog may not expose a title field. Returns the Speechify document URL on success.

### Delete an item

Remove an item from your Speechify library:

```bash
# By full URL
speechify-add delete https://app.speechify.com/item/abc-123-def

# By item UUID
speechify-add delete abc-123-def
```

Uses the `archiveLibraryItem` API — no browser needed.

### Search / verify

Confirm an item is in your library:

```bash
# Direct URL/UUID check — recommended for verifying something you just uploaded.
# Bypasses Speechify's library search (which has minutes of indexing latency
# for fresh uploads) and goes straight to the item page.
speechify-add verify https://app.speechify.com/item/783247eb-59c9-4ade-9027-e01f8d77d959
speechify-add verify 783247eb-59c9-4ade-9027-e01f8d77d959

# Article URL (fetches the page title and searches by it)
speechify-add verify https://arstechnica.com/some-article/

# Free-form keyword search
speechify-add verify "cosmic distance ladder"
```

The search-based modes match by title substring (case-insensitive, either direction): a non-empty fuzzy result list isn't enough, the query must actually appear in at least one returned title. The URL/UUID mode is the reliable form for automation that wants to verify the upload it just did.

### Auth management

```bash
# One-time setup (opens browser)
speechify-add auth setup

# Refresh expired Firebase token
speechify-add auth refresh
```

---

## Python API

`speechify-add` is also importable from Python — useful for downstream pipelines (`medium-fetch`, `book-to-speechify`, `hn-digest`, …) that want to push content to Speechify without shelling out:

```python
from pathlib import Path
from speechify_add import upload_text, upload_file, upload_url

# Each call returns the Speechify item URL on success.
url = upload_text("hello world", title="Test")
url = upload_file(Path("article.pdf"), title="On Re-reading LOTR")
url = upload_url("https://example.com/article")
```

All three are sync wrappers around the underlying async browser flows — they manage the event loop internally, so callers don't need to. Auth comes from the persistent browser profile created by `speechify-add auth setup`.

`upload_url` returns an empty string if Speechify accepted the URL but didn't redirect to the item page within ~15 seconds (the URL was still queued — we just couldn't observe its id).

### Issue #51 — text routes through file-upload

`upload_text` (and the underlying `browser.add_text`) writes the text to a temp `.txt` file and uploads it via Speechify's file-upload flow. The SPA's *paste-text* flow doesn't persist content blobs to Firebase Storage — items end up cached only in the upload session's local IndexedDB and render `"Oops!"` for every other browser. The file-upload flow POSTs to Firebase Storage explicitly, producing items any session can read.

Notes on behavior:
- **Latency**: text uploads now take ~50–60s (vs ~10s) because we wait for fresh-context verification to confirm the content blob is server-side fetchable before returning.
- **Failure mode**: if the content blob doesn't persist within the 90s budget, the call raises instead of returning a broken URL.
- **Title rendering**: Speechify uses the uploaded file's basename as the item title (the file-upload flow has no title field). The `title` arg is sanitized into a filesystem-safe name — e.g. `upload_text(text, title="My Article")` produces an item titled `My-Article` in the library.

---

## How It Works

| Approach | Used by | How |
|---|---|---|
| **Consumer API** | `delete` | Direct HTTP calls to Speechify's Firebase Cloud Functions |
| **Browser automation** | `add`, `text`, `file` | Drives the shared [chrome-hub](https://github.com/Josephkready/chrome-hub) Chrome over CDP (Playwright `connect_over_cdp`), reusing its persistent login profile |

Browser operations connect to chrome-hub's already-running Chrome rather than launching their own — avoiding a ~17s cold start and letting chrome-hub manage the headed/virtual display (Speechify's clipboard API requires a visible window, which chrome-hub provides).

**Tab cleanup.** Browser operations open tabs in the shared [chrome-hub](https://github.com/Josephkready/chrome-hub) Chrome and close them when done. If a `speechify-add` process is killed mid-operation (timeout, `Ctrl-C`, OOM during batch uploads) the close never runs and the tab is stranded. To prevent these from piling up, every tab is recorded with its owning PID; the next `speechify-add` invocation sweeps the registry and closes any tab whose owning process is no longer alive (issue #55). Tabs owned by a *live* concurrent run are never touched.

---

## Project Structure

```
speechify_add/
  __init__.py  # Public Python API (upload_text, upload_file, upload_url)
  cli.py       # Click CLI (add, text, file, delete, verify, auth, debug)
  auth.py      # Firebase token capture, refresh, and storage
  api.py       # Direct API calls (add URL, delete item)
  browser.py   # Playwright automation (add URL, add text, add file)
  verify.py    # Library search via headless browser
  tab_registry.py  # Owned-tab tracking + dead-process orphan sweep (issue #55)
  config.py    # Paths and configuration
```

**Config files** (`~/.config/speechify-add/`):

| File | Purpose |
|------|---------|
| `auth.json` | Firebase refresh token and API key |
| `browser-profile/` | Persistent Chromium profile (stays logged in) |

**State files** (`~/.local/state/speechify-add/`):

| File | Purpose |
|------|---------|
| `open-tabs.json` | Tabs this tool has open in chrome-hub, keyed by CDP target id, with the owning PID — used to reap tabs leaked by killed runs. Override the path with `SPEECHIFY_ADD_TAB_REGISTRY`. |

---

## Tests

Pure-logic tests run with no external services and no auth:

```bash
pytest -m "not live"
```

Live tests round-trip through the real Speechify backend (upload → archive). They require a working browser profile from `auth setup` and `pip install -e .[dev]` for the PDF test and the `--forked` flag (reportlab + pytest-forked). Tests clean up after themselves in a `finally` block, but if a test crashes hard you may need to delete the stray item manually:

```bash
# Recommended: one fresh process per test, sidesteps occasional
# chrome-hub orphan-cleanup hangs when async_new_page() runs back-to-back.
pytest -m live --forked

# Or without forking (single process, faster but flakier on consecutive tests):
pytest -m live
```

**Run `pytest -m live --forked` before merging any change that touches the browser-automation flows in `browser.py` or `verify.py`.** Speechify's `data-testid` attributes are unstable; the live tests are our only guard against the DOM rotating out from under us.

Set `--log-cli-level=DEBUG` to see per-step timing — `add_file` and `search_library` emit timestamps for `goto`, sidebar visibility, menu clicks, file-input attachment, and the `/item/<uuid>` redirect, so a slow run can be diagnosed without re-instrumentation.

---

## Disclaimer

This tool uses Speechify's undocumented internal API. It is not affiliated with or endorsed by Speechify Inc. Use it for personal use with your own account. The API can change at any time without notice.
