"""Core domain modules for POPTRAP: config, environment, logging, and module specs.

Kept free of eager third-party imports so ``poptrap.core.environment`` and
``poptrap.core.exceptions`` remain importable before runtime dependencies
are verified.
"""

from __future__ import annotations

from poptrap.core.exceptions import (
    ConfigFormatError,
    ConfigNotFoundError,
    ConfigSaveError,
    ConfigurationError,
    ConfigValidationError,
    EnvironmentCheckError,
    MissingDependencyError,
    PopTrapError,
    PrankConfigError,
    PrankEngineError,
    RuntimeServerError,
    SessionIdentifierError,
    SessionLimitError,
    SessionNotFoundError,
    SessionStateError,
    TemplateNotFoundError,
    TemplateRegistrationError,
    UnsupportedPythonError,
    UnsupportedTerminalError,
)

__all__ = [
    "ConfigFormatError",
    "ConfigNotFoundError",
    "ConfigSaveError",
    "ConfigValidationError",
    "ConfigurationError",
    "EnvironmentCheckError",
    "MissingDependencyError",
    "PopTrapError",
    "PrankConfigError",
    "PrankEngineError",
    "RuntimeServerError",
    "SessionIdentifierError",
    "SessionLimitError",
    "SessionNotFoundError",
    "SessionStateError",
    "TemplateNotFoundError",
    "TemplateRegistrationError",
    "UnsupportedPythonError",
    "UnsupportedTerminalError",
]
