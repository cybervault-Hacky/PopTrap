"""Operating system and terminal capability detection.

Uses only the Python standard library so checks can run before importing
third-party packages and never execute external shell commands.
"""

from __future__ import annotations

import codecs
import os
import platform as stdlib_platform
import shutil
import sys
from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum

MIN_TERMINAL_COLUMNS: int = 44
MIN_TERMINAL_ROWS: int = 12
RECOMMENDED_TERMINAL_COLUMNS: int = 64
RECOMMENDED_TERMINAL_ROWS: int = 18

_WINDOWS_VT_MIN_BUILD: int = 10586
_UNICODE_CODEC_PREFIXES: tuple[str, ...] = ("utf-8", "utf-16", "utf-32")


class OperatingSystem(StrEnum):
    """Normalized operating system targets recognized by POPTRAP."""

    LINUX = "linux"
    WINDOWS = "windows"
    MACOS = "macos"
    TERMUX = "termux"
    UNKNOWN = "unknown"


class ColorDepth(StrEnum):
    """Detected terminal color capability level."""

    NONE = "none"
    BASIC = "16"
    EIGHT_BIT = "256"
    TRUECOLOR = "truecolor"


@dataclass(frozen=True, slots=True)
class PlatformInfo:
    """Immutable snapshot of the host operating system and Python runtime."""

    os_type: OperatingSystem
    system_name: str
    release: str
    version: str
    architecture: str
    python_version: str
    python_version_tuple: tuple[int, int, int]
    is_termux: bool
    is_wsl: bool

    @property
    def is_supported_os(self) -> bool:
        """Return True when the detected OS is a supported POPTRAP target."""
        return self.os_type is not OperatingSystem.UNKNOWN

    @property
    def summary(self) -> str:
        """Compact one-line label suitable for UI status display."""
        arch = self.architecture or "unknown"
        if self.is_wsl and self.os_type is OperatingSystem.LINUX:
            return f"Linux/WSL · {arch}"
        return f"{self.system_name} · {arch}"


@dataclass(frozen=True, slots=True)
class TerminalCapabilities:
    """Immutable snapshot of detected terminal characteristics."""

    is_interactive: bool
    term: str
    encoding: str
    is_dumb_terminal: bool
    color_depth: ColorDepth
    supports_unicode: bool
    columns: int
    rows: int
    meets_minimum_size: bool
    meets_recommended_size: bool
    supports_tui: bool
    warnings: tuple[str, ...]
    blocking_issues: tuple[str, ...]


def is_termux_environment(env: Mapping[str, str] | None = None) -> bool:
    """Detect whether the current process runs inside Android Termux."""
    source = os.environ if env is None else env
    if source.get("TERMUX_VERSION", "").strip():
        return True
    if source.get("TERMUX_MAIN_PACKAGE_FORMAT", "").strip():
        return True
    prefix = source.get("PREFIX", "")
    return "com.termux" in prefix.lower()


def is_wsl_environment(
    env: Mapping[str, str] | None = None,
    release: str | None = None,
) -> bool:
    """Detect whether a Linux runtime is Windows Subsystem for Linux."""
    source = os.environ if env is None else env
    if source.get("WSL_DISTRO_NAME", "").strip() or source.get("WSL_INTEROP", "").strip():
        return True
    kernel_release = stdlib_platform.release() if release is None else release
    return "microsoft" in kernel_release.lower()


def detect_platform(
    *,
    sys_platform: str | None = None,
    system: str | None = None,
    release: str | None = None,
    version: str | None = None,
    machine: str | None = None,
    python_version_info: tuple[int, int, int] | None = None,
    env: Mapping[str, str] | None = None,
) -> PlatformInfo:
    """Detect operating system, architecture, and Python version safely."""
    current_env = os.environ if env is None else env
    raw_platform = (sys.platform if sys_platform is None else sys_platform).lower()
    raw_system = stdlib_platform.system() if system is None else system
    raw_release = stdlib_platform.release() if release is None else release
    raw_version = stdlib_platform.version() if version is None else version
    raw_machine = (stdlib_platform.machine() if machine is None else machine) or "unknown"

    if python_version_info is None:
        vi = sys.version_info
        py_tuple = (int(vi[0]), int(vi[1]), int(vi[2]))
    else:
        py_tuple = (
            int(python_version_info[0]),
            int(python_version_info[1]),
            int(python_version_info[2]),
        )
    py_str = f"{py_tuple[0]}.{py_tuple[1]}.{py_tuple[2]}"

    termux = is_termux_environment(current_env) and not raw_platform.startswith(("win", "darwin"))
    wsl = False

    if termux:
        os_type = OperatingSystem.TERMUX
        display_name = "Termux"
    elif raw_platform in {"win32", "cygwin", "msys"} or raw_system.lower() == "windows":
        os_type = OperatingSystem.WINDOWS
        display_name = "Windows"
    elif raw_platform == "darwin" or raw_system.lower() == "darwin":
        os_type = OperatingSystem.MACOS
        display_name = "macOS"
    elif raw_platform.startswith("linux") or raw_system.lower() == "linux":
        os_type = OperatingSystem.LINUX
        display_name = "Linux"
        wsl = is_wsl_environment(current_env, raw_release)
    else:
        os_type = OperatingSystem.UNKNOWN
        display_name = raw_system.strip() or raw_platform or "Unknown"

    return PlatformInfo(
        os_type=os_type,
        system_name=display_name,
        release=raw_release,
        version=raw_version,
        architecture=raw_machine,
        python_version=py_str,
        python_version_tuple=py_tuple,
        is_termux=termux,
        is_wsl=wsl,
    )


