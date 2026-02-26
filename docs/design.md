# speechify-add — Design Document

## Problem Statement

Speechify has no public API for managing a user's listening library. Adding an article requires opening the app or browser extension and manually pasting a URL. The goal of this project is to make article addition scriptable — enabling CLI use, automation pipelines, and batch imports.

---

## Research Summary

### What Speechify exposes publicly

Speechify launched a public **TTS audio generation API** (`api.speechify.ai`, `docs.sws.speechify.com`) in 2023. It converts text to audio files (MP3/WAV) and is well-documented with Python and Node.js SDKs. **It has no concept of a user library or queue.** It is the wrong product for this use case entirely.

### What Speechify does not expose

The **consumer app** (`app.speechify.com`, iOS, Android, Chrome extension) — the product where your listening queue lives — has **no public API for library management**. No official endpoints, no documented auth, no Zapier/Make/IFTTT connectors.

### Key findings

| Finding | Detail |
|---|---|
| No public consumer API | Confirmed. Speechify's developer docs cover only TTS audio generation |
| No email-to-library | Unlike Pocket/Instapaper, Speechify has no inbound email import |
| No Zapier / Make / IFTTT | No native Speechify connector on any automation platform |
| No documented URL scheme | No `speechify://` deep link scheme is publicly documented |
| Firebase backend | The consumer app is React-based and communicates with a Firebase/Google Cloud backend using short-lived Firebase ID tokens (JWT, 1-hour TTL) |
| Internal queue method exists | A Speechify take-home interview assignment revealed an `addToQueue(data)` method that "sends an RPC to the Speechify Server" accepting HTML, TXT, or JSON with `type` and `source` fields |
| Google Drive sync | Speechify natively imports from a connected Google Drive — a viable path for document content |
| Browser automation is viable | Playwright can drive `app.speechify.com` headlessly to add URLs through the UI |
| Chrome extension ID | `ljflmlehinmoeknoonhibbjpldiijjmm` — manifest inspection needed to check for `externally_connectable` |

---

## Approach 1: Consumer API Replay (Primary)

### Overview

The Speechify web app makes HTTP requests to its backend when a user adds a URL. We capture those requests once (via Playwright or browser DevTools), then replay them directly in a script — bypassing the UI entirely.

### Authentication

The consumer app uses **Firebase Authentication**:

- On login (Google/Apple OAuth), Firebase issues an **ID token** (JWT) valid for **1 hour**
- Firebase also provides a **refresh token** (long-lived, does not expire unless revoked)
- The app sends `Authorization: Bearer <id-token>` with every API request
- New ID tokens are obtained by POST-ing the refresh token to Firebase's token refresh endpoint:

```
POST https://securetoken.googleapis.com/v1/token?key=<firebase-api-key>
Content-Type: application/x-www-form-urlencoded

grant_type=refresh_token&refresh_token=<your-refresh-token>
```

Response includes a fresh `id_token` and `refresh_token`.

### Capturing the endpoints

**One-time capture procedure:**

1. Log into `app.speechify.com` in Chrome
2. DevTools → Network → filter XHR/Fetch
3. Click "+" in the Speechify UI → paste any URL → confirm add
4. Observe the outbound POST — note:
   - Full URL (likely `https://api.speechify.com/...` or a Firebase Firestore REST endpoint)
   - Request headers (especially `Authorization`, `Content-Type`, any `x-*` headers)
   - Request body shape (JSON — likely `{ url: "...", type: "url" }` or similar)
5. Save those details as the canonical "add URL" API spec

Alternatively, Playwright can intercept network traffic automatically during a single supervised login session and dump the request for us.

### Auth capture flow

```
speechify-add auth setup
  │
  ├─ Launch Playwright (headed Chromium)
  ├─ Navigate to app.speechify.com
  ├─ User logs in manually (Google/Apple OAuth — can't automate due to OAuth restrictions)
  ├─ Intercept: capture Firebase ID token + refresh token from browser storage
  │    (localStorage key: likely "firebaseLocalStorage" or similar)
  ├─ Intercept: capture one "add URL" network request to record the endpoint
  └─ Save { refresh_token, firebase_api_key, add_endpoint, add_headers } to ~/.config/speechify-add/auth.json
```

### Runtime flow (after auth setup)

```
speechify-add <url>
  │
  ├─ Load auth.json
  ├─ Check if stored ID token is still valid (decode JWT exp claim)
  │    ├─ If expired: call Firebase token refresh → get new id_token
  │    └─ Save updated id_token back to auth.json
  ├─ POST to captured add_endpoint with { url: <url> }
  │    Headers: Authorization: Bearer <id_token>
  └─ Check response → report success / error
```

### Risks

- **API changes**: Speechify can change their endpoints, auth mechanism, or request shape at any time. No SLA.
- **Auth token changes**: If Speechify rotates their Firebase API key or moves off Firebase, the refresh flow breaks.
- **Mitigation**: The browser-automation fallback (Approach 2) handles this case.

---

## Approach 2: Browser Automation Fallback

### Overview

Use Playwright to drive a real Chromium browser against `app.speechify.com`. Slower and heavier than API replay but robust to backend API changes since it uses the actual UI.

### Authentication

Playwright supports **persistent browser contexts** — a profile directory is saved to disk. After the user logs in once (supervised, headed), all subsequent runs load that profile and remain authenticated.

```python
from playwright.async_api import async_playwright

async with async_playwright() as p:
    browser = await p.chromium.launch_persistent_context(
        user_data_dir="~/.config/speechify-add/browser-profile",
        headless=True,  # headed for first login, then headless
    )
    page = await browser.new_page()
    await page.goto("https://app.speechify.com")
    # ... add URL via UI
```

