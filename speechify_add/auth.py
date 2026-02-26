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
        raise RuntimeError(
            "Missing refresh token or Firebase API key. Run: speechify-add auth setup"
        )

    async with httpx.AsyncClient() as client:
        resp = await client.post(
            f"https://securetoken.googleapis.com/v1/token?key={api_key}",
            data={"grant_type": "refresh_token", "refresh_token": refresh_token},
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            timeout=15,
        )
        resp.raise_for_status()

    result = resp.json()
    id_token = result["id_token"]
    expires_in = int(result.get("expires_in", 3600))

    data["id_token"] = id_token
    data["refresh_token"] = result.get("refresh_token", refresh_token)
    data["id_token_expires_at"] = time.time() + expires_in
    config.save(data)

    return id_token


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
    print("  1. Log in to your Speechify account")
    print("  2. Add any URL to your library (click + → URL)")
    print("  3. Close the browser tab/window when done")
    print("─" * 60)

    captured: dict = {}

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
                captured["id_token_captured_at"] = time.time()

            # Capture Firebase API key from any googleapis call that includes ?key=
            from urllib.parse import urlparse, parse_qs
            if "googleapis.com" in request.url and "key=" in request.url:
                params = parse_qs(urlparse(request.url).query)
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
                    # Also check nested structure
                    user = body.get("users", [{}])[0] if "users" in body else {}
                    stm = user.get("providerUserInfo", [])
                    _ = stm  # not what we want here
                except Exception:
                    pass

            if req.method not in ("POST", "PUT", "PATCH"):
                return
            if "add_endpoint" in captured:
                return
            if "speechify" not in url:
                return

            # Exclude Firestore read/aggregate operations — these fire on page
            # load and are not what we want
            read_ops = ("runAggregationQuery", "runQuery", ":batchGet", ":listen")
            if any(op in url for op in read_ops):
                return

            # Heuristic: URL contains a path segment associated with adding content
            add_keywords = ("/items", "/queue", "/library", "/content", "/import",
                            "/documents/", "/listen", "/feed", "/v1/", "/v2/")
            if not any(kw in url for kw in add_keywords):
                return

            try:
                body = req.post_data or ""
                if not body:
                    return
                captured["add_endpoint"] = url
                captured["add_method"] = req.method
                # Keep only stable, non-host headers
                keep = {"content-type", "accept", "x-client-version",
                        "x-firebase-gmpid", "x-goog-request-params"}
                captured["add_headers"] = {
                    k: v for k, v in req.headers.items() if k.lower() in keep
                }
                captured["add_body_example"] = body
                print(f"\n✓ Captured API endpoint: {url}")
            except Exception:
                pass

        page.on("request", on_request)
        page.on("response", on_response)

        await page.goto("https://app.speechify.com")
        print("\nWaiting... (close the browser when done)\n")

        try:
            await page.wait_for_event("close", timeout=600_000)
        except Exception:
            pass

        # ── Read Firebase tokens from IndexedDB before closing ───────────
        try:
            firebase_data = await _read_firebase_indexeddb(page)
            _extract_firebase_tokens(firebase_data, captured)
        except Exception as e:
            print(f"  (Could not read IndexedDB: {e})")

        await ctx.close()

    # ── Report and persist ───────────────────────────────────────────────
    print("\n── Auth capture results ──")
    print(f"  Firebase API key : {'✓' if captured.get('firebase_api_key') else '✗ missing'}")
    print(f"  Refresh token    : {'✓' if captured.get('refresh_token') else '✗ missing'}")
    print(f"  ID token         : {'✓' if captured.get('id_token') else '✗ missing'}")
    print(f"  Add endpoint     : {'✓ ' + captured['add_endpoint'] if captured.get('add_endpoint') else '✗ missing — add a URL to your library during setup'}")

    if not captured.get("refresh_token"):
        print("\n⚠  No refresh token captured.")
        print("   Approach 1 (API replay) won't work until you re-run auth setup")
        print("   and add a URL while the browser is open.")
        print("   Approach 2 (browser automation) will still work.")

    config.save(captured)
    print(f"\n✓ Saved to {config.AUTH_FILE}")


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
