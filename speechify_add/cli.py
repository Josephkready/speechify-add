"""
CLI entry point.

Usage:
    speechify-add add <url>
    speechify-add add --file urls.txt
    speechify-add add --stdin
    speechify-add auth setup
    speechify-add auth refresh
"""

import asyncio
import re
import sys

import click
import httpx


def _run(coro):
    return asyncio.run(coro)


CONTEXT_SETTINGS = {"help_option_names": ["-h", "--help"]}


@click.group(context_settings=CONTEXT_SETTINGS)
def cli():
    """Add articles to your Speechify listening queue."""


# ---------------------------------------------------------------------------
# add command
# ---------------------------------------------------------------------------

@cli.command()
@click.argument("url", required=False)
@click.option("--file", "-f", "file_path", type=click.Path(exists=True),
              help="File of URLs to add (one per line, # for comments)")
@click.option("--stdin", "from_stdin", is_flag=True,
              help="Read URLs from stdin")
@click.option(
    "--mode",
    type=click.Choice(["browser", "api"]),
    default="browser",
    show_default=True,
    help=(
        "browser: opens a Chromium window using your saved session (default). "
        "api: direct API call — experimental."
    ),
)
@click.pass_context
def add(ctx, url, file_path, from_stdin, mode):
    """Add one or more URLs to your Speechify library."""
    urls = _collect_urls(url, file_path, from_stdin)
    if not urls:
        click.echo(ctx.get_help())
        return

    # Use batch mode (single browser session) when we have multiple URLs
    if len(urls) > 1 and mode == "browser":
        _run(_add_batch(urls))
        return

    success = fail = 0
    for u in urls:
        try:
            _run(_add_one(u, mode))
            click.echo(f"✓  {u}")
            success += 1
        except Exception as e:
            click.echo(f"✗  {u}\n   {e}", err=True)
            fail += 1

    if len(urls) > 1:
        click.echo(f"\n{success} added, {fail} failed")

    if fail:
        sys.exit(1)


def _collect_urls(url, file_path, from_stdin) -> list[str]:
    if url:
        return [url]
    if file_path:
        with open(file_path) as f:
            return [
                line.strip()
                for line in f
                if line.strip() and not line.strip().startswith("#")
            ]
    if from_stdin:
        return [
            line.strip()
            for line in sys.stdin
            if line.strip()
        ]
    return []


_GOOGLE_DOCS_RE = re.compile(
    r"https://docs\.google\.com/document/d/([a-zA-Z0-9_-]+)"
)


def _is_google_doc(url: str) -> bool:
    return _GOOGLE_DOCS_RE.match(url) is not None


def _google_doc_export_url(url: str) -> str:
    """Convert a Google Docs URL to its plain-text export URL."""
    m = _GOOGLE_DOCS_RE.match(url)
    if not m:
        raise ValueError(f"Not a Google Docs URL: {url}")
    doc_id = m.group(1)
    return f"https://docs.google.com/document/d/{doc_id}/export?format=txt"


def _extract_title_from_text(text: str) -> str:
    """Extract a title from the first non-empty line of text."""
    for line in text.splitlines():
        stripped = line.strip()
        if stripped:
            return stripped[:120]
    return ""


async def _fetch_google_doc_text(url: str) -> str:
    """Download a Google Doc as plain text via the public export endpoint."""
    export_url = _google_doc_export_url(url)
    async with httpx.AsyncClient(follow_redirects=True, timeout=30) as client:
        resp = await client.get(export_url)
    if resp.status_code in (401, 403):
        raise RuntimeError(
            f"Google Doc is private (HTTP {resp.status_code}). "
            "Make the document publicly accessible, or copy the text manually "
            "and use: speechify-add text --stdin -t \"Title\""
        )
    if resp.status_code == 404:
        raise RuntimeError(
            "Google Doc not found (404). Check that the URL is correct."
        )
    if resp.status_code >= 400:
        raise RuntimeError(
            f"Google Doc export failed (HTTP {resp.status_code}). "
            "Try sharing the document publicly or exporting manually."
        )
    return resp.text


async def _precheck_url(url: str) -> None:
    """HTTP HEAD pre-check: raise if the URL requires authentication."""
    try:
        async with httpx.AsyncClient(follow_redirects=True, timeout=15) as client:
            resp = await client.head(url)
    except httpx.HTTPError:
        # Network errors are not auth problems — let Speechify try it
        return
    if resp.status_code in (401, 403):
        raise RuntimeError(
            f"URL returned HTTP {resp.status_code} (unauthorized/forbidden). "
            "Speechify won't be able to access this content. "
            "Download the text and use: speechify-add text --stdin -t \"Title\""
        )