### Add URL flow

The exact selectors will be identified during implementation, but the general flow:

1. Navigate to `https://app.speechify.com`
2. Find and click the "+" / "Add content" button
3. Select the "URL" input option
4. Fill the URL field
5. Click confirm
6. Wait for the item to appear in the library (or success toast)

### Selector strategy

Use `data-testid` attributes if available (most stable), fall back to `aria-label` attributes, avoid CSS class selectors (change frequently).

### Performance

~5–15 seconds per URL (browser launch + page load + UI interaction). Fine for batch use, too slow for real-time use cases.

---

## Approach 3: Google Drive Bridge

### Overview

Speechify natively syncs from a connected Google Drive. We can exploit this by:

1. Fetching and extracting the clean text of an article (via `newspaper3k`, `trafilatura`, or Mozilla Readability)
2. Creating a Google Doc via the Drive API with that content
3. Speechify automatically imports it

### When to use this

Best for long-form article content. Not suitable for paywalled content (we only get what the scraper can fetch). Produces a "document" item in Speechify rather than a "webpage" item, which may affect metadata/title display.

### Auth

Google OAuth 2.0 with `drive.file` scope (narrow scope — only files the app creates). Standard OAuth flow; credentials stored in `~/.config/speechify-add/google-oauth.json`.

### Article extraction

Use `trafilatura` (better accuracy than `newspaper3k` for modern sites):

```python
import trafilatura

downloaded = trafilatura.fetch_url(url)
text = trafilatura.extract(downloaded, include_comments=False, include_tables=False)
title = trafilatura.extract_metadata(downloaded).title
```

### Drive upload

```python
from googleapiclient.discovery import build

service = build("docs", "v1", credentials=creds)
doc = service.documents().create(body={"title": title}).execute()
service.documents().batchUpdate(
    documentId=doc["documentId"],
    body={"requests": [{"insertText": {"location": {"index": 1}, "text": text}}]}
).execute()
```

---

## Approach 4: Chrome Extension Inspection

### Overview

Speechify's Chrome extension (ID: `ljflmlehinmoeknoonhibbjpldiijjmm`) may expose `externally_connectable` in its manifest, which would allow any web page or external script to send messages directly to the extension to trigger "add to queue" — the lightest-weight approach possible.

### How to check

```bash
# Download and inspect the extension manifest
curl -L "https://clients2.google.com/service/update2/crx?response=redirect&prodversion=120.0&x=id%3Dljflmlehinmoeknoonhibbjpldiijjmm%26installsource%3Dondemand%26uc" -o speechify.crx
unzip speechify.crx -d speechify_ext
cat speechify_ext/manifest.json | python3 -m json.tool | grep -A10 externally_connectable
```

If `externally_connectable` is present and includes `"matches": ["<all_urls>"]` or includes our target domain, then from any page:

```javascript
chrome.runtime.sendMessage(
  "ljflmlehinmoeknoonhibbjpldiijjmm",
  { action: "addToQueue", url: "https://example.com/article" },
  (response) => console.log(response)
);
```

### Status

Not yet confirmed — pending manifest inspection. If available, this becomes the simplest integration path.

---

## Implementation Plan

### Phase 1 — Auth foundation
- [ ] `auth.py`: Playwright-based supervised login that captures Firebase refresh token + records one add-URL network request
- [ ] `auth.py`: Firebase token refresh (call `securetoken.googleapis.com` with refresh token)
- [ ] Persist `~/.config/speechify-add/auth.json`

### Phase 2 — Consumer API replay (Approach 1)
- [ ] `api.py`: POST to captured add-URL endpoint with auth header
- [ ] `cli.py`: `speechify-add <url>` wired up end-to-end
- [ ] Error handling: detect expired auth, auto-refresh, retry once

### Phase 3 — CLI polish
- [ ] `--file` and `--stdin` batch modes
- [ ] Progress output for batch jobs
- [ ] `speechify-add auth refresh` command

### Phase 4 — Browser automation fallback (Approach 2)
- [ ] `browser.py`: Playwright automation against `app.speechify.com`
- [ ] `--mode browser` flag to force this path
- [ ] Auto-fallback if API approach returns non-2xx

### Phase 5 — Google Drive bridge (Approach 3)
- [ ] `drive.py`: article extraction with `trafilatura`
- [ ] Google Drive API upload as Google Doc
- [ ] `--mode drive` flag

### Phase 6 — Extension investigation (Approach 4)
- [ ] Script to download and inspect Speechify extension manifest
- [ ] Implement if `externally_connectable` is available

---

## Configuration

Stored at `~/.config/speechify-add/auth.json`:

```json
{
  "firebase_api_key": "...",
  "refresh_token": "...",
  "id_token": "...",
  "id_token_expires_at": 1700000000,
  "add_endpoint": "https://...",
  "add_headers": { "Content-Type": "application/json" },
  "add_body_template": { "url": "{url}", "type": "url" }
}
```

---

## Dependencies

```
playwright          # Browser automation + auth capture
trafilatura         # Article extraction (Drive bridge)
google-api-python-client  # Google Drive API
google-auth-oauthlib      # Google OAuth
httpx               # HTTP client for API replay
click               # CLI framework
pyjwt               # Decode JWT exp claim without network call
```

---

## Security Considerations

- Auth tokens stored in `~/.config/speechify-add/auth.json` with `chmod 600`
- Refresh tokens are long-lived; treat like passwords
- Google OAuth uses `drive.file` scope (minimal) — not `drive` (full access)
- No tokens are logged or sent anywhere except Speechify/Google endpoints
