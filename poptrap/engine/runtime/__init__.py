"""Browser runtime server, asset loader, and session storage for POPTRAP."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from poptrap.engine.runtime.assets import (
    SECURITY_HEADERS,
    get_web_root,
    resolve_web_asset,
)
from poptrap.engine.runtime.sessions import SessionRecord, SessionStore

if TYPE_CHECKING:
    from poptrap.engine.runtime.server import (
        DEFAULT_BIND_HOST,
        DEFAULT_BIND_PORT,
        ExperienceServer,
        create_runtime_app,
    )

__all__ = [
    "DEFAULT_BIND_HOST",
    "DEFAULT_BIND_PORT",
    "ExperienceServer",
    "SECURITY_HEADERS",
    "SessionRecord",
    "SessionStore",
    "create_runtime_app",
    "get_web_root",
    "resolve_web_asset",
]

_SERVER_EXPORTS: frozenset[str] = frozenset(
    {"DEFAULT_BIND_HOST", "DEFAULT_BIND_PORT", "ExperienceServer", "create_runtime_app"}
)


def __getattr__(name: str) -> Any:
    if name in _SERVER_EXPORTS:
        from poptrap.engine.runtime import server

        return getattr(server, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