async def _add_one(url: str, mode: str) -> None:
    from . import api, browser

    # Google Docs: export as text and upload via the text path
    if _is_google_doc(url):
        text = await _fetch_google_doc_text(url)
        title = _extract_title_from_text(text)
        await browser.add_text(text, title=title)
        return

    # For all other URLs, pre-check accessibility
    await _precheck_url(url)

    if mode == "api":
        await api.add_url(url)
    else:
        await browser.add_url(url)


async def _add_batch(urls: list[str]) -> None:
    """Add multiple URLs using a single browser session (much faster)."""
    from .browser import BrowserSession

    success = fail = 0
    async with BrowserSession() as session:
        for url in urls:
            try:
                if _is_google_doc(url):
                    text = await _fetch_google_doc_text(url)
                    title = _extract_title_from_text(text)
                    await session.add_text(text, title=title)
                else:
                    await _precheck_url(url)
                    await session.add_url(url)
                click.echo(f"✓  {url}")
                success += 1
            except Exception as e:
                click.echo(f"✗  {url}\n   {e}", err=True)
                fail += 1

    if len(urls) > 1:
        click.echo(f"\n{success} added, {fail} failed")

    if fail:
        sys.exit(1)


# ---------------------------------------------------------------------------
# text command
# ---------------------------------------------------------------------------

@cli.command()
@click.option("--title", "-t", default="", help="Title for the Speechify document")
@click.option("--file", "-f", "file_path", type=click.Path(exists=True),
              help="Read text from a file (plain text or markdown)")
@click.option("--stdin", "from_stdin", is_flag=True,
              help="Read text from stdin")
@click.pass_context
def text(ctx, title, file_path, from_stdin):
    """Add raw text or markdown to your Speechify library.

    Returns the Speechify document URL on success.

    Examples:
      speechify-add text -f digest.md -t "HN Digest 2026-02-27"
      cat summary.md | speechify-add text --stdin -t "Daily Summary"
    """
    content = _collect_text(file_path, from_stdin)
    if not content:
        click.echo(ctx.get_help())
        return

    try:
        doc_url = _run(_add_text(content, title))
        click.echo(doc_url)
    except Exception as e:
        click.echo(f"Error: {e}", err=True)
        sys.exit(1)


def _collect_text(file_path, from_stdin) -> str:
    if file_path:
        with open(file_path) as f:
            return f.read()
    if from_stdin:
        return sys.stdin.read()
    return ""


async def _add_text(content: str, title: str) -> str:
    from . import browser
    return await browser.add_text(content, title=title)


# ---------------------------------------------------------------------------
# delete command
# ---------------------------------------------------------------------------

_SPEECHIFY_ITEM_RE = re.compile(
    r"(?:https?://app\.speechify\.com/item/)?"
    r"([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})",
    re.IGNORECASE,
)


def _parse_item_id(item: str) -> str:
    """Extract a Speechify item UUID from a full URL or bare UUID string."""
    m = _SPEECHIFY_ITEM_RE.search(item)
    if not m:
        raise click.BadParameter(
            f"Could not parse a Speechify item UUID from: {item}\n"
            "Expected a UUID like 783247eb-59c9-4ade-9027-e01f8d77d959 "
            "or a URL like https://app.speechify.com/item/<uuid>"
        )
    return m.group(1)


@cli.command()
@click.argument("item", required=True)
@click.option("--mode", type=click.Choice(["browser", "api"]), default="browser",
              show_default=True, help="browser: uses chrome-hub (default). api: Firebase API (requires valid token).")
@click.option("--debug", is_flag=True, help="Save debug screenshots")
def delete(item, mode, debug):
    """Delete an item from your Speechify library.

    ITEM can be a full URL (https://app.speechify.com/item/UUID)
    or just the UUID.
    """
    item_id = _parse_item_id(item)
    try:
        _run(_do_delete(item_id, mode=mode, debug=debug))
        click.echo(f"Deleted {item_id}")
    except Exception as e:
        click.echo(f"Error: {e}", err=True)
        sys.exit(1)


async def _do_delete(item_id: str, mode: str = "browser", debug: bool = False) -> None:
    if mode == "api":
        from . import api
        await api.delete_item(item_id)
    else:
        from . import browser
        await browser.delete_item(item_id, debug=debug)


# ---------------------------------------------------------------------------
# verify command
# ---------------------------------------------------------------------------

@cli.command()
@click.argument("query")
def verify(query):
    """
    Search your Speechify library and confirm an article is there.

    QUERY can be a URL (title is fetched automatically) or any search term.

    Examples:
      speechify-add verify https://arstechnica.com/...
      speechify-add verify "cosmic distance ladder"
    """
    _run(_do_verify(query))


