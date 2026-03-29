"""
Auth management: supervised browser login, Firebase token capture and refresh.

On first run (`auth setup`), a headed browser opens and the user logs in manually.
We intercept:
  - The Firebase refresh token and API key (from IndexedDB)
  - The first authenticated network request (to learn the ID token)
  - The first "add item" POST request (to capture the consumer API endpoint)

Subsequent runs refresh the ID token via the Firebase REST API using the
stored refresh token, without opening a browser.
"""

import asyncio
import re
import time

import httpx

from . import config

# ---------------------------------------------------------------------------
# Token refresh (Approach 1 fast path)
# ---------------------------------------------------------------------------

async def get_id_token() -> str:
    """Return a valid Firebase ID token, refreshing if needed."""
    data = config.load()
    if not data:
        raise RuntimeError("Not authenticated. Run: speechify-add auth setup")

    expires_at = data.get("id_token_expires_at", 0)
    # Keep a 5-minute buffer before actual expiry
    if time.time() < expires_at - 300 and data.get("id_token"):
        return data["id_token"]

    return await _refresh_id_token(data)


async def refresh_and_print():
    """Refresh the token and print a confirmation (used by the CLI command)."""
    data = config.load()
    if not data:
        raise RuntimeError("Not authenticated. Run: speechify-add auth setup")
    await _refresh_id_token(data)


async def _refresh_id_token(data: dict) -> str:
    refresh_token = data.get("refresh_token")
    api_key = data.get("firebase_api_key")

    if not refresh_token or not api_key:
        # Try chrome-hub fallback before giving up
        return await _refresh_from_chrome_hub(data)

    async with httpx.AsyncClient() as client:
        resp = await client.post(
            f"https://securetoken.googleapis.com/v1/token?key={api_key}",
            data={"grant_type": "refresh_token", "refresh_token": refresh_token},
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            timeout=15,
        )
        if resp.status_code in (400, 403):
            # Firebase refresh failed — try extracting fresh tokens from chrome-hub
            return await _refresh_from_chrome_hub(data)
        resp.raise_for_status()

    result = resp.json()
    id_token = result["id_token"]
    expires_in = int(result.get("expires_in", 3600))

    data["id_token"] = id_token
    data["refresh_token"] = result.get("refresh_token", refresh_token)
    data["id_token_expires_at"] = time.time() + expires_in
    config.save(data)

    return id_token


async def _refresh_from_chrome_hub(data: dict) -> str:
    """Extract fresh Firebase tokens from chrome-hub's logged-in Chrome session."""
    try:
        from chrome_hub import async_new_page
    except ImportError:
        raise RuntimeError(
            "Token refresh failed and chrome-hub is not installed. "
            "Run: speechify-add auth setup"
        )

    async with async_new_page() as page:
        await page.goto("https://app.speechify.com", wait_until="load", timeout=30_000)
        await page.wait_for_timeout(3_000)

        if "auth" in page.url:
            raise RuntimeError(
                "Chrome-hub session is not logged into Speechify. "
                "Run: speechify-add auth setup"
            )

        records = await _read_firebase_indexeddb(page)

    captured: dict = {}
    _extract_firebase_tokens(records, captured)

    id_token = captured.get("id_token")
    if not id_token:
        raise RuntimeError(
            "Could not extract Firebase token from chrome-hub session. "
            "Run: speechify-add auth setup"
        )

    # Update stored auth data with fresh tokens
    data["id_token"] = id_token
    if captured.get("refresh_token"):
        data["refresh_token"] = captured["refresh_token"]
    if captured.get("firebase_api_key"):
        data["firebase_api_key"] = captured["firebase_api_key"]
    if captured.get("id_token_expires_at"):
        data["id_token_expires_at"] = captured["id_token_expires_at"]
    else:
        data["id_token_expires_at"] = time.time() + 3600
    config.save(data)

    return id_token


# ---------------------------------------------------------------------------
# Debug log redaction
# ---------------------------------------------------------------------------

_SENSITIVE_KEYS = frozenset({
    "token", "idtoken", "refreshtoken", "access_token", "refresh_token",
    "id_token", "apikey", "api_key", "password", "secret", "credential",
    "authorization", "accesstoken", "grant_type",
})


