"""Safe filesystem resolver and security headers for POPTRAP web assets."""

from __future__ import annotations

from pathlib import Path

from poptrap.core.exceptions import PrankEngineError

_ALLOWED_EXTENSIONS: dict[str, str] = {
    ".html": "text/html; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".js": "text/javascript; charset=utf-8",
    ".svg": "image/svg+xml",
}

SECURITY_HEADERS: dict[str, str] = {
    "Content-Security-Policy": (
        "default-src 'self'; "
        "script-src 'self'; "
        "style-src 'self'; "
        "img-src 'self' data:; "
        "connect-src 'self' ws: wss:; "
        "media-src 'none'; "
        "object-src 'none'; "
        "base-uri 'none'; "
        "form-action 'none'"
    ),
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "no-referrer",
    "Permissions-Policy": (
        "camera=(), microphone=(), geolocation=(), usb=(), bluetooth=(), payment=()"
    ),
    "Cache-Control": "no-store",
}


def get_web_root() -> Path:
    """Return the verified root directory containing ``poptrap/web`` assets."""
    root = Path(__file__).resolve().parents[2] / "web"
    if not root.is_dir():
        raise PrankEngineError(
            f"Browser experience assets directory missing at '{root}'.",
            hint="Reinstall POPTRAP so the poptrap/web assets are present.",
        )
    return root


def resolve_web_asset(relative_path: str, *, web_root: Path | None = None) -> tuple[Path, str]:
    """Safely resolve a static web asset and its MIME type.

    Prevents path traversal, hidden file access, and disallowed file types.

    Raises:
        FileNotFoundError: When the requested asset does not exist or is disallowed.
    """
    base = (get_web_root() if web_root is None else web_root).resolve()
    cleaned = relative_path.strip().lstrip("/")
    if not cleaned or "\x00" in cleaned or "\\" in cleaned:
        raise FileNotFoundError("Invalid asset path.")

    parts = Path(cleaned).parts
    if any(part.startswith(".") or part == ".." for part in parts):
        raise FileNotFoundError("Disallowed asset path segment.")

    candidate = (base / cleaned).resolve()
    if not candidate.is_relative_to(base) or not candidate.is_file():
        raise FileNotFoundError(f"Web asset not found: {cleaned}")

    media_type = _ALLOWED_EXTENSIONS.get(candidate.suffix.lower())
    if media_type is None:
        raise FileNotFoundError(f"Disallowed asset extension: {candidate.suffix}")

    return candidate, media_type
