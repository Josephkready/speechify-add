"""
Owned-tab registry + dead-process orphan sweep (issue #55).

chrome-hub's ``async_new_page()`` reliably closes the page in its ``finally``
(client.log shows 363 clean closes, 0 failures) — *when the finally runs*. It
doesn't run when the speechify-add process is killed before the ``async with``
exits: batch / parallel uploads that time out, ``Ctrl-C``, OOM. The page is
then stranded in the shared Chrome, and chrome-hub's own reaper can't catch it
while a persistent CDP consumer is connected (chrome-hub#57).

A consumer can't clean up after its own ``kill -9`` *during* that run, but it
can clean up on the *next* run. This module:

1. Records every tab speechify-add opens (CDP target id + the owning PID) to a
   small JSON file under ``~/.local/state/speechify-add/``.
2. On the next CLI startup, ``sweep_orphans()`` closes any recorded tab whose
   owning process is no longer a live speechify-add process — those are
   definitively leaks from a killed prior run. Tabs owned by a *live* sibling
   process (concurrent batch uploads) are never touched, because their PID is
   still alive.

The sweep talks to chrome-hub over the CDP HTTP endpoints only (``/json/list``
and ``/json/close/<id>``) so it stays import-light and Playwright-free; only
``tracked_page`` needs Playwright, and it imports it lazily.
"""

import json
import logging
import os
import tempfile
import time
import urllib.request
from contextlib import asynccontextmanager, contextmanager
from pathlib import Path

import fcntl

log = logging.getLogger(__name__)

# chrome-hub binds CDP here (chrome_hub.browser.CDP_URL). Hardcoded rather
# than imported so this module stays free of the Playwright import chain.
CDP_URL = os.environ.get("CHROME_HUB_CDP_URL", "http://127.0.0.1:9222")

DEFAULT_REGISTRY_PATH = (
    Path.home() / ".local" / "state" / "speechify-add" / "open-tabs.json"
)


def _registry_path() -> Path:
    """Path to the owned-tab registry file.

    Resolved at call time (env override + monkeypatchable) so tests can point
    it at a tmp dir without touching the real state file.
    """
    override = os.environ.get("SPEECHIFY_ADD_TAB_REGISTRY")
    return Path(override) if override else DEFAULT_REGISTRY_PATH


# ---------------------------------------------------------------------------
# Registry file I/O (cross-process safe via flock)
# ---------------------------------------------------------------------------

