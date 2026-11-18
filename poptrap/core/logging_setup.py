"""Local rotating file logging setup for POPTRAP.

Never attaches console handlers by default so terminal UI rendering remains
completely clean and uncorrupted.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from logging.handlers import RotatingFileHandler
from pathlib import Path

from poptrap.core.config import LogLevel
from poptrap.utils.paths import ensure_private_directory, get_log_file_path

LOGGER_NAME: str = "poptrap"
MAX_LOG_BYTES: int = 1_048_576  # 1 MiB
LOG_BACKUP_COUNT: int = 3
LOG_FORMAT: str = "%(asctime)s | %(levelname)-8s | %(name)s | %(message)s"
LOG_DATE_FORMAT: str = "%Y-%m-%dT%H:%M:%S"


@dataclass(frozen=True, slots=True)
class LoggingState:
    """Result of initializing the application logger."""

    level: LogLevel
    log_path: Path | None
    file_active: bool
    warning: str | None = None


def get_logger(name: str | None = None) -> logging.Logger:
    """Return the POPTRAP root logger or a named child logger."""
    if not name or name == LOGGER_NAME:
        return logging.getLogger(LOGGER_NAME)
    if name.startswith(f"{LOGGER_NAME}."):
        return logging.getLogger(name)
    return logging.getLogger(f"{LOGGER_NAME}.{name}")


def _resolve_numeric_level(level: LogLevel | str) -> tuple[LogLevel, int]:
    """Normalize a ``LogLevel`` or string name into ``(LogLevel, int)``."""
    normalized = level if isinstance(level, LogLevel) else LogLevel(level.strip().upper())
    numeric = getattr(logging, normalized.value, logging.INFO)
    return normalized, int(numeric)


def configure_logging(
    level: LogLevel | str = LogLevel.INFO,
    *,
    log_path: Path | None = None,
) -> LoggingState:
    """Initialize local rotating file logging with safe fallback to ``NullHandler``."""
    shutdown_logging()

    resolved_level, numeric_level = _resolve_numeric_level(level)
    logger = logging.getLogger(LOGGER_NAME)
    logger.setLevel(numeric_level)
    logger.propagate = False

    target = get_log_file_path() if log_path is None else log_path.expanduser()
    try:
        ensure_private_directory(target.parent)
        handler = RotatingFileHandler(
            filename=target,
            maxBytes=MAX_LOG_BYTES,
            backupCount=LOG_BACKUP_COUNT,
            encoding="utf-8",
        )
        handler.setLevel(numeric_level)
        handler.setFormatter(logging.Formatter(fmt=LOG_FORMAT, datefmt=LOG_DATE_FORMAT))
        logger.addHandler(handler)
        return LoggingState(
            level=resolved_level,
            log_path=target,
            file_active=True,
            warning=None,
        )
    except OSError as exc:
        logger.addHandler(logging.NullHandler())
        return LoggingState(
            level=resolved_level,
            log_path=None,
            file_active=False,
            warning=f"File logging unavailable at '{target}' ({exc}); continuing without log file.",
        )


def set_log_level(level: LogLevel | str) -> LogLevel:
    """Update the live log level on the POPTRAP logger and its handlers."""
    resolved_level, numeric_level = _resolve_numeric_level(level)
    logger = logging.getLogger(LOGGER_NAME)
    logger.setLevel(numeric_level)
    for handler in logger.handlers:
        if not isinstance(handler, logging.NullHandler):
            handler.setLevel(numeric_level)
    return resolved_level


def shutdown_logging() -> None:
    """Flush, close, and detach all handlers attached to the POPTRAP logger."""
    logger = logging.getLogger(LOGGER_NAME)
    for handler in list(logger.handlers):
        try:
            handler.flush()
        except OSError:
            pass
        try:
            handler.close()
        except OSError:
            pass
        logger.removeHandler(handler)
