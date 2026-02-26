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
    type=click.Choice(["auto", "api", "browser"]),
    default="auto",
    show_default=True,
    help=(
        "auto: try API replay first, fall back to browser. "
        "api: API replay only (fast, requires endpoint capture). "
        "browser: browser automation only (slower, more robust)."
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
        return

    if mode == "browser":
        await browser.add_url(url)
        return

    # auto: try API first, fall back to browser
    from . import config
    cfg = config.load()
    if cfg.get("add_endpoint"):
        try:
            await api.add_url(url)
            return
        except Exception as e:
            click.echo(f"  API mode failed ({e}), retrying via browser...", err=True)

    await browser.add_url(url)


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
