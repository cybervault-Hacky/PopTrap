"""Automated tests for platform detection, paths, terminal, and dependency checks."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from poptrap.core.environment import (
    DependencySpec,
    check_dependencies,
    check_environment,
    check_python_version,
    enforce_environment,
    parse_version_tuple,
)
from poptrap.core.exceptions import (
    MissingDependencyError,
    UnsupportedPythonError,
    UnsupportedTerminalError,
)
from poptrap.utils.paths import (
    ENV_CONFIG_DIR,
    ENV_LOG_DIR,
    format_display_path,
    get_user_config_path,
    get_user_log_dir,
)
from poptrap.utils.platform import (
    ColorDepth,
    OperatingSystem,
    detect_platform,
    detect_terminal_capabilities,
    is_termux_environment,
    is_wsl_environment,
)


class TestPlatformDetection:
    """Verify OS, Termux, WSL, and host platform detection."""

    def test_current_host_platform_detection(self) -> None:
        info = detect_platform()
        assert isinstance(info.os_type, OperatingSystem)
        assert info.python_version_tuple >= (3, 11, 0)
        assert info.system_name != ""
        assert info.architecture != ""
        assert info.summary != ""

    def test_detect_linux(self) -> None:
        info = detect_platform(
            sys_platform="linux",
            system="Linux",
            release="6.6.12-generic",
            version="#1 SMP",
            machine="x86_64",
            env={},
        )
        assert info.os_type is OperatingSystem.LINUX
        assert info.system_name == "Linux"
        assert info.is_supported_os is True
        assert info.is_termux is False
        assert info.is_wsl is False
        assert info.summary == "Linux · x86_64"

    def test_detect_linux_wsl(self) -> None:
        info = detect_platform(
            sys_platform="linux",
            system="Linux",
            release="5.15.153.1-microsoft-standard-WSL2",
            version="#1 SMP",
            machine="x86_64",
            env={"WSL_DISTRO_NAME": "Ubuntu"},
        )
        assert info.os_type is OperatingSystem.LINUX
        assert info.is_wsl is True
        assert info.summary == "Linux/WSL · x86_64"
        assert is_wsl_environment({}, "5.15.0-microsoft-standard") is True

    def test_detect_windows(self) -> None:
        info = detect_platform(
            sys_platform="win32",
            system="Windows",
            release="11",
            version="10.0.22631",
            machine="AMD64",
            env={},
        )
        assert info.os_type is OperatingSystem.WINDOWS
        assert info.system_name == "Windows"
        assert info.is_supported_os is True
        assert info.is_termux is False

    def test_detect_macos(self) -> None:
        info = detect_platform(
            sys_platform="darwin",
            system="Darwin",
            release="23.4.0",
            version="Darwin Kernel Version 23.4.0",
            machine="arm64",
            env={},
        )
        assert info.os_type is OperatingSystem.MACOS
        assert info.system_name == "macOS"
        assert info.is_supported_os is True

    @pytest.mark.parametrize(
        "env",
        [
            {"TERMUX_VERSION": "0.118.1"},
            {"PREFIX": "/data/data/com.termux/files/usr"},
            {"TERMUX_MAIN_PACKAGE_FORMAT": "debian"},
        ],
    )
    def test_detect_termux(self, env: dict[str, str]) -> None:
        assert is_termux_environment(env) is True
        info = detect_platform(
            sys_platform="linux",
            system="Linux",
            release="5.10.198-android13",
            version="#1 SMP PREEMPT",
            machine="aarch64",
            env=env,
        )
        assert info.os_type is OperatingSystem.TERMUX
        assert info.system_name == "Termux"
        assert info.is_termux is True
        assert info.is_supported_os is True

    def test_detect_unknown_operating_system(self) -> None:
        info = detect_platform(
            sys_platform="sunos5",
            system="SunOS",
            release="5.11",
            version="11.4",
            machine="i86pc",
            env={},
        )
        assert info.os_type is OperatingSystem.UNKNOWN
        assert info.is_supported_os is False


class TestCrossPlatformPaths:
    """Verify per-user config and log directory resolution across OS families."""

    def test_env_overrides_take_precedence(self, tmp_path: Path) -> None:
        custom_cfg = tmp_path / "custom_cfg"
        custom_log = tmp_path / "custom_log"
        env = {ENV_CONFIG_DIR: str(custom_cfg), ENV_LOG_DIR: str(custom_log)}

        assert get_user_config_path(os_type=OperatingSystem.WINDOWS, env=env) == (
            custom_cfg / "config.toml"
        )
        assert get_user_log_dir(os_type=OperatingSystem.MACOS, env=env) == custom_log

    def test_windows_paths(self, tmp_path: Path) -> None:
        env = {
            "APPDATA": str(tmp_path / "Roaming"),
            "LOCALAPPDATA": str(tmp_path / "Local"),
        }
        assert get_user_config_path(
            os_type=OperatingSystem.WINDOWS, env=env, home=tmp_path
        ) == (tmp_path / "Roaming" / "poptrap" / "config.toml")
        assert get_user_log_dir(
            os_type=OperatingSystem.WINDOWS, env=env, home=tmp_path
        ) == (tmp_path / "Local" / "poptrap" / "logs")

    def test_macos_paths(self, tmp_path: Path) -> None:
        assert get_user_config_path(
            os_type=OperatingSystem.MACOS, env={}, home=tmp_path
        ) == (tmp_path / "Library" / "Application Support" / "poptrap" / "config.toml")
        assert get_user_log_dir(
            os_type=OperatingSystem.MACOS, env={}, home=tmp_path
        ) == (tmp_path / "Library" / "Logs" / "poptrap")

    def test_linux_and_termux_xdg_paths(self, tmp_path: Path) -> None:
        assert get_user_config_path(
            os_type=OperatingSystem.LINUX, env={}, home=tmp_path
        ) == (tmp_path / ".config" / "poptrap" / "config.toml")
        assert get_user_config_path(
            os_type=OperatingSystem.TERMUX,
            env={"XDG_CONFIG_HOME": str(tmp_path / "xdg")},
            home=tmp_path,
        ) == (tmp_path / "xdg" / "poptrap" / "config.toml")
        assert get_user_log_dir(
            os_type=OperatingSystem.LINUX, env={}, home=tmp_path
        ) == (tmp_path / ".local" / "state" / "poptrap" / "logs")

    def test_format_display_path_shortens_home(self, tmp_path: Path) -> None:
        inside = tmp_path / ".config" / "poptrap" / "config.toml"
        assert format_display_path(inside, home=tmp_path).startswith("~")


class TestTerminalCapabilities:
    """Verify terminal capability checks and unsupported terminal guidance."""

    def test_full_featured_interactive_terminal(self) -> None:
        linux = detect_platform(
            sys_platform="linux", system="Linux", release="6.1", version="", machine="x86_64", env={}
        )
        caps = detect_terminal_capabilities(
            linux,
            env={"TERM": "xterm-256color", "COLORTERM": "truecolor"},
            stdin_isatty=True,
            stdout_isatty=True,
            encoding="utf-8",
            terminal_size=(100, 30),
        )
        assert caps.supports_tui is True
        assert caps.color_depth is ColorDepth.TRUECOLOR
        assert caps.supports_unicode is True
        assert caps.meets_minimum_size is True
        assert caps.meets_recommended_size is True
        assert caps.blocking_issues == ()
        assert caps.warnings == ()

    def test_non_interactive_stream_blocks_tui_with_guidance(self) -> None:
        caps = detect_terminal_capabilities(
            env={"TERM": "xterm-256color"},
            stdin_isatty=False,
            stdout_isatty=False,
            encoding="utf-8",
            terminal_size=(80, 24),
        )
        assert caps.is_interactive is False
        assert caps.supports_tui is False
        assert any("--check" in msg for msg in caps.blocking_issues)

    def test_dumb_terminal_blocks_tui_with_guidance(self) -> None:
        caps = detect_terminal_capabilities(
            env={"TERM": "dumb"},
            stdin_isatty=True,
            stdout_isatty=True,
            encoding="utf-8",
            terminal_size=(80, 24),
        )
        assert caps.is_dumb_terminal is True
        assert caps.supports_tui is False
        assert caps.color_depth is ColorDepth.NONE
        assert any("dumb" in msg.lower() for msg in caps.blocking_issues)

    def test_legacy_windows_console_blocks_while_windows_terminal_passes(self) -> None:
        legacy_win = detect_platform(
            sys_platform="win32",
            system="Windows",
            release="8.1",
            version="6.3.9600",
            machine="AMD64",
            env={},
        )
        blocked = detect_terminal_capabilities(
            legacy_win,
            env={},
            stdin_isatty=True,
            stdout_isatty=True,
            encoding="utf-8",
            terminal_size=(80, 24),
        )
        assert blocked.supports_tui is False
        assert any("windows terminal" in msg.lower() for msg in blocked.blocking_issues)

        modern_wt = detect_terminal_capabilities(
            legacy_win,
            env={"WT_SESSION": "abc-123"},
            stdin_isatty=True,
            stdout_isatty=True,
            encoding="utf-8",
            terminal_size=(80, 24),
        )
        assert modern_wt.supports_tui is True
        assert modern_wt.color_depth is ColorDepth.TRUECOLOR

    def test_no_color_ascii_and_small_termux_warnings(self) -> None:
        termux = detect_platform(
            sys_platform="linux",
            system="Linux",
            release="5.10",
            version="",
            machine="aarch64",
            env={"TERMUX_VERSION": "0.118.1"},
        )
        caps = detect_terminal_capabilities(
            termux,
            env={"TERM": "xterm-256color", "NO_COLOR": "1"},
            stdin_isatty=True,
            stdout_isatty=True,
            encoding="ascii",
            terminal_size=(40, 14),
        )
        assert caps.supports_tui is True
        assert caps.color_depth is ColorDepth.NONE
        assert caps.supports_unicode is False
        assert caps.meets_minimum_size is False
        joined = " ".join(caps.warnings).lower()
        assert "utf-8" in joined
        assert "no_color" in joined
        assert "termux" in joined


class TestRuntimeDependencyAndPythonChecks:
    """Verify Python version and third-party dependency validation."""

    def test_python_version_checks(self) -> None:
        ok_310, str_310 = check_python_version((3, 10, 14))
        assert ok_310 is False
        assert str_310 == "3.10.14"

        ok_311, str_311 = check_python_version((3, 11, 2))
        assert ok_311 is True
        assert str_311 == "3.11.2"

    def test_version_tuple_parser(self) -> None:
        assert parse_version_tuple("8.2.8") == (8, 2, 8)
        assert parse_version_tuple("2.14.0rc1") == (2, 14, 0)
        assert parse_version_tuple("14.3") == (14, 3, 0)
        assert parse_version_tuple("unknown") is None

    def test_real_installed_dependencies_pass(self) -> None:
        report = check_environment(require_interactive=False)
        assert report.python_ok is True
        assert report.dependencies_ok is True
        assert report.core_ok is True
        assert report.errors == ()
        enforce_environment(report, require_tui=False)

    def test_missing_and_outdated_dependencies_detected(self) -> None:
        specs = (
            DependencySpec("textual", "textual", (0, 85, 0)),
            DependencySpec("pydantic", "pydantic", (2, 7, 0)),
        )

        def fake_import(name: str) -> object:
            if name == "textual":
                raise ModuleNotFoundError("No module named 'textual'")
            return SimpleNamespace(__version__="1.10.15")

        statuses = check_dependencies(
            specs,
            importer=fake_import,
            version_lookup=lambda _dist, mod: getattr(mod, "__version__", None),
        )
        assert statuses[0].available is False
        assert statuses[0].ok is False
        assert statuses[1].available is True
        assert statuses[1].compatible is False
        assert statuses[1].ok is False

        report = check_environment(
            require_interactive=False,
            dependency_specs=specs,
            importer=fake_import,
            version_lookup=lambda _dist, mod: getattr(mod, "__version__", None),
        )
        assert report.dependencies_ok is False
        with pytest.raises(MissingDependencyError) as exc_info:
            enforce_environment(report, require_tui=False)
        assert len(exc_info.value.missing) == 2

    def test_enforce_environment_raises_on_old_python_and_bad_terminal(self) -> None:
        old_py_platform = detect_platform(python_version_info=(3, 10, 9))
        old_report = check_environment(
            require_interactive=False,
            platform_info=old_py_platform,
        )
        with pytest.raises(UnsupportedPythonError):
            enforce_environment(old_report, require_tui=False)

        bad_term = detect_terminal_capabilities(
            env={"TERM": "dumb"},
            stdin_isatty=True,
            stdout_isatty=True,
            encoding="utf-8",
            terminal_size=(80, 24),
        )
        bad_term_report = check_environment(
            require_interactive=True,
            terminal_capabilities=bad_term,
        )
        with pytest.raises(UnsupportedTerminalError):
            enforce_environment(bad_term_report, require_tui=True)
