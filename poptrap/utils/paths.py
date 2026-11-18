"""Cross-platform local filesystem path resolution for POPTRAP.

All configuration and log paths remain strictly local to the current user
account and support explicit environment variable overrides for portability
and isolated testing.
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from pathlib import Path

from poptrap.utils.platform import OperatingSystem, detect_platform

APP_DIR_SLUG: str = "poptrap"
CONFIG_FILENAME: str = "config.toml"
PRESETS_DIRNAME: str = "presets"
DEFAULT_CONFIG_RELATIVE: tuple[str, str] = ("config", "default.toml")
LOG_FILENAME: str = "poptrap.log"

ENV_CONFIG_DIR: str = "POPTRAP_CONFIG_DIR"
ENV_LOG_DIR: str = "POPTRAP_LOG_DIR"
ENV_PRESETS_DIR: str = "POPTRAP_PRESETS_DIR"


def get_project_root() -> Path:
    """Return the root directory containing the ``poptrap`` package."""
    return Path(__file__).resolve().parents[2]


def get_default_config_path(project_root: Path | None = None) -> Path:
    """Return the path to the bundled ``config/default.toml`` file."""
    base = get_project_root() if project_root is None else project_root
    return base.joinpath(*DEFAULT_CONFIG_RELATIVE)


def _resolve_env_path(value: str) -> Path:
    """Expand user home markers and normalize an environment-supplied path."""
    return Path(value.strip()).expanduser()


def get_user_config_dir(
    *,
    os_type: OperatingSystem | None = None,
    env: Mapping[str, str] | None = None,
    home: Path | None = None,
) -> Path:
    """Return the platform-appropriate per-user configuration directory."""
    current_env = os.environ if env is None else env
    override = current_env.get(ENV_CONFIG_DIR, "").strip()
    if override:
        return _resolve_env_path(override)

    target_os = detect_platform(env=current_env).os_type if os_type is None else os_type
    user_home = Path.home() if home is None else home

    if target_os is OperatingSystem.WINDOWS:
        appdata = current_env.get("APPDATA", "").strip()
        base = _resolve_env_path(appdata) if appdata else user_home / "AppData" / "Roaming"
        return base / APP_DIR_SLUG

    if target_os is OperatingSystem.MACOS:
        xdg = current_env.get("XDG_CONFIG_HOME", "").strip()
        if xdg:
            return _resolve_env_path(xdg) / APP_DIR_SLUG
        return user_home / "Library" / "Application Support" / APP_DIR_SLUG

    if target_os in {OperatingSystem.LINUX, OperatingSystem.TERMUX}:
        xdg = current_env.get("XDG_CONFIG_HOME", "").strip()
        base = _resolve_env_path(xdg) if xdg else user_home / ".config"
        return base / APP_DIR_SLUG

    return user_home / f".{APP_DIR_SLUG}" / "config"


def get_user_config_path(
    *,
    os_type: OperatingSystem | None = None,
    env: Mapping[str, str] | None = None,
    home: Path | None = None,
) -> Path:
    """Return the default per-user ``config.toml`` file path."""
    return get_user_config_dir(os_type=os_type, env=env, home=home) / CONFIG_FILENAME


def get_user_presets_dir(
    *,
    os_type: OperatingSystem | None = None,
    env: Mapping[str, str] | None = None,
    home: Path | None = None,
) -> Path:
    """Return the platform-appropriate per-user local Studio presets directory."""
    current_env = os.environ if env is None else env
    override = current_env.get(ENV_PRESETS_DIR, "").strip()
    if override:
        return _resolve_env_path(override)
    return (
        get_user_config_dir(os_type=os_type, env=env, home=home) / PRESETS_DIRNAME
    )


def get_user_log_dir(
    *,
    os_type: OperatingSystem | None = None,
    env: Mapping[str, str] | None = None,
    home: Path | None = None,
) -> Path:
    """Return the platform-appropriate per-user log directory."""
    current_env = os.environ if env is None else env
    override = current_env.get(ENV_LOG_DIR, "").strip()
    if override:
        return _resolve_env_path(override)

    target_os = detect_platform(env=current_env).os_type if os_type is None else os_type
    user_home = Path.home() if home is None else home

    if target_os is OperatingSystem.WINDOWS:
        local_appdata = current_env.get("LOCALAPPDATA", "").strip()
        base = (
            _resolve_env_path(local_appdata)
            if local_appdata
            else user_home / "AppData" / "Local"
        )
        return base / APP_DIR_SLUG / "logs"

    if target_os is OperatingSystem.MACOS:
        return user_home / "Library" / "Logs" / APP_DIR_SLUG

    if target_os in {OperatingSystem.LINUX, OperatingSystem.TERMUX}:
        xdg_state = current_env.get("XDG_STATE_HOME", "").strip()
        base = _resolve_env_path(xdg_state) if xdg_state else user_home / ".local" / "state"
        return base / APP_DIR_SLUG / "logs"

    return user_home / f".{APP_DIR_SLUG}" / "logs"


def get_log_file_path(
    *,
    os_type: OperatingSystem | None = None,
    env: Mapping[str, str] | None = None,
    home: Path | None = None,
) -> Path:
    """Return the default per-user application log file path."""
    return get_user_log_dir(os_type=os_type, env=env, home=home) / LOG_FILENAME


def ensure_private_directory(path: Path) -> Path:
    """Create ``path`` if absent, restricting permissions to the current user on POSIX."""
    created = not path.exists()
    path.mkdir(parents=True, exist_ok=True)
    if created and os.name == "posix":
        try:
            path.chmod(0o700)
        except OSError:
            # Filesystems such as shared mounts or Android external storage may ignore chmod.
            pass
    return path


def format_display_path(path: Path, *, home: Path | None = None) -> str:
    """Format ``path`` compactly using ``~`` when located under the user's home."""
    user_home = Path.home() if home is None else home
    try:
        relative = path.resolve().relative_to(user_home.resolve())
    except (OSError, ValueError):
        return str(path)
    rel_str = str(relative)
    if rel_str == ".":
        return "~"
    sep = "\\" if os.name == "nt" else "/"
    return f"~{sep}{rel_str}"
