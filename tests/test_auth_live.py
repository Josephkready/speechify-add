"""
Live tests for speechify_add/auth.py.

These tests hit real external services. Run with:
    pytest -m live tests/test_auth_live.py

Requirements:
  - ~/.config/speechify-add/auth.json must exist and contain valid credentials
    (run `speechify-add auth setup` first)
  - Network access to securetoken.googleapis.com
"""

import pytest

from speechify_add import auth, config


@pytest.mark.live
def test_live_config_loads_credentials():
    """
    Validates that stored credentials are present and structurally valid.

    External services: None (reads local config file only).
    Cost/time: Free, <1ms.
    Requirements: auth.json must exist with valid fields.
    """
    data = config.load()
    assert data, "No credentials found — run: speechify-add auth setup"
    assert "firebase_api_key" in data, "firebase_api_key missing from auth.json"
    assert "refresh_token" in data, "refresh_token missing from auth.json"


@pytest.mark.live
@pytest.mark.asyncio
async def test_live_get_id_token_returns_string():
    """
    Calls get_id_token() against the real Firebase token endpoint if needed.

    External services: securetoken.googleapis.com (only if token is expired).
    Cost/time: Free (Firebase token refresh), ~1-2s if refresh needed.
    Requirements: Valid auth.json with firebase_api_key and refresh_token.
    """
    token = await auth.get_id_token()
    assert isinstance(token, str)
    assert len(token) > 100, "ID token looks too short to be a real JWT"
    # Verify it has JWT structure (3 dot-separated segments)
    parts = token.split(".")
    assert len(parts) == 3, f"ID token doesn't look like a JWT: {token[:50]}..."
