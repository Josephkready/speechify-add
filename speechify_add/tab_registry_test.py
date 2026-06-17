"""Unit + live tests for the owned-tab registry + orphan sweep (issue #55)."""

import json
import os
from unittest.mock import AsyncMock, MagicMock

import pytest

from speechify_add import tab_registry as tr


def _fake_urlopen(payload: bytes):
    """A urlopen stand-in usable as a context manager + file-like read()."""
    class _Resp:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def read(self, *a):
            return payload

    def _open(url, timeout=None):
        return _Resp()

    return _open


@pytest.fixture
def reg(tmp_path):
    """A registry path under a tmp dir (real state file untouched)."""
    return tmp_path / "open-tabs.json"


# --- registry file I/O -----------------------------------------------------

def test_record_and_read_roundtrip(reg):
    tr.record_tab("T1", "https://app.speechify.com/", pid=111, now=1.0, path=reg)
    tr.record_tab("T2", "https://app.speechify.com/item/x", pid=222, now=2.0, path=reg)

    state = tr._read_registry(reg)
    assert set(state) == {"T1", "T2"}
    assert state["T1"] == {"pid": 111, "url": "https://app.speechify.com/", "opened_at": 1.0}
    assert state["T2"]["pid"] == 222


def test_forget_tab_removes_entry(reg):
    tr.record_tab("T1", "u", pid=1, path=reg)
    tr.forget_tab("T1", path=reg)
    assert tr._read_registry(reg) == {}


def test_forget_unknown_tab_is_noop(reg):
    tr.record_tab("T1", "u", pid=1, path=reg)
    tr.forget_tab("NOPE", path=reg)
    assert set(tr._read_registry(reg)) == {"T1"}


def test_read_missing_file_returns_empty(reg):
    assert tr._read_registry(reg) == {}


def test_read_corrupt_file_returns_empty(reg):
    reg.parent.mkdir(parents=True, exist_ok=True)
    reg.write_text("{ not json")
    assert tr._read_registry(reg) == {}


def test_registry_path_env_override(tmp_path, monkeypatch):
    target = tmp_path / "custom.json"
    monkeypatch.setenv("SPEECHIFY_ADD_TAB_REGISTRY", str(target))
    assert tr._registry_path() == target


# --- process liveness ------------------------------------------------------

def test_pid_alive_current_process():
    assert tr._pid_alive(os.getpid()) is True


def test_pid_alive_implausible_pid_is_dead():
    # A PID far above any real one on the system.
    assert tr._pid_alive(2 ** 22) is False


def test_pid_alive_zero_is_dead():
    assert tr._pid_alive(0) is False


def test_owner_alive_requires_both_alive_and_speechify(monkeypatch):
    monkeypatch.setattr(tr, "_pid_alive", lambda pid: True)
    monkeypatch.setattr(tr, "_proc_is_speechify", lambda pid: False)
    assert tr._owner_alive(123) is False  # alive but not a speechify proc

    monkeypatch.setattr(tr, "_proc_is_speechify", lambda pid: True)
    assert tr._owner_alive(123) is True


def test_owner_dead_pid_is_not_alive(monkeypatch):
    monkeypatch.setattr(tr, "_pid_alive", lambda pid: False)
    # _proc_is_speechify must not even matter once the pid is gone.
    monkeypatch.setattr(tr, "_proc_is_speechify", lambda pid: pytest.fail("unreached"))
    assert tr._owner_alive(999) is False


# --- the sweep -------------------------------------------------------------

def _stub_cdp(monkeypatch, live_ids, closed_sink, close_ok=True):
    monkeypatch.setattr(tr, "_list_target_ids", lambda cdp_url=None: live_ids)

    def _close(tid, cdp_url=None):
        closed_sink.append(tid)
        return close_ok

    monkeypatch.setattr(tr, "_close_target", _close)


def test_sweep_closes_only_dead_owner_tabs(reg, monkeypatch):
    tr.record_tab("DEAD", "u", pid=999, path=reg)
    tr.record_tab("LIVE", "u", pid=1000, path=reg)
    monkeypatch.setattr(tr, "_owner_alive", lambda pid: pid == 1000)
    closed = []
    _stub_cdp(monkeypatch, {"DEAD", "LIVE"}, closed)

    result = tr.sweep_orphans(path=reg)

    assert result == ["DEAD"]
    assert closed == ["DEAD"]          # the live sibling's tab is untouched
    assert set(tr._read_registry(reg)) == {"LIVE"}