def _redact_body(body: str) -> str:
    """Redact sensitive values from a request body (JSON or form-encoded)."""
    import json as _json
    from urllib.parse import parse_qs as _parse_qs

    # Try JSON redaction first
    try:
        data = _json.loads(body)
        if isinstance(data, dict):
            return _json.dumps(_redact_dict(data))
    except (ValueError, TypeError):
        pass

    # Try form-encoded redaction
    if "=" in body and "&" in body or "token" in body.lower():
        try:
            pairs = _parse_qs(body, keep_blank_values=True)
            redacted = {
                k: (["<REDACTED>"] if k.lower() in _SENSITIVE_KEYS else v)
                for k, v in pairs.items()
            }
            return "&".join(
                f"{k}={v[0]}" for k, v in redacted.items()
            )
        except Exception:
            pass

    # Fall back to regex for anything else
    return re.sub(
        r'(?i)(["\']?)('
        + "|".join(re.escape(k) for k in _SENSITIVE_KEYS)
        + r')(\1\s*[:=]\s*["\']?)([^"\'&\s]{8,})(["\']?)',
        r"\1\2\3<REDACTED>\5",
        body,
    )


def _redact_dict(data: dict) -> dict:
    """Recursively redact sensitive keys in a dictionary."""
    result = {}
    for k, v in data.items():
        if k.lower().replace("-", "_") in _SENSITIVE_KEYS:
            result[k] = "<REDACTED>"
        elif isinstance(v, dict):
            result[k] = _redact_dict(v)
        else:
            result[k] = v
    return result


# ---------------------------------------------------------------------------
# Supervised login + token capture
# ---------------------------------------------------------------------------

async def setup():
    """
    Open a headed browser so the user can log in. While they interact with
    the app, we intercept network traffic to capture:
      - Firebase refresh token + API key (from IndexedDB)
      - A Bearer ID token (from any authenticated request)
      - The add-URL endpoint, method, headers, and body (from the first
        POST/PUT that looks like an "add item" request)

    Instructs the user to add one URL to their library before closing the
    browser, so we learn the exact endpoint shape.
    """
    from playwright.async_api import async_playwright

    profile_dir = config.BROWSER_PROFILE_DIR
    profile_dir.mkdir(parents=True, exist_ok=True)

    print("─" * 60)
    print("Speechify Auth Setup")
    print("─" * 60)
    print("A browser window will open. Please:")
    print("  1. Log in to your Speechify account (if not already logged in)")
    print("  2. Click the 'New' button → 'Paste Link'")
    print("  3. Paste ANY real URL and confirm (this captures the API endpoint)")
    print("  4. Close the browser window when the URL has been added")
    print()
    print("  ⚠  Step 3 is critical — the tool needs to observe the network")
    print("     call Speechify makes when adding a URL.")
    print("─" * 60)

    import json as _json
    from urllib.parse import urlparse as _urlparse, parse_qs as _parse_qs

    captured: dict = {}
    # All POST/PUT requests logged here for manual inspection
    debug_log_path = config.CONFIG_DIR / "auth-debug-requests.jsonl"
    debug_log_path.parent.mkdir(parents=True, exist_ok=True)

    # Domains we never care about (pure analytics/telemetry)
    _SKIP = (
        "google-analytics.com", "googletagmanager.com",
        "segment.io", "segment.com",
        "amplitude.com", "mixpanel.com", "hotjar.com",
        "sentry.io", "datadog", "newrelic", "logrocket",
        "grafana.net", "faro-collector", "faro-cloud-proxy",
    )

    async with async_playwright() as p:
        ctx = await p.chromium.launch_persistent_context(
            str(profile_dir),
            headless=False,
            args=["--disable-blink-features=AutomationControlled"],
        )

        page = await ctx.new_page()

        # ── Intercept outgoing requests ──────────────────────────────────
        async def on_request(request):
            auth_header = request.headers.get("authorization", "")
            if auth_header.startswith("Bearer ") and "id_token" not in captured:
                captured["id_token"] = auth_header.removeprefix("Bearer ")
                captured["id_token_expires_at"] = time.time() + 3600

            # Capture Firebase API key
            if "googleapis.com" in request.url and "key=" in request.url:
                params = _parse_qs(_urlparse(request.url).query)
                if params.get("key") and not captured.get("firebase_api_key"):
                    captured["firebase_api_key"] = params["key"][0]

        async def on_response(response):
            url = response.url
            req = response.request

            # ── Capture refresh token from Firebase auth responses ────────
            if (
                "securetoken.googleapis.com" in url
                or "identitytoolkit.googleapis.com" in url
            ) and req.method == "POST":
                try:
                    body = await response.json()
                    if body.get("refreshToken") and not captured.get("refresh_token"):
                        captured["refresh_token"] = body["refreshToken"]
                    if body.get("idToken") and not captured.get("id_token"):
                        captured["id_token"] = body["idToken"]
                except Exception:
                    pass

            if req.method not in ("POST", "PUT", "PATCH"):
                return

            # Skip pure telemetry/analytics domains
            if any(skip in url for skip in _SKIP):
                return

            # Log ALL remaining POST/PUT requests to the debug file
            # Redact sensitive fields to avoid leaking tokens
            try:
                body = req.post_data or ""
                body_preview = _redact_body(body[:500])
                entry = {
                    "url": url,
                    "method": req.method,
                    "body_len": len(body),
                    "body_preview": body_preview,
                    "content_type": req.headers.get("content-type", ""),
                }
                with open(debug_log_path, "a") as f:
                    f.write(_json.dumps(entry) + "\n")
            except Exception:
                pass

        page.on("request", on_request)
        page.on("response", on_response)

        await page.goto("https://app.speechify.com")
        print(f"\nAll requests are being logged to:\n  {debug_log_path}")
        print("\nWaiting... (close the browser when done)\n")

        # Read Firebase tokens from IndexedDB now — must happen while page is open.
        # Captures pre-existing auth state for users already logged in.
        try:
            firebase_data = await _read_firebase_indexeddb(page)
            _extract_firebase_tokens(firebase_data, captured)
        except Exception as e:
            print(f"  (Could not read IndexedDB: {e})")

        try:
            await page.wait_for_event("close", timeout=600_000)
        except Exception:
            pass

        await ctx.close()

    # ── Analyse debug log to find the add-URL endpoint ───────────────────
    import re as _re
    import json as _json

    add_candidates = []
    try:
        with open(debug_log_path) as f:
            for line in f:
                try:
                    entry = _json.loads(line)
                except Exception:
                    continue
                url = entry.get("url", "")
                body = entry.get("body_preview", "")
                # Skip Firestore read-only paths
                if any(op in url for op in ("runAggregationQuery", "runQuery",
                                            ":batchGet", ":listen")):
                    continue
                # Skip single-letter Segment paths
                if _re.search(r"/v\d+/[a-z]$", url):
                    continue
                add_candidates.append(entry)
    except FileNotFoundError:
        pass

    print(f"\n── Auth capture results ──")
    print(f"  Firebase API key : {'✓' if captured.get('firebase_api_key') else '✗ missing'}")
    print(f"  Refresh token    : {'✓' if captured.get('refresh_token') else '✗ missing'}")
    print(f"  ID token         : {'✓' if captured.get('id_token') else '✗ missing'}")

    if add_candidates:
        print(f"\n  POST/PUT requests captured ({len(add_candidates)} total):")
        for e in add_candidates:
            print(f"    {e['method']:6} {e['url']}")
            print(f"           body ({e['body_len']}B): {e['body_preview'][:500]}")
    else:
        print(f"  ⚠  No candidate API requests captured.")
        print(f"     See {debug_log_path} for full log.")

    config.save(captured)
    print(f"\n✓ Saved to {config.AUTH_FILE}")
    print(f"  Debug log: {debug_log_path}")


