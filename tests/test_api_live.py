"""
Live tests for speechify_add/api.py.

These tests hit real external services and require valid credentials.
Run with: pytest -m live tests/test_api_live.py

NOTE: Do NOT run Playwright or browser tests (repo policy).
_get_title relies on verify.get_page_title which uses Playwright — no live test for it.
"""
import pytest

from speechify_add import api, auth


@pytest.fixture(scope="module")
def id_token():
    """
    Fetch a real Firebase ID token from the configured credentials.

    Hits: Firebase token refresh endpoint (auth module).
    Estimated cost/time: ~1 network call, <3s.
    Requirements: valid refresh_token in ~/.config/speechify-add/auth.json.
    """
    import asyncio
    return asyncio.run(auth.get_id_token())


@pytest.mark.live
class TestLiveUserIdExtraction:
    def test_live_user_id_extracted_from_real_token(self, id_token):
        """
        Verify _user_id_from_token works with a real Firebase JWT.

        Hits: Firebase token endpoint (via id_token fixture).
        Estimated cost/time: shared fixture, no additional calls.
        Requirements: valid credentials in auth.json.
        """
        uid = api._user_id_from_token(id_token)
        # Real Firebase UIDs are non-empty strings
        assert uid
        assert isinstance(uid, str)
        assert len(uid) > 4
