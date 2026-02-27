"""
Approach 1: Consumer API replay.

Replays the HTTP request captured during auth setup, substituting in the
target URL. Refreshes the Firebase ID token automatically before each call.
"""

import json
import re

import httpx

from . import auth, config

_URL_KEYS = {"url", "link", "href", "uri", "source", "address", "path"}


async def add_url(url: str) -> None:
    cfg = config.load()

    endpoint = cfg.get("add_endpoint")
    if not endpoint:
        raise RuntimeError(
            "No API endpoint captured. Run: speechify-add auth setup "
            "and add a URL to your library when prompted."
        )

    id_token = await auth.get_id_token()

    headers = _build_headers(cfg, id_token)
    body = _build_body(cfg.get("add_body_example", ""), url)

    async with httpx.AsyncClient() as client:
        resp = await client.request(
            method=cfg.get("add_method", "POST"),
            url=endpoint,
            content=body,
            headers=headers,
            timeout=30,
        )

    # TODO: Add retry with backoff for transient HTTP errors (429, 5xx)
    if resp.status_code not in (200, 201, 204):
        raise RuntimeError(
            f"API returned HTTP {resp.status_code}: {resp.text[:300]}"
        )


def _build_headers(cfg: dict, id_token: str) -> dict:
    # Start from captured headers, override auth
    headers = dict(cfg.get("add_headers", {}))
    headers["authorization"] = f"Bearer {id_token}"
    if "content-type" not in {k.lower() for k in headers}:
        headers["content-type"] = "application/json"
    return headers


def _build_body(body_example: str, url: str) -> str:
    """
    Substitute the new URL into the captured request body.
    Tries JSON-aware replacement first, falls back to regex.
    """
    if not body_example:
        return json.dumps({"url": url, "type": "url"})

    try:
        data = json.loads(body_example)
        data = _replace_url_value(data, url)
        return json.dumps(data)
    except (json.JSONDecodeError, ValueError):
        # Fall back: replace any bare https?:// string in the raw body
        return re.sub(r'https?://[^\s"\'\\]+', url, body_example)


def _replace_url_value(obj, new_url: str):
    """
    Recursively replace URL-like string values in a parsed JSON structure.
    Targets keys that suggest they hold a URL, and any string value that
    starts with http.
    """
    if isinstance(obj, dict):
        result = {}
        for k, v in obj.items():
            if k.lower() in _URL_KEYS and isinstance(v, str) and v.startswith("http"):
                result[k] = new_url
            else:
                result[k] = _replace_url_value(v, new_url)
        return result

    if isinstance(obj, list):
        return [_replace_url_value(item, new_url) for item in obj]

    # TODO: This is aggressive — it replaces ANY string starting with "http",
    # which could clobber non-URL values like "https-proxy" or "http2-enabled".
    # Consider a stricter URL regex check.
    if isinstance(obj, str) and obj.startswith("http"):
        return new_url

    return obj
