"""Structured exception hierarchy for POPTRAP."""

from __future__ import annotations

from pathlib import Path


class PopTrapError(Exception):
    """Base exception for all expected POPTRAP application errors."""

    def __init__(self, message: str, *, hint: str | None = None) -> None:
        super().__init__(message)
        self.message = message
        self.hint = hint

    def format_user_message(self) -> str:
        """Return a clean human-readable message including optional recovery guidance."""
        if self.hint:
            return f"{self.message}\nGuidance: {self.hint}"
        return self.message


class ConfigurationError(PopTrapError):
    """Base exception for configuration loading, validation, or saving failures."""

    def __init__(
        self,
        message: str,
        *,
        path: Path | None = None,
        hint: str | None = None,
    ) -> None:
        super().__init__(message, hint=hint)
        self.path = path


class ConfigNotFoundError(ConfigurationError):
    """Raised in strict mode when a requested configuration file does not exist."""


class ConfigFormatError(ConfigurationError):
    """Raised when a configuration file cannot be read or parsed as valid TOML."""


class ConfigValidationError(ConfigurationError):
    """Raised when parsed configuration values violate the POPTRAP schema."""

    def __init__(
        self,
        message: str,
        *,
        field_errors: tuple[str, ...] = (),
        path: Path | None = None,
        hint: str | None = None,
    ) -> None:
        super().__init__(message, path=path, hint=hint)
        self.field_errors = field_errors


class ConfigSaveError(ConfigurationError):
    """Raised when writing configuration to disk fails."""


class EnvironmentCheckError(PopTrapError):
    """Base exception for host runtime or dependency verification failures."""


class UnsupportedPythonError(EnvironmentCheckError):
    """Raised when the running Python interpreter is older than required."""


class MissingDependencyError(EnvironmentCheckError):
    """Raised when one or more required runtime dependencies are unavailable."""

    def __init__(
        self,
        message: str,
        *,
        missing: tuple[str, ...] = (),
        hint: str | None = None,
    ) -> None:
        super().__init__(message, hint=hint)
        self.missing = missing


class UnsupportedTerminalError(EnvironmentCheckError):
    """Raised when the current terminal cannot support the interactive TUI."""

    def __init__(
        self,
        message: str,
        *,
        reasons: tuple[str, ...] = (),
        hint: str | None = None,
    ) -> None:
        super().__init__(message, hint=hint)
        self.reasons = reasons


class PrankEngineError(PopTrapError):
    """Base exception for prank engine validation, session, and runtime failures."""


class TemplateNotFoundError(PrankEngineError):
    """Raised when an unknown prank template identifier is requested."""


class TemplateRegistrationError(PrankEngineError):
    """Raised when registering a duplicate or invalid prank template."""


class PrankConfigError(PrankEngineError):
    """Raised when a prank session configuration fails validation."""

    def __init__(
        self,
        message: str,
        *,
        field_errors: tuple[str, ...] = (),
        hint: str | None = None,
    ) -> None:
        super().__init__(message, hint=hint)
        self.field_errors = field_errors


class SessionNotFoundError(PrankEngineError):
    """Raised when a requested session does not exist or has been cleaned up."""


class SessionIdentifierError(PrankEngineError):
    """Raised when a supplied session identifier fails format validation."""


class SessionStateError(PrankEngineError):
    """Raised when a lifecycle transition is invalid for the current session state."""


class SessionLimitError(PrankEngineError):
    """Raised when the maximum concurrent active session cap is reached."""


class RuntimeServerError(PrankEngineError):
    """Raised when starting or stopping the local browser experience server fails."""


class StudioError(PrankEngineError):
    """Base exception for Phase 4 Prank Studio validation, composition, and preset errors."""


class StudioValidationError(PrankConfigError):
    """Raised when a Prank Studio configuration or stage sequence fails validation."""


class PresetError(StudioError):
    """Base exception for local preset storage operations."""

    def __init__(
        self,
        message: str,
        *,
        path: Path | None = None,
        hint: str | None = None,
    ) -> None:
        super().__init__(message, hint=hint)
        self.path = path


class PresetIdentifierError(PresetError):
    """Raised when a preset identifier is malformed or attempts path traversal."""


class PresetNotFoundError(PresetError):
    """Raised when a requested local preset file does not exist."""


class PresetExistsError(PresetError):
    """Raised when saving or renaming would overwrite an existing preset without confirmation."""

    def __init__(
        self,
        message: str,
        *,
        existing_id: str,
        path: Path | None = None,
        hint: str | None = None,
    ) -> None:
        super().__init__(message, path=path, hint=hint)
        self.existing_id = existing_id


class PresetFormatError(PresetError):
    """Raised when a preset file cannot be parsed or fails schema validation."""


class PresetStorageError(PresetError):
    """Raised when reading, writing, or deleting a local preset fails."""

