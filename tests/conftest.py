"""Shared fixtures for the speechify-add test suite."""

import pytest


@pytest.fixture(autouse=True)
def _no_startup_tab_sweep(monkeypatch):
    """Neutralize the CLI startup orphan-sweep (issue #55) for these tests.

    ``cli()`` calls ``tab_registry.sweep_orphans()`` on every invocation; under
    ``CliRunner`` that would reach the real chrome-hub / state file and could
    close a developer's actual tabs. Stub it so the suite stays hermetic.
    The sweep itself is covered directly in ``speechify_add/tab_registry_test.py``.
    """
    monkeypatch.setattr(
        "speechify_add.tab_registry.sweep_orphans", lambda *a, **k: []
    )
