"""Shared pytest fixtures ensuring hermetic configuration and logging state."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest

from poptrap.core.logging_setup import shutdown_logging
from poptrap.utils.paths import ENV_CONFIG_DIR, ENV_LOG_DIR


@pytest.fixture(autouse=True)
def isolated_runtime_dirs(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> Iterator[tuple[Path, Path]]:
    """Redirect config and log directories to a temporary location per test."""
    config_dir = tmp_path / "user_config"
    log_dir = tmp_path / "user_logs"
    monkeypatch.setenv(ENV_CONFIG_DIR, str(config_dir))
    monkeypatch.setenv(ENV_LOG_DIR, str(log_dir))
    yield config_dir, log_dir
    shutdown_logging()
