"""Inspection surfaces.

Two renderers over the same data accessors in `data.py`:

  app.py    NiceGUI (Vue + ES modules + websocket). Richer, but will not
            initialise inside VS Code's webview or behind a path-prefixing
            proxy — that produces "Your browser does not support ES modules".
  plain.py  Zero JavaScript, server-rendered, Python stdlib only. Works
            anywhere, and needs no optional dependency at all.

Submodules are imported lazily via `importlib`, so `biomedical.ui.plain` does
not require NiceGUI to be installed. That is the entire point of a fallback, and
an eager `from .app import main` here defeated it.
"""

from __future__ import annotations

import importlib
from typing import Any

__all__ = ["main", "plain_main"]

_LAZY = {"app", "plain", "data", "theme", "guards"}


def main() -> Any:
    """Launch the NiceGUI surface. Requires `nicegui`."""
    return importlib.import_module("biomedical.ui.app").main()


def plain_main() -> Any:
    """Launch the zero-JavaScript surface. Requires nothing optional."""
    return importlib.import_module("biomedical.ui.plain").main()


def __getattr__(name: str) -> Any:
    if name in _LAZY:
        mod = importlib.import_module(f"biomedical.ui.{name}")
        globals()[name] = mod          # cache, so __getattr__ is not re-entered
        return mod
    raise AttributeError(f"module 'biomedical.ui' has no attribute {name!r}")
