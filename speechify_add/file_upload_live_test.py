"""
Live end-to-end test for file upload.

Uploads a uniquely-named text file to a real Speechify account, verifies the
item appears in the library, then deletes it. Cleanup runs even if the test
body fails.

Run with:
    pytest -m live speechify_add/file_upload_live_test.py

Requirements:
  - `speechify-add auth setup` has been run
  - chrome-hub is reachable
  - Network access to Speechify
"""
from __future__ import annotations

import re
import time
from pathlib import Path

import pytest

import speechify_add
from speechify_add import api, verify

_ITEM_UUID_RE = re.compile(
    r"/item/([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})",
    re.IGNORECASE,
)


def _extract_item_uuid(item_url: str) -> str:
    m = _ITEM_UUID_RE.search(item_url)
    if not m:
        raise AssertionError(
            f"upload_file did not return a /item/<uuid> URL. Got: {item_url!r}"
        )
    return m.group(1)


@pytest.mark.live
def test_upload_file_round_trip(tmp_path):
    """Upload a TXT file, verify it lands in the library, then delete it.

    The unique title (timestamp-based) ensures the search match isn't ambiguous
    against pre-existing library items.

    Cost/time: ~30-90s. Hits real Speechify infra.
    """
    title = f"speechify-add live test {int(time.time())}"
    body = (
        f"# {title}\n\n"
        "This is a test document uploaded by the speechify-add test suite. "
        "It should be deleted automatically when the test exits.\n"
    )
    src = tmp_path / "speechify-add-live-test.txt"
    src.write_text(body, encoding="utf-8")

    item_url: str | None = None
    try:
        item_url = speechify_add.upload_file(src, title=title)
        assert item_url, "upload_file returned an empty URL"
        item_uuid = _extract_item_uuid(item_url)

        # Give Speechify's indexer a moment to surface the new item in search.
        time.sleep(5)

        import asyncio
        results = asyncio.run(verify.search_library(title))
        matches = [r for r in results if title in r.get("title", "")]
        assert matches, (
            f"Uploaded item not found in library search for {title!r}. "
            f"upload_file returned {item_url!r}; search returned {results!r}."
        )
    finally:
        if item_url:
            try:
                uuid = _extract_item_uuid(item_url)
                import asyncio
                asyncio.run(api.delete_item(uuid))
            except Exception as cleanup_err:  # noqa: BLE001
                pytest.fail(
                    f"Cleanup failed for {item_url!r}: {cleanup_err}. "
                    "You may need to delete this item manually."
                )
