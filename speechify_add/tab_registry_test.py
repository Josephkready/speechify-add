"""Unit + live tests for the owned-tab registry + orphan sweep (issue #55)."""

import os

import pytest

from speechify_add import tab_registry as tr


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
