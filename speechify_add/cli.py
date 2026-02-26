"""
CLI entry point.

Usage:
    speechify-add <url>
    speechify-add --file urls.txt
    speechify-add --stdin
    speechify-add auth setup
    speechify-add auth refresh
"""

import asyncio
import sys

import click


def _run(coro):
    return asyncio.run(coro)


# ---------------------------------------------------------------------------
# Main command
# ---------------------------------------------------------------------------

@click.group(invoke_without_command=True, context_settings={"help_option_names": ["-h", "--help"]})
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
def cli(ctx, url, file_path, from_stdin, mode):
    """Add articles to your Speechify listening queue."""
    if ctx.invoked_subcommand:
        return

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
# Auth subcommands
# ---------------------------------------------------------------------------

@cli.group()
def auth():
    """Manage Speechify authentication."""


@auth.command("setup")
def auth_setup():
    """
    Run the one-time auth setup.

    Opens a browser window — log in to Speechify, then add any URL to your
    library so we can capture the API endpoint. Close the browser when done.
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