def test_sweep_drops_already_gone_orphan_without_closing(reg, monkeypatch):
    tr.record_tab("GONE", "u", pid=999, path=reg)
    monkeypatch.setattr(tr, "_owner_alive", lambda pid: False)
    closed = []
    _stub_cdp(monkeypatch, set(), closed)  # not present in CDP anymore

    assert tr.sweep_orphans(path=reg) == []
    assert closed == []                    # nothing to close
    assert tr._read_registry(reg) == {}    # stale entry pruned


def test_sweep_keeps_entry_when_close_fails(reg, monkeypatch):
    tr.record_tab("STUCK", "u", pid=999, path=reg)
    monkeypatch.setattr(tr, "_owner_alive", lambda pid: False)
    closed = []
    _stub_cdp(monkeypatch, {"STUCK"}, closed, close_ok=False)

    assert tr.sweep_orphans(path=reg) == []
    assert set(tr._read_registry(reg)) == {"STUCK"}  # retained for retry


def test_sweep_aborts_when_cdp_unreachable(reg, monkeypatch):
    tr.record_tab("DEAD", "u", pid=999, path=reg)
    monkeypatch.setattr(tr, "_owner_alive", lambda pid: False)
    monkeypatch.setattr(tr, "_list_target_ids", lambda cdp_url=None: None)
    monkeypatch.setattr(
        tr, "_close_target",
        lambda tid, cdp_url=None: pytest.fail("must not close when CDP down"),
    )

    assert tr.sweep_orphans(path=reg) == []
    assert set(tr._read_registry(reg)) == {"DEAD"}  # retained, retry next run


def test_sweep_skips_network_when_no_orphans(reg, monkeypatch):
    tr.record_tab("LIVE", "u", pid=1, path=reg)
    monkeypatch.setattr(tr, "_owner_alive", lambda pid: True)
    monkeypatch.setattr(
        tr, "_list_target_ids",
        lambda cdp_url=None: pytest.fail("should not hit CDP with no orphans"),
    )
    assert tr.sweep_orphans(path=reg) == []


def test_sweep_empty_registry_is_noop(reg, monkeypatch):
    monkeypatch.setattr(
        tr, "_list_target_ids",
        lambda cdp_url=None: pytest.fail("should not hit CDP on empty registry"),
    )
    assert tr.sweep_orphans(path=reg) == []


def test_sweep_never_raises_on_internal_error(reg, monkeypatch):
    tr.record_tab("DEAD", "u", pid=999, path=reg)
    monkeypatch.setattr(tr, "_owner_alive", lambda pid: False)

    def _boom(cdp_url=None):
        raise RuntimeError("kaboom")

    monkeypatch.setattr(tr, "_list_target_ids", _boom)
    # Public entry point swallows the error and returns [].
    assert tr.sweep_orphans(path=reg) == []


def test_read_non_dict_file_returns_empty(reg):
    # Valid JSON of the wrong type (a list) hits a different branch than
    # corrupt JSON: it logs a warning and returns {}.
    reg.parent.mkdir(parents=True, exist_ok=True)
    reg.write_text("[]")
    assert tr._read_registry(reg) == {}


def test_record_tab_non_string_target_id_is_noop(reg):
    tr.record_tab(123, "u", path=reg)  # non-str id must never key the registry
    assert tr._read_registry(reg) == {}


# --- _proc_is_speechify (the PID-reuse safety guard) -----------------------

def test_proc_is_speechify_true_for_matching_cmdline(monkeypatch):
    monkeypatch.setattr(
        "pathlib.Path.read_bytes",
        lambda self: b"/usr/bin/python\x00-m\x00speechify_add\x00add\x00",
    )
    assert tr._proc_is_speechify(1234) is True


def test_proc_is_speechify_false_for_other_process(monkeypatch):
    monkeypatch.setattr(
        "pathlib.Path.read_bytes", lambda self: b"/usr/bin/vim\x00notes.txt"
    )
    assert tr._proc_is_speechify(1234) is False