def _supports_unicode_encoding(encoding: str) -> bool:
    """Return True if ``encoding`` resolves to a Unicode codec."""
    if not encoding:
        return False
    try:
        canonical = codecs.lookup(encoding).name.lower()
    except LookupError:
        canonical = encoding.lower().replace("_", "-")
    return canonical.startswith(_UNICODE_CODEC_PREFIXES)


def _extract_windows_build(version: str) -> int | None:
    """Parse build number from a Windows version string such as ``10.0.22631``."""
    parts = version.strip().split(".")
    if len(parts) >= 3 and parts[2].isdigit():
        return int(parts[2])
    return None


def _windows_supports_vt(
    platform_info: PlatformInfo,
    env: Mapping[str, str],
) -> bool:
    """Determine whether a Windows console supports virtual terminal sequences."""
    if any(
        env.get(var, "").strip()
        for var in ("WT_SESSION", "TERM_PROGRAM", "ANSICON", "TERM")
    ):
        return True
    if env.get("ConEmuANSI", "").strip().upper() == "ON":
        return True
    build = _extract_windows_build(platform_info.version)
    if build is not None:
        return build >= _WINDOWS_VT_MIN_BUILD
    # When version string is unavailable, assume modern Windows 10/11 rather than
    # issuing a false hard block, since Python 3.11+ requires Windows 8.1+.
    return True


def _detect_color_depth(
    platform_info: PlatformInfo,
    env: Mapping[str, str],
    *,
    is_dumb: bool,
) -> ColorDepth:
    """Infer terminal color capability from environment indicators."""
    if is_dumb:
        return ColorDepth.NONE

    # Honor https://no-color.org/ convention when set to a non-empty string.
    if env.get("NO_COLOR", "") != "":
        return ColorDepth.NONE

    colorterm = env.get("COLORTERM", "").strip().lower()
    if colorterm in {"truecolor", "24bit"}:
        return ColorDepth.TRUECOLOR

    term = env.get("TERM", "").strip().lower()
    term_program = env.get("TERM_PROGRAM", "").strip().lower()

    if platform_info.os_type is OperatingSystem.WINDOWS:
        if env.get("WT_SESSION", "").strip() or term_program in {"vscode", "wezterm"}:
            return ColorDepth.TRUECOLOR
        if _windows_supports_vt(platform_info, env):
            return ColorDepth.TRUECOLOR
        return ColorDepth.BASIC

    if platform_info.os_type is OperatingSystem.TERMUX:
        return ColorDepth.TRUECOLOR

    if any(token in term for token in ("direct", "truecolor", "24bit")):
        return ColorDepth.TRUECOLOR
    if term_program in {"iterm.app", "wezterm", "vscode", "ghostty", "alacritty", "kitty"}:
        return ColorDepth.TRUECOLOR
    if "256color" in term:
        return ColorDepth.EIGHT_BIT
    if term in {"", "unknown"}:
        return ColorDepth.BASIC
    if any(
        token in term
        for token in ("xterm", "screen", "tmux", "vt100", "ansi", "linux", "rxvt", "color")
    ):
        return ColorDepth.BASIC
    return ColorDepth.BASIC


def _safe_stream_isatty(stream: object | None) -> bool:
    """Check ``stream.isatty()`` defensively against closed or replaced streams."""
    if stream is None:
        return False
    isatty = getattr(stream, "isatty", None)
    if not callable(isatty):
        return False
    try:
        return bool(isatty())
    except (OSError, ValueError):
        return False


def _detect_stream_encoding(stream: object | None) -> str:
    """Read the active text encoding of ``stream`` with safe fallback."""
    encoding = getattr(stream, "encoding", None)
    if isinstance(encoding, str) and encoding.strip():
        return encoding.strip()
    return sys.getdefaultencoding()


