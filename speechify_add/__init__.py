"""Public Python API for speechify-add.

These are the supported entrypoints for downstream code (medium-fetch,
book-to-speechify, hn-digest, etc.). They are sync wrappers around the async
browser flows so callers don't need to manage an event loop.

Each function returns the Speechify item URL when observable.

Examples:
    from pathlib import Path
    from speechify_add import upload_text, upload_file, upload_url

    url = upload_text("hello world", title="Test")
    url = upload_file(Path("article.pdf"), title="On Re-reading LOTR")
    url = upload_url("https://example.com/article")
"""
from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Union

from . import browser as _browser

__all__ = ["upload_text", "upload_file", "upload_url"]


def upload_text(text: str, title: str = "") -> str:
    """Upload raw text or markdown to your Speechify library.

    Returns the Speechify item URL on success.
    """
    return asyncio.run(_browser.add_text(text, title=title))


def upload_file(path: Union[str, Path], title: str = "") -> str:
    """Upload a file (.pdf/.epub/.html/.htm/.txt) to your Speechify library.

    The browser's persistent profile is used for auth. ``title`` is best-effort:
    if the import dialog exposes a title field it will be filled, otherwise
    Speechify extracts the title from the file's own metadata.

    Returns the Speechify item URL on success.
    """
    validated = _browser._validate_file_path(Path(path))
    return asyncio.run(_browser.add_file(validated, title=title))


def upload_url(url: str) -> str:
    """Add a URL to your Speechify library via the Paste Link flow.

    Returns the Speechify item URL if Speechify redirects within ~15 seconds;
    returns an empty string if the URL was queued but the redirect wasn't
    observable.
    """
    return asyncio.run(_browser.add_url(url))
