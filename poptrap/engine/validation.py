"""Validation helpers for session identifiers, prank configs, and client events."""

from __future__ import annotations

import re
import secrets
from collections.abc import Mapping
from typing import TYPE_CHECKING, Any

from pydantic import ValidationError

from poptrap.core.exceptions import PrankConfigError, SessionIdentifierError
from poptrap.engine.models import (
    MAX_CUSTOM_MESSAGE_ITEM_LENGTH,
    MAX_CUSTOM_MESSAGES,
    ClientEvent,
    PrankConfig,
)

if TYPE_CHECKING:
    from poptrap.engine.templates.base import PrankTemplate

# 16 random bytes encoded with URL-safe base64 yield 22 unpadded characters (128 bits).
SESSION_TOKEN_BYTES: int = 16
_SESSION_ID_RE = re.compile(r"^[A-Za-z0-9_-]{20,32}$")


def generate_session_id() -> str:
    """Generate a cryptographically random, URL-safe unique session identifier."""
    return secrets.token_urlsafe(SESSION_TOKEN_BYTES)


def validate_session_id(session_id: Any) -> str:
    """Validate that ``session_id`` matches the strict POPTRAP token grammar.

    Raises:
        SessionIdentifierError: If the identifier is empty, malformed, or unsafe.
    """
    if not isinstance(session_id, str):
        raise SessionIdentifierError(
            "Session identifier must be a string.",
            hint="Use the exact session URL issued by POPTRAP.",
        )
    candidate = session_id.strip()
    if not _SESSION_ID_RE.fullmatch(candidate):
        raise SessionIdentifierError(
            "Invalid session identifier format.",
            hint="Session identifiers must be 20–32 URL-safe alphanumeric characters.",
        )
    return candidate


def format_pydantic_errors(exc: ValidationError) -> tuple[str, ...]:
    """Convert a Pydantic ``ValidationError`` into concise ``field: message`` strings."""
    formatted: list[str] = []
    for err in exc.errors():
        loc = ".".join(str(part) for part in err.get("loc", ())) or "config"
        raw_msg = str(err.get("msg", "Invalid value"))
        if raw_msg.startswith("Value error, "):
            raw_msg = raw_msg.removeprefix("Value error, ")
        formatted.append(f"{loc}: {raw_msg}")
    return tuple(formatted)


def validate_prank_config(
    data: PrankConfig | Mapping[str, Any],
    *,
    template: PrankTemplate | None = None,
) -> PrankConfig:
    """Validate raw configuration data and optional template-specific bounds.

    Raises:
        PrankConfigError: When any configuration field or safety limit is violated.
    """
    if isinstance(data, PrankConfig):
        config = data
    elif isinstance(data, Mapping):
        try:
            config = PrankConfig.model_validate(dict(data))
        except ValidationError as exc:
            errors = format_pydantic_errors(exc)
            raise PrankConfigError(
                "; ".join(errors),
                field_errors=errors,
                hint="Adjust the configuration values within allowed limits and retry.",
            ) from exc
    else:
        raise PrankConfigError(
            "Prank configuration must be a mapping or PrankConfig instance.",
            field_errors=("config: Expected an object/mapping.",),
        )

    if template is not None:
        template.validate_config(config)

    return config


def parse_semicolon_messages(raw: str) -> tuple[str, ...]:
    """Split an optional semicolon-delimited popup message string into validated items."""
    stripped = raw.strip()
    if not stripped:
        return ()
    parts = [segment.strip() for segment in stripped.split(";") if segment.strip()]
    if len(parts) > MAX_CUSTOM_MESSAGES:
        raise PrankConfigError(
            f"At most {MAX_CUSTOM_MESSAGES} popup messages are allowed.",
            field_errors=(
                f"custom_messages: Maximum {MAX_CUSTOM_MESSAGES} items supported.",
            ),
        )
    for idx, item in enumerate(parts, 1):
        if len(item) > MAX_CUSTOM_MESSAGE_ITEM_LENGTH:
            raise PrankConfigError(
                f"Popup message #{idx} exceeds {MAX_CUSTOM_MESSAGE_ITEM_LENGTH} characters.",
                field_errors=(
                    f"custom_messages.{idx}: Must be <={MAX_CUSTOM_MESSAGE_ITEM_LENGTH} characters.",
                ),
            )
    return tuple(parts)


def parse_positive_int_input(raw: str, *, field_name: str, label: str) -> int:
    """Parse a user-entered integer text field strictly (no floats or signs tricks)."""
    cleaned = raw.strip()
    if not cleaned:
        raise PrankConfigError(
            f"{label} is required.",
            field_errors=(f"{field_name}: Value cannot be empty.",),
        )
    if cleaned.startswith("-"):
        try:
            return int(cleaned)
        except ValueError as exc:
            raise PrankConfigError(
                f"{label} must be a whole number.",
                field_errors=(f"{field_name}: Expected an integer.",),
            ) from exc
    if not cleaned.isdigit():
        raise PrankConfigError(
            f"{label} must be a whole number.",
            field_errors=(f"{field_name}: Expected an integer.",),
        )
    return int(cleaned)


def validate_client_event(raw: Any) -> ClientEvent:
    """Validate an incoming recipient WebSocket message against the allowlist.

    Raises:
        PrankConfigError: If the payload is not a recognized client event.
    """
    if not isinstance(raw, Mapping):
        raise PrankConfigError(
            "Client message must be a JSON object.",
            field_errors=("event: Expected JSON object.",),
        )
    try:
        return ClientEvent.model_validate(dict(raw))
    except ValidationError as exc:
        errors = format_pydantic_errors(exc)
        raise PrankConfigError(
            f"Unsupported client event: {'; '.join(errors)}",
            field_errors=errors,
        ) from exc
