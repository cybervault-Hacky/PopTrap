"""POPTRAP's application identity, shared by the CLI, runtime and build."""

from __future__ import annotations

from poptrap._version import __version__ as __version__

APP_NAME: str = "POPTRAP"

__all__ = ["APP_NAME", "__version__"]
