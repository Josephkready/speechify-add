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
import sys

import click


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
                if line.strip() and not line.startswith("#")
            ]
    if from_stdin:
        return [
            line.strip()
            for line in sys.stdin
            if line.strip()
        ]
    return []


async def _add_one(url: str, mode: str) -> None:
    from . import api, browser

    if mode == "api":
        await api.add_url(url)
    else:
        await browser.add_url(url)


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
