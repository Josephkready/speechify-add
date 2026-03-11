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

## How It Works

| Approach | Used by | How |
|---|---|---|
| **Consumer API** | `delete` | Direct HTTP calls to Speechify's Firebase Cloud Functions |
| **Browser automation** | `add`, `text` | Drives headed Chromium via Playwright with a persistent login profile |

The browser runs in headed mode (Speechify's clipboard API requires a visible window). On headless servers, Xvfb is used automatically as a virtual display.

---

## Project Structure

```
speechify_add/
  cli.py       # Click CLI (add, text, delete, verify, auth, debug)
  auth.py      # Firebase token capture, refresh, and storage
  api.py       # Direct API calls (add URL, delete item)
  browser.py   # Playwright automation (add URL, add text)
  verify.py    # Library search via headless browser
  config.py    # Paths and configuration
```

**Config files** (`~/.config/speechify-add/`):

| File | Purpose |
|------|---------|
| `auth.json` | Firebase refresh token and API key |
| `browser-profile/` | Persistent Chromium profile (stays logged in) |

---

## Disclaimer

This tool uses Speechify's undocumented internal API. It is not affiliated with or endorsed by Speechify Inc. Use it for personal use with your own account. The API can change at any time without notice.
