"""Platform-independent runtime utilities for POPTRAP."""

from __future__ import annotations

from poptrap.utils.paths import (
    ensure_private_directory,
    get_default_config_path,
    get_log_file_path,
    get_project_root,
    get_user_config_dir,
    get_user_config_path,
    get_user_log_dir,
)
from poptrap.utils.platform import (
    MIN_TERMINAL_COLUMNS,
    MIN_TERMINAL_ROWS,
    RECOMMENDED_TERMINAL_COLUMNS,
    RECOMMENDED_TERMINAL_ROWS,
    ColorDepth,
    OperatingSystem,
    PlatformInfo,
    TerminalCapabilities,
    detect_platform,
    detect_terminal_capabilities,
    is_termux_environment,
    is_wsl_environment,
)

__all__ = [
    "ColorDepth",
    "MIN_TERMINAL_COLUMNS",
    "MIN_TERMINAL_ROWS",
    "OperatingSystem",
    "PlatformInfo",
    "RECOMMENDED_TERMINAL_COLUMNS",
    "RECOMMENDED_TERMINAL_ROWS",
    "TerminalCapabilities",
    "detect_platform",
    "detect_terminal_capabilities",
    "ensure_private_directory",
    "get_default_config_path",
    "get_log_file_path",
    "get_project_root",
    "get_user_config_dir",
    "get_user_config_path",
    "get_user_log_dir",
    "is_termux_environment",
    "is_wsl_environment",
]