def test_proc_is_speechify_false_when_proc_gone(monkeypatch):
    def _raise(self):
        raise FileNotFoundError()

    monkeypatch.setattr("pathlib.Path.read_bytes", _raise)
    assert tr._proc_is_speechify(1234) is False


def test_proc_is_speechify_conservative_on_oserror(monkeypatch):
    # Can't read cmdline (e.g. EPERM) → assume ours so we never reap a tab
    # out from under a process we can't inspect.
    def _raise(self):
        raise OSError("EPERM")

    monkeypatch.setattr("pathlib.Path.read_bytes", _raise)
    assert tr._proc_is_speechify(1234) is True


# --- CDP HTTP helpers (real urllib path) -----------------------------------

def test_list_target_ids_returns_page_ids(monkeypatch):
    payload = json.dumps([
        {"id": "A", "type": "page"},
        {"id": "B", "type": "page"},
        {"id": "W", "type": "service_worker"},  # not a page
        {"type": "page"},                        # no id
    ]).encode()
    monkeypatch.setattr("urllib.request.urlopen", _fake_urlopen(payload))
    assert tr._list_target_ids() == {"A", "B"}


def test_list_target_ids_none_on_connection_error(monkeypatch):
    def _boom(url, timeout=None):
        raise OSError("connection refused")

    monkeypatch.setattr("urllib.request.urlopen", _boom)
    assert tr._list_target_ids() is None


def test_list_target_ids_none_on_bad_json(monkeypatch):
    monkeypatch.setattr("urllib.request.urlopen", _fake_urlopen(b"{not json"))
    assert tr._list_target_ids() is None


def test_close_target_true_on_success(monkeypatch):
    monkeypatch.setattr("urllib.request.urlopen", _fake_urlopen(b"Target closing"))
    assert tr._close_target("A") is True


def test_close_target_false_on_error(monkeypatch):
    def _boom(url, timeout=None):
        raise OSError("connection refused")

    monkeypatch.setattr("urllib.request.urlopen", _boom)
    assert tr._close_target("A") is False


# --- track_target (register on open, forget on close) ----------------------

async def test_track_target_registers_then_forgets(reg, monkeypatch):
    monkeypatch.setattr(tr, "_registry_path", lambda: reg)
    monkeypatch.setattr(tr, "resolve_target_id", AsyncMock(return_value="TID"))
    page = MagicMock()
    page.url = "https://app.speechify.com/"

    async with tr.track_target(page):
        assert set(tr._read_registry(reg)) == {"TID"}  # registered while open
    assert tr._read_registry(reg) == {}                 # forgotten on exit


async def test_track_target_forgets_on_exception(reg, monkeypatch):
    monkeypatch.setattr(tr, "_registry_path", lambda: reg)
    monkeypatch.setattr(tr, "resolve_target_id", AsyncMock(return_value="TID"))
    page = MagicMock()
    page.url = "u"

    with pytest.raises(ValueError):
        async with tr.track_target(page):
            assert set(tr._read_registry(reg)) == {"TID"}
            raise ValueError("boom")
    assert tr._read_registry(reg) == {}  # forgotten even on error


async def test_track_target_untracked_when_no_target_id(reg, monkeypatch):
    monkeypatch.setattr(tr, "_registry_path", lambda: reg)
    monkeypatch.setattr(tr, "resolve_target_id", AsyncMock(return_value=None))

    async with tr.track_target(MagicMock()):
        pass
    assert tr._read_registry(reg) == {}  # nothing recorded


# --- live: real chrome-hub round-trip --------------------------------------

@pytest.mark.live
async def test_tracked_page_registers_then_forgets_and_closes(reg, monkeypatch):
    monkeypatch.setattr(tr, "_registry_path", lambda: reg)

    async with tr.tracked_page() as page:
        await page.goto("about:blank")
        state = tr._read_registry(reg)
        assert len(state) == 1, "tab should be registered while open"
        tid = next(iter(state))
        assert tid in (tr._list_target_ids() or set())

    # After clean exit: forgotten from the registry and closed in Chrome.
    assert tr._read_registry(reg) == {}
    assert tid not in (tr._list_target_ids() or set())