async def _do_verify(query: str):
    from . import verify as verify_module

    # If it looks like a URL, fetch the page title to search by
    search_term = query
    if query.startswith("http"):
        click.echo(f"Fetching title for {query} ...")
        title = await verify_module.get_page_title(query)
        if title and not any(bad in title.lower() for bad in ("404", "not found", "error")):
            # Use first 6 meaningful words to avoid over-specific matching
            words = [w for w in title.split() if len(w) > 2][:6]
            search_term = " ".join(words)
            click.echo(f"Searching for: \"{search_term}\"")
        elif title:
            click.echo(f"⚠  URL returned: \"{title}\" — the page may not exist")
            raise SystemExit(1)
        else:
            # Fall back to the URL's path segments as search term
            from urllib.parse import urlparse
            path = urlparse(query).path.strip("/").replace("-", " ").replace("/", " ")
            search_term = " ".join(path.split()[:5])
            click.echo(f"Could not fetch title. Searching for: \"{search_term}\"")

    click.echo("Opening Speechify library...")
    results = await verify_module.search_library(search_term)

    if not results:
        click.echo(f"\n✗  No results found for \"{search_term}\"")
        raise SystemExit(1)

    click.echo(f"\n{len(results)} result(s) found:\n")
    for item in results:
        click.echo(f"  ✓  {item['title']}")
        if item["meta"]:
            click.echo(f"     {item['meta']}")
    click.echo()


# ---------------------------------------------------------------------------
# progress command
# ---------------------------------------------------------------------------

@cli.command()
@click.argument("title", required=False)
@click.option("--batch", "batch_json", default=None,
              help='JSON array of {"id": "uuid", "title": "..."} objects')
@click.option("--batch-file", "batch_file", type=click.Path(exists=True), default=None,
              help="Path to a JSON file containing the batch array")
def progress(title, batch_json, batch_file):
    """Query listen progress (0-100) for one or more Speechify library items.

    Single mode:  speechify-add progress "Article title here"
    Batch mode:   speechify-add progress --batch '[{"id":"uuid","title":"..."}]'
                  speechify-add progress --batch-file /tmp/items.json

    Single mode prints an integer (0-100) or exits 1 if not found.
    Batch mode prints a JSON array: [{"id": "uuid", "listen_pct": 73}, ...]
    """
    if batch_json or batch_file:
        try:
            _run(_do_progress_batch(batch_json, batch_file))
        except Exception as e:
            click.echo(f"Error: {e}", err=True)
            sys.exit(1)
    elif title:
        try:
            _run(_do_progress_single(title))
        except SystemExit:
            raise
        except Exception as e:
            click.echo(f"Error: {e}", err=True)
            sys.exit(1)
    else:
        click.echo("Provide a TITLE or use --batch / --batch-file", err=True)
        sys.exit(1)


async def _do_progress_single(title: str):
    from . import verify as verify_module
    results = await verify_module.search_library_batch([title])
    pct = results[0]
    if pct is None:
        click.echo(f"Not found: {title}", err=True)
        sys.exit(1)
    click.echo(pct)


async def _do_progress_batch(batch_json: str | None, batch_file: str | None):
    import json as _json
    from . import verify as verify_module

    if batch_file:
        with open(batch_file) as f:
            items = _json.load(f)
    else:
        items = _json.loads(batch_json)

    titles = [item.get("title", "") for item in items]
    pcts = await verify_module.search_library_batch(titles)

    output = [
        {"id": item.get("id", ""), "listen_pct": pct}
        for item, pct in zip(items, pcts)
    ]
    click.echo(_json.dumps(output))


# ---------------------------------------------------------------------------
# debug command
# ---------------------------------------------------------------------------

@cli.command()
def debug():
    """
    Take screenshots of the Speechify UI to diagnose selector issues.

    Saves screenshots + an element dump to:
    ~/.config/speechify-add/debug-screenshots/
    """
    _run(_do_debug())


async def _do_debug():
    from . import browser as browser_module
    screenshot_dir = await browser_module.screenshot_walkthrough()
    click.echo(f"Screenshots saved to: {screenshot_dir}")
    click.echo("Files:")
    for f in sorted(screenshot_dir.iterdir()):
        click.echo(f"  {f.name}")


# ---------------------------------------------------------------------------
# auth subcommands
# ---------------------------------------------------------------------------

@cli.group()
def auth():
    """Manage Speechify authentication."""


@auth.command("setup")
def auth_setup():
    """
    One-time auth setup — opens a browser window.

    Log in to Speechify, add any URL to your library, then close the browser.
    Captures your tokens and the API endpoint for future headless use.
    """
    _run(_do_auth_setup())


async def _do_auth_setup():
    from . import auth as auth_module
    await auth_module.setup()


@auth.command("refresh")
def auth_refresh():
    """Refresh the stored Firebase ID token."""
    _run(_do_auth_refresh())


async def _do_auth_refresh():
    from . import auth as auth_module
    await auth_module.refresh_and_print()
    click.echo("✓  Token refreshed (valid for ~1 hour)")


if __name__ == "__main__":
    cli()
