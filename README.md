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

Confirm an article is in your library:

```bash
# Search by URL (fetches the page title automatically)
speechify-add verify https://arstechnica.com/some-article/

# Search by keyword
speechify-add verify "cosmic distance ladder"
```

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

---

## How It Works

| Approach | Used by | How |
|---|---|---|
| **Consumer API** | `delete` | Direct HTTP calls to Speechify's Firebase Cloud Functions |
| **Browser automation** | `add`, `text`, `file` | Drives headed Chromium via Playwright with a persistent login profile |

The browser runs in headed mode (Speechify's clipboard API requires a visible window). On headless servers, Xvfb is used automatically as a virtual display.

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
  config.py    # Paths and configuration
```

**Config files** (`~/.config/speechify-add/`):

| File | Purpose |
|------|---------|
| `auth.json` | Firebase refresh token and API key |
| `browser-profile/` | Persistent Chromium profile (stays logged in) |

---

## Tests

Pure-logic tests run with no external services and no auth:

```bash
pytest -m "not live"
```

Live tests round-trip through the real Speechify backend (upload → search → delete). They require a working browser profile from `auth setup`. Tests clean up after themselves in a `finally` block, but if a test crashes hard you may need to delete the stray item manually:

```bash
pytest -m live
```

---

## Disclaimer

This tool uses Speechify's undocumented internal API. It is not affiliated with or endorsed by Speechify Inc. Use it for personal use with your own account. The API can change at any time without notice.
