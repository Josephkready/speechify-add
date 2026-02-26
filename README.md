# speechify-add

A CLI tool for programmatically adding articles and URLs to your Speechify listening queue.

Speechify has no public API for library management, so this project reverse-engineers the consumer app's internal API and provides a browser-automation fallback — letting you add articles from the command line, scripts, or automation pipelines.

---

## Motivation

Speechify is great for listening to articles, but adding content requires opening the app manually. This tool makes it scriptable:

```bash
# Add a single article
speechify-add https://example.com/article

# Batch add from a file
speechify-add --file reading-list.txt

# Pipe URLs in
cat urls.txt | speechify-add --stdin
```

---

## How It Works

There is no official Speechify library management API. This project uses three approaches, in order of preference:

| # | Approach | How | Reliability |
|---|---|---|---|
| 1 | **Consumer API replay** | Captures and replays the internal HTTP requests `app.speechify.com` makes when you add a URL | High once set up; brittle to API changes |
| 2 | **Browser automation** | Drives a headless Chromium instance via Playwright to add URLs through the UI | Medium; robust to API changes, slow |
| 3 | **Google Drive bridge** | Fetches and cleans article content, uploads to Google Drive; Speechify auto-imports from connected Drive | High; only works for document-style content |

See [`docs/design.md`](docs/design.md) for full technical detail on each approach.

---

## Setup

> Full setup instructions coming once implementation is complete.

**Requirements:**
- Python 3.11+
- A Speechify account (Google or Apple login)
- Chrome/Chromium (for initial auth capture and browser-automation fallback)

**Install:**
```bash
git clone https://github.com/Josephkready/speechify-add
cd speechify-add
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
```

**Authenticate:**
```bash
speechify-add auth setup
# Opens a browser, you log in once; tokens are saved locally
```

---

## Usage

```bash
# Add a single URL
speechify-add https://arstechnica.com/some-article/

# Add multiple URLs from a file (one URL per line)
speechify-add --file urls.txt

# Pipe in from stdin
echo "https://example.com/article" | speechify-add --stdin

# Force browser-automation mode (slower but more robust)
speechify-add --mode browser https://example.com/article

# Force Google Drive bridge
speechify-add --mode drive https://example.com/article

# Refresh stored auth tokens
speechify-add auth refresh
```

---

## Project Structure

```
speechify-add/
├── speechify_add/
│   ├── __init__.py
│   ├── cli.py           # Entry point / argument parsing
│   ├── auth.py          # Firebase token management
│   ├── api.py           # Consumer API replay (approach 1)
│   ├── browser.py       # Playwright automation (approach 2)
│   └── drive.py         # Google Drive bridge (approach 3)
├── docs/
│   └── design.md        # Architecture and research
├── tests/
├── requirements.txt
└── README.md
```

---

## Status

- [ ] Auth capture + Firebase token refresh
- [ ] Consumer API reverse-engineering
- [ ] CLI skeleton
- [ ] Browser automation fallback
- [ ] Google Drive bridge
- [ ] Batch / stdin support
- [ ] Tests

---

## Disclaimer

This tool uses Speechify's undocumented internal API. It is not affiliated with or endorsed by Speechify Inc. Use it for personal use with your own account. The API can change at any time without notice.
