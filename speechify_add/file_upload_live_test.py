"""
Live end-to-end tests for file upload.

Each test uploads a unique file to a real Speechify account, asserts the
upload returns a well-formed library URL, then archives it via Speechify's
HTTP API. Cleanup runs even if the test body fails. Successful archive
proves the item actually existed in Speechify's backend (the API 4xx's on
unknown UUIDs).

Two separate tests rather than one parametrize:
  - chrome-hub's per-page orphan cleanup occasionally hangs when two
    `async_new_page()` calls land back-to-back inside one Python process.
    Running each scenario as its own test (and ideally with
    ``pytest --forked`` so each forks its own subprocess) sidesteps that
    hang and keeps the suite reliable.

Run with:
    pytest -m live speechify_add/file_upload_live_test.py
    # Recommended — one fresh process per test:
    pytest -m live --forked speechify_add/file_upload_live_test.py

These tests **must** be run before merging any change that touches the
browser-automation upload flow — they're our only guard against Speechify
rotating its DOM selectors out from under us.

Requirements:
  - `speechify-add auth setup` has been run
  - chrome-hub is reachable
  - Network access to Speechify
  - The PDF test requires `reportlab` (install via `pip install -e .[dev]`)
  - `--forked` requires `pytest-forked` (also in `[dev]`)
"""
from __future__ import annotations

import asyncio
import re
import time
from pathlib import Path

import pytest

import speechify_add
from speechify_add import api

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


def _make_txt(tmp_path: Path, title: str) -> Path:
    body = (
        f"# {title}\n\n"
        "This is a test document uploaded by the speechify-add test suite. "
        "It should be deleted automatically when the test exits.\n"
    )
    src = tmp_path / "speechify-add-live-test.txt"
    src.write_text(body, encoding="utf-8")
    return src


def _make_pdf(tmp_path: Path, title: str) -> Path:
    pytest.importorskip(
        "reportlab", reason="reportlab is required for the PDF live test"
    )
    from reportlab.lib.pagesizes import LETTER  # noqa: WPS433
    from reportlab.pdfgen import canvas  # noqa: WPS433

    src = tmp_path / "speechify-add-live-test.pdf"
    c = canvas.Canvas(str(src), pagesize=LETTER)
    c.setTitle(title)
    c.setAuthor("speechify-add live test")
    c.setFont("Helvetica-Bold", 18)
    c.drawString(72, 720, title)
    c.setFont("Helvetica", 12)
    c.drawString(72, 690, "This is a test PDF uploaded by the speechify-add test suite.")
    c.drawString(72, 670, "It should be deleted automatically when the test exits.")
    c.showPage()
    c.save()
    return src


def _run_round_trip(src: Path) -> None:
    """Upload `src`, assert the returned URL parses, then archive it.

    Successful archive (HTTP 2xx from Speechify's archiveLibraryItem Cloud
    Function) confirms the item exists server-side. Cleanup runs in a
    finally so a failed assertion still removes the artifact.
    """
    item_url: str | None = None
    try:
        item_url = speechify_add.upload_file(src)
        assert item_url, "upload_file returned an empty URL"
        item_uuid = _extract_item_uuid(item_url)
        asyncio.run(api.delete_item(item_uuid))
        item_url = None
    finally:
        if item_url:
            try:
                uuid = _extract_item_uuid(item_url)
                asyncio.run(api.delete_item(uuid))
            except Exception as cleanup_err:  # noqa: BLE001
                pytest.fail(
                    f"Cleanup failed for {item_url!r}: {cleanup_err}. "
                    "You may need to delete this item manually."
                )


@pytest.mark.live
def test_upload_txt_round_trip(tmp_path):
    """Upload a TXT, assert it lands, archive it. Cost: ~20-30s."""
    title = f"speechify-add live test txt {int(time.time())}"
    _run_round_trip(_make_txt(tmp_path, title))


@pytest.mark.live
def test_upload_pdf_round_trip(tmp_path):
    """Upload a PDF, assert it lands, archive it. Cost: ~20-30s.

    Skipped when reportlab isn't installed; install via
    ``pip install -e .[dev]``.
    """
    title = f"speechify-add live test pdf {int(time.time())}"
    _run_round_trip(_make_pdf(tmp_path, title))
