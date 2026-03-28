"""
Add a URL to Speechify via the real API flow:

  1. Upload an empty placeholder file to Firebase Storage → get a download token
  2. POST to the sdk-createFileFromWebLink Cloud Function, which fetches the page
     and adds it to the user's library

This is the reliable, headless approach — no browser needed.
"""

import json
import time
import urllib.parse
import uuid as _uuid

import httpx
import jwt as pyjwt

from . import auth

_CLOUD_FN = (
    "https://us-central1-speechifymobile.cloudfunctions.net/sdk-createFileFromWebLink"
)
_ARCHIVE_FN = (
    "https://us-central1-speechifymobile.cloudfunctions.net/sdk-firestore-archiveLibraryItem"
)
_STORAGE_BASE = (
    "https://firebasestorage.googleapis.com/v0/b/speechifymobile.appspot.com/o"
)


async def add_url(url: str) -> None:
    id_token = await auth.get_id_token()
    user_id = _user_id_from_token(id_token)
    doc_id = str(_uuid.uuid4())
    storage_path = f"multiplatform/import/{user_id}/{doc_id}"

    # Fetch page title (best-effort; fall back to hostname)
    title = await _get_title(url)

    async with httpx.AsyncClient(timeout=30) as client:
        # ── Step 1: create empty placeholder in Firebase Storage ─────────
        download_token = await _upload_empty(client, id_token, storage_path)

        source_stored_url = (
            f"{_STORAGE_BASE}/{urllib.parse.quote(storage_path, safe='')}?"
            f"alt=media&token={download_token}"
        )

        # ── Step 2: call the Cloud Function ──────────────────────────────
        resp = await client.post(
            _CLOUD_FN,
            headers={
                "Authorization": f"Bearer {id_token}",
                "Content-Type": "application/json",
            },
            json={
                "userId": user_id,
                "client": "WEB_APP",
                "dateAdded": int(time.time()),
                "recordTitle": title,
                "url": url,
                "sourceStoredURL": source_stored_url,
                "storageBucket": "speechifymobile.appspot.com",
                "storagePath": storage_path,
                "recordUid": doc_id,
                "type": "WEB",
            },
        )

    if resp.status_code not in (200, 201, 204):
        raise RuntimeError(
            f"sdk-createFileFromWebLink returned HTTP {resp.status_code}: "
            f"{resp.text[:300]}"
        )


async def delete_item(item_id: str) -> None:
    """Delete (archive) a Speechify library item by its UUID."""
    id_token = await auth.get_id_token()

    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.post(
            _ARCHIVE_FN,
            headers={
                "Authorization": f"Bearer {id_token}",
                "Content-Type": "application/json",
            },
            json={"rootItemId": item_id},
        )

    if resp.status_code not in (200, 201, 204):
        raise RuntimeError(
            f"archiveLibraryItem returned HTTP {resp.status_code}: "
            f"{resp.text[:300]}"
        )


async def _upload_empty(client: httpx.AsyncClient, id_token: str,
                        storage_path: str) -> str:
    """Upload a 0-byte placeholder to Firebase Storage. Returns the download token."""
    upload_url = (
        f"{_STORAGE_BASE}?name={urllib.parse.quote(storage_path, safe='')}"
    )
    resp = await client.post(
        upload_url,
        headers={
            "Authorization": f"Bearer {id_token}",
            "Content-Type": "application/octet-stream",
            "Content-Length": "0",
        },
        content=b"",
    )
    if resp.status_code not in (200, 201):
        raise RuntimeError(
            f"Firebase Storage upload returned HTTP {resp.status_code}: "
            f"{resp.text[:200]}"
        )
    data = resp.json()
    token = data.get("downloadTokens")
    if not token:
        raise RuntimeError(
            f"Firebase Storage did not return a download token. Response: "
            f"{json.dumps(data)[:200]}"
        )
    return token


def _user_id_from_token(id_token: str) -> str:
    """Decode the Firebase JWT payload and return the user_id (uid)."""
    try:
        payload = pyjwt.decode(
            id_token,
            options={"verify_signature": False},
            algorithms=["RS256"],
        )
        uid = payload.get("user_id") or payload.get("sub")
        if not uid:
            raise ValueError("no user_id/sub in JWT payload")
        return uid
    except Exception as e:
        raise RuntimeError(f"Could not extract user ID from token: {e}") from e


async def _get_title(url: str) -> str:
    """Fetch the page <title>; fall back to the URL's hostname."""
    try:
        from . import verify
        title = await verify.get_page_title(url)
        if title:
            return title
    except Exception:
        pass
    return urllib.parse.urlparse(url).hostname or url