async def _read_firebase_indexeddb(page) -> list:
    """Read all records from the firebaseLocalStorageDb IndexedDB store."""
    return await page.evaluate("""
        async () => {
            return new Promise((resolve) => {
                try {
                    const req = indexedDB.open('firebaseLocalStorageDb');
                    req.onsuccess = (e) => {
                        const db = e.target.result;
                        const stores = Array.from(db.objectStoreNames);
                        const storeName = stores.find(s => s.toLowerCase().includes('firebase'))
                                       || stores[0];
                        if (!storeName) { resolve([]); return; }
                        const tx = db.transaction(storeName, 'readonly');
                        const getAllReq = tx.objectStore(storeName).getAll();
                        getAllReq.onsuccess = () => resolve(getAllReq.result || []);
                        getAllReq.onerror = () => resolve([]);
                    };
                    req.onerror = () => resolve([]);
                } catch (_) { resolve([]); }
            });
        }
    """)


def _extract_firebase_tokens(records: list, captured: dict):
    """Walk IndexedDB records and extract refresh token + API key."""
    for record in records:
        value = record.get("value") if isinstance(record, dict) else record
        if isinstance(value, str):
            try:
                import json
                value = json.loads(value)
            except Exception:
                continue
        if not isinstance(value, dict):
            continue

        if value.get("apiKey") and not captured.get("firebase_api_key"):
            captured["firebase_api_key"] = value["apiKey"]

        stm = value.get("stsTokenManager", {})
        if stm.get("refreshToken") and not captured.get("refresh_token"):
            captured["refresh_token"] = stm["refreshToken"]
        if stm.get("accessToken") and not captured.get("id_token"):
            captured["id_token"] = stm["accessToken"]
        if stm.get("expirationTime") and not captured.get("id_token_expires_at"):
            captured["id_token_expires_at"] = stm["expirationTime"] / 1000

        if value.get("refreshToken") and not captured.get("refresh_token"):
            captured["refresh_token"] = value["refreshToken"]