@contextmanager
def _locked(path: Path):
    """Hold an exclusive flock for a read-modify-write of the registry.

    Parallel speechify-add invocations share one registry file; the lock
    serializes their record/forget/remove operations so concurrent writes
    don't clobber each other.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    lock_path = path.with_name(path.name + ".lock")
    with open(lock_path, "w") as lf:
        fcntl.flock(lf, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(lf, fcntl.LOCK_UN)


def _read_registry(path: Path | None = None) -> dict[str, dict]:
    """Return the registry mapping ``{target_id: {pid, url, opened_at}}``.

    Returns ``{}`` on a missing or corrupt file — a lost registry just means
    the next sweep has nothing to act on, never an error.
    """
    path = path or _registry_path()
    try:
        with open(path) as f:
            data = json.load(f)
        if isinstance(data, dict):
            return data
        log.warning("tab registry at %s is not a dict — discarding", path)
    except FileNotFoundError:
        pass
    except (OSError, json.JSONDecodeError) as e:
        log.warning("failed to read tab registry %s: %s", path, e)
    return {}


def _write_unlocked(state: dict[str, dict], path: Path) -> None:
    """Atomically replace the registry file. Caller must hold ``_locked``."""
    with tempfile.NamedTemporaryFile(
        mode="w", dir=str(path.parent), prefix=".open-tabs-", suffix=".tmp",
        delete=False,
    ) as tf:
        json.dump(state, tf)
        tmp_path = Path(tf.name)
    os.replace(tmp_path, path)


def record_tab(
    target_id: str, url: str, *, pid: int | None = None,
    now: float | None = None, path: Path | None = None,
) -> None:
    """Record a tab this process just opened. Best-effort — never raises."""
    if not isinstance(target_id, str):
        return  # only ever key the registry on real string target ids
    path = path or _registry_path()
    pid = os.getpid() if pid is None else pid
    now = time.time() if now is None else now
    try:
        with _locked(path):
            state = _read_registry(path)
            state[target_id] = {"pid": pid, "url": url, "opened_at": now}
            _write_unlocked(state, path)
    except OSError as e:
        log.debug("record_tab(%s) failed: %s", target_id, e)


def forget_tab(target_id: str, *, path: Path | None = None) -> None:
    """Drop a tab from the registry after it has been closed cleanly."""
    _remove_tabs([target_id], path=path)


def _remove_tabs(target_ids, *, path: Path | None = None) -> None:
    """Remove ``target_ids`` from the registry. Best-effort — never raises."""
    target_ids = list(target_ids)
    if not target_ids:
        return
    path = path or _registry_path()
    try:
        with _locked(path):
            state = _read_registry(path)
            changed = False
            for tid in target_ids:
                if state.pop(tid, None) is not None:
                    changed = True
            if changed:
                _write_unlocked(state, path)
    except OSError as e:
        log.debug("_remove_tabs failed: %s", e)


# ---------------------------------------------------------------------------
# Process-liveness checks
# ---------------------------------------------------------------------------

def _pid_alive(pid: int) -> bool:
    """True if a process with ``pid`` currently exists."""
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        # Exists but owned by another user — still "alive".
        return True
    return True


def _proc_is_speechify(pid: int) -> bool:
    """True if ``pid``'s cmdline looks like a speechify-add process.

    Guards against PID reuse: a dead speechify-add PID recycled by an unrelated
    process should not protect its stale tab from the sweep. On any read error
    other than "process is gone", err conservative (treat as ours → don't reap)
    so we never close a tab out from under a live process we can't inspect.
    """
    try:
        raw = Path(f"/proc/{pid}/cmdline").read_bytes()
    except (FileNotFoundError, ProcessLookupError):
        return False
    except OSError:
        return True
    return b"speechify" in raw.lower()


def _owner_alive(pid: int) -> bool:
    """True if the tab's owner is a live speechify-add process.

    Safety property: this only ever returns False for a PID that is genuinely
    not a running speechify-add process. ``os.kill(pid, 0)`` cannot report a
    live process as dead, so a tab a sibling run is actively using is never
    reaped. The worst case is a missed cleanup (PID recycled to another
    speechify-add run), which is safe — the tab is retried next sweep.
    """
    return _pid_alive(pid) and _proc_is_speechify(pid)


# ---------------------------------------------------------------------------
# CDP HTTP helpers (no Playwright)
# ---------------------------------------------------------------------------

def _list_target_ids(cdp_url: str | None = None) -> set[str] | None:
    """Return the set of page-target ids open in chrome-hub.

    Returns ``None`` (not an empty set) when chrome-hub is unreachable, so the
    sweep can distinguish "no tabs" from "couldn't check" and avoid dropping
    registry entries it failed to verify.
    """
    url = (cdp_url or CDP_URL).rstrip("/") + "/json/list"
    try:
        with urllib.request.urlopen(url, timeout=5) as resp:
            data = json.load(resp)
    except (OSError, json.JSONDecodeError) as e:
        log.debug("CDP /json/list unreachable: %s", e)
        return None
    return {t["id"] for t in data if t.get("type") == "page" and "id" in t}


def _close_target(target_id: str, cdp_url: str | None = None) -> bool:
    """Close a tab by CDP target id via ``/json/close``. Returns success."""
    url = (cdp_url or CDP_URL).rstrip("/") + f"/json/close/{target_id}"
    try:
        with urllib.request.urlopen(url, timeout=5) as resp:
            resp.read()
        return True
    except OSError as e:
        log.debug("CDP close of %s failed: %s", target_id, e)
        return False


# ---------------------------------------------------------------------------
# The sweep
# ---------------------------------------------------------------------------

def sweep_orphans(
    *, path: Path | None = None, cdp_url: str | None = None,
) -> list[str]:
    """Close registered tabs whose owning process is dead. Returns closed ids.

    Safe to call on every CLI startup: a no-op when the registry is empty or
    holds only live-owned tabs (it doesn't even hit the network then). Never
    raises — cleanup must not break the command the user actually ran.
    """
    try:
        return _sweep_orphans(path=path, cdp_url=cdp_url)
    except Exception as e:  # defensive: a sweep must never break the CLI
        log.debug("sweep_orphans failed: %s", e)
        return []


def _sweep_orphans(
    *, path: Path | None = None, cdp_url: str | None = None,
) -> list[str]:
    path = path or _registry_path()
    state = _read_registry(path)
    if not state:
        return []

    orphans = [
        tid for tid, meta in state.items()
        if not _owner_alive(int(meta.get("pid", -1)))
    ]
    if not orphans:
        return []

    live_ids = _list_target_ids(cdp_url)
    if live_ids is None:
        # chrome-hub unreachable — keep the entries and retry next run.
        return []

    closed: list[str] = []
    to_drop: list[str] = []
    for tid in orphans:
        if tid not in live_ids:
            # Already gone (closed elsewhere) — just drop the stale entry.
            to_drop.append(tid)
        elif _close_target(tid, cdp_url):
            closed.append(tid)
            to_drop.append(tid)
        # else: close failed — keep the entry so a later sweep retries it.

    _remove_tabs(to_drop, path=path)
    if closed:
        log.info(
            "swept %d orphaned speechify tab(s) from dead runs: %s",
            len(closed), ", ".join(closed),
        )
    return closed


# ---------------------------------------------------------------------------
# Page tracking context managers
# ---------------------------------------------------------------------------

def _safe_url(page) -> str:
    try:
        return page.url or ""
    except Exception:
        return ""


async def _resolve_target_id(page) -> str | None:
    """Return the CDP target id backing a Playwright page, or None.

    Uses a short-lived CDP session (``Target.getTargetInfo``) and detaches it
    immediately. Best-effort: on any failure the page simply goes untracked.
    """
    try:
        session = await page.context.new_cdp_session(page)
        try:
            info = await session.send("Target.getTargetInfo")
            target_id = info["targetInfo"]["targetId"]
            # Guard against a non-string id (real CDP always returns a str;
            # this keeps a mocked page in tests from poisoning the registry).
            return target_id if isinstance(target_id, str) else None
        finally:
            await session.detach()
    except Exception as e:
        log.debug("could not resolve CDP target id: %s", e)
        return None


@asynccontextmanager
async def track_target(page):
    """Register ``page``'s target for the block, forget it on exit.

    Wraps any page (not just chrome-hub's ``async_new_page``) so the
    fresh-context verify path can be tracked too.
    """
    target_id = await _resolve_target_id(page)
    if target_id:
        record_tab(target_id, _safe_url(page))
    try:
        yield
    finally:
        if target_id:
            forget_tab(target_id)


@asynccontextmanager
async def tracked_page():
    """Drop-in for chrome-hub's ``async_new_page`` that tracks the tab.

    The page is registered in the owned-tab registry on open and forgotten on
    clean close, so a future ``sweep_orphans()`` can reclaim it if this process
    dies before the close runs.
    """
    from chrome_hub import async_new_page  # lazy: keeps module Playwright-free

    async with async_new_page() as page:
        async with track_target(page):
            yield page