def detect_terminal_capabilities(
    platform_info: PlatformInfo | None = None,
    *,
    env: Mapping[str, str] | None = None,
    stdin_isatty: bool | None = None,
    stdout_isatty: bool | None = None,
    encoding: str | None = None,
    terminal_size: tuple[int, int] | None = None,
) -> TerminalCapabilities:
    """Inspect terminal capabilities and generate actionable guidance."""
    pinfo = detect_platform(env=env) if platform_info is None else platform_info
    current_env = os.environ if env is None else env

    in_tty = _safe_stream_isatty(sys.stdin) if stdin_isatty is None else stdin_isatty
    out_tty = _safe_stream_isatty(sys.stdout) if stdout_isatty is None else stdout_isatty
    is_interactive = in_tty and out_tty

    term = current_env.get("TERM", "").strip()
    is_dumb = term.lower() == "dumb"

    active_encoding = (
        _detect_stream_encoding(sys.stdout) if encoding is None else encoding.strip()
    )
    supports_unicode = _supports_unicode_encoding(active_encoding)

    if terminal_size is None:
        size = shutil.get_terminal_size(fallback=(80, 24))
        columns, rows = int(size.columns), int(size.lines)
    else:
        columns, rows = max(0, int(terminal_size[0])), max(0, int(terminal_size[1]))

    meets_minimum = columns >= MIN_TERMINAL_COLUMNS and rows >= MIN_TERMINAL_ROWS
    meets_recommended = (
        columns >= RECOMMENDED_TERMINAL_COLUMNS and rows >= RECOMMENDED_TERMINAL_ROWS
    )
    color_depth = _detect_color_depth(pinfo, current_env, is_dumb=is_dumb)

    blocking: list[str] = []
    warnings: list[str] = []

    if not is_interactive:
        blocking.append(
            "Standard input/output is not attached to an interactive terminal. "
            "Launch POPTRAP directly inside a terminal emulator (not piped or redirected), "
            "or run 'python poptrap.py --check' for non-interactive diagnostics."
        )

    if is_dumb:
        blocking.append(
            "TERM is set to 'dumb', which lacks cursor addressing required for the "
            "interactive interface. Use a full terminal emulator with TERM set to "
            "'xterm-256color' or equivalent."
        )

    if pinfo.os_type is OperatingSystem.WINDOWS and not _windows_supports_vt(pinfo, current_env):
        blocking.append(
            "This Windows console does not support ANSI/VT terminal sequences. "
            "Run POPTRAP inside Windows Terminal or Windows 10 (build 10586+) / Windows 11."
        )

    if not pinfo.is_supported_os:
        warnings.append(
            f"Unrecognized operating system '{pinfo.system_name}'. "
            "POPTRAP targets Linux, macOS, Windows, and Termux."
        )

    if not supports_unicode:
        warnings.append(
            f"Terminal encoding is '{active_encoding or 'unknown'}' rather than UTF-8. "
            "ASCII fallback glyphs will be used; configure UTF-8 for optimal typography."
        )

    if color_depth is ColorDepth.NONE and not is_dumb:
        warnings.append(
            "Terminal color output is disabled (NO_COLOR is set). "
            "Unset NO_COLOR to enable dark cyan/white styling."
        )
    elif color_depth is ColorDepth.BASIC:
        warnings.append(
            "Limited 16-color terminal detected. Use a 256-color or truecolor terminal "
            "(for example TERM=xterm-256color) for accurate dark palette rendering."
        )

    if is_interactive and not meets_minimum:
        resize_tip = (
            "Rotate to landscape or reduce font zoom in Termux."
            if pinfo.is_termux
            else "Enlarge the terminal window for full menu visibility."
        )
        warnings.append(
            f"Terminal size is {columns}x{rows} (minimum {MIN_TERMINAL_COLUMNS}x{MIN_TERMINAL_ROWS}). "
            f"{resize_tip}"
        )
    elif is_interactive and not meets_recommended:
        warnings.append(
            f"Compact terminal size detected ({columns}x{rows}; recommended "
            f"{RECOMMENDED_TERMINAL_COLUMNS}x{RECOMMENDED_TERMINAL_ROWS}+). Using compact layout."
        )

    return TerminalCapabilities(
        is_interactive=is_interactive,
        term=term or ("windows-console" if pinfo.os_type is OperatingSystem.WINDOWS else "unset"),
        encoding=active_encoding or "unknown",
        is_dumb_terminal=is_dumb,
        color_depth=color_depth,
        supports_unicode=supports_unicode,
        columns=columns,
        rows=rows,
        meets_minimum_size=meets_minimum,
        meets_recommended_size=meets_recommended,
        supports_tui=len(blocking) == 0,
        warnings=tuple(warnings),
        blocking_issues=tuple(blocking),
    )
