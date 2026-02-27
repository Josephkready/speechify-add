"""Shared test setup — mock third-party dependencies that aren't installed in CI."""

import sys
import types
from unittest.mock import MagicMock


def _ensure_mock_module(name):
    """Insert a MagicMock module into sys.modules if not already importable."""
    if name not in sys.modules:
        sys.modules[name] = MagicMock()


# Mock third-party modules before any speechify_add imports
for _mod in (
    "httpx",
    "click",
    "playwright",
    "playwright.async_api",
    "pyjwt",
    "jwt",
):
    _ensure_mock_module(_mod)


# ---------------------------------------------------------------------------
# Make click decorators pass-through so CLI functions remain callable
# ---------------------------------------------------------------------------

def _passthrough_decorator(*args, **kwargs):
    """A decorator that returns the function unchanged."""
    def wrapper(f):
        return f
    # Support being called with or without arguments
    if args and callable(args[0]):
        return args[0]
    return wrapper


class _ClickGroupResult:
    """Mimics a click.Group so .command() and .group() work as decorators."""

    def __init__(self, func):
        self._func = func
        # Copy function attrs so the object is callable
        self.__name__ = getattr(func, '__name__', 'group')
        self.__call__ = func

    def command(self, *args, **kwargs):
        return _passthrough_decorator

    def group(self, *args, **kwargs):
        def deco(f):
            return _ClickGroupResult(f)
        return deco

    def __call__(self, *args, **kwargs):
        return self._func(*args, **kwargs)


def _click_group(**kwargs):
    """Mock for @click.group() that returns a _ClickGroupResult."""
    def deco(f):
        return _ClickGroupResult(f)
    return deco


_click = sys.modules["click"]
_click.group = _click_group
_click.command = _passthrough_decorator
_click.argument = _passthrough_decorator
_click.option = _passthrough_decorator
_click.pass_context = _passthrough_decorator
_click.Choice = lambda choices: choices
_click.Path = lambda **kw: str
_click.echo = MagicMock()

# Ensure speechify_add package is importable from the repo root
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
