"""Automated interactive UI and CLI startup tests for POPTRAP Phase 1."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest
from textual.widgets import Button, Select, Static, Switch

from poptrap import cli
from poptrap.app import PopTrapApp
from poptrap.core.config import (
    AppConfig,
    LanguageCode,
    LogLevel,
    ThemeName,
    load_config,
)
from poptrap.core.logging_setup import configure_logging, get_logger
from poptrap.core.modules import MAIN_MENU_MODULES, ModuleAvailability, ModuleId
from poptrap.ui.screens import (
    BluetoothScreen,
    CreatePrankScreen,
    MainMenuScreen,
    ModuleStatusScreen,
    SettingsScreen,
)
from poptrap.ui.widgets import BrandHeader, MainMenuList, ResizeNotice
from poptrap.utils.paths import get_project_root
from poptrap.utils.platform import detect_platform, detect_terminal_capabilities

EXPECTED_MENU_LABELS: list[str] = [
    "1.  Create Prank",
    "2.  Bluetooth",
    "3.  QR / Share Link",
    "4.  Local Network",
    "5.  Settings",
    "6.  Exit",
]


def _make_app(
    tmp_path: Path,
    *,
    config: AppConfig | None = None,
    config_path: Path | None = None,
    warnings: tuple[str, ...] = (),
    unicode_enabled: bool = True,
) -> PopTrapApp:
    pinfo = detect_platform()
    caps = detect_terminal_capabilities(
        pinfo,
        env={"TERM": "xterm-256color", "COLORTERM": "truecolor"},
        stdin_isatty=True,
        stdout_isatty=True,
        encoding="utf-8" if unicode_enabled else "ascii",
        terminal_size=(80, 24),
    )
    return PopTrapApp(
        config=config or AppConfig(),
        config_path=config_path or (tmp_path / "config.toml"),
        config_warnings=warnings,
        platform_info=pinfo,
        terminal_capabilities=caps,
    )


class TestModuleRegistry:
    """Verify exact Phase 1 menu structure and honest availability states."""

    def test_exact_six_modules_and_phase1_scope(self) -> None:
        assert len(MAIN_MENU_MODULES) == 6
        assert [m.module_id for m in MAIN_MENU_MODULES] == [
            ModuleId.CREATE_PRANK,
            ModuleId.BLUETOOTH,
            ModuleId.QR_SHARE_LINK,
            ModuleId.LOCAL_NETWORK,
            ModuleId.SETTINGS,
            ModuleId.EXIT,
        ]
        for implemented_spec in MAIN_MENU_MODULES[:2]:
            assert implemented_spec.availability is ModuleAvailability.AVAILABLE
            assert implemented_spec.is_available is True
        for future_spec in MAIN_MENU_MODULES[2:4]:
            assert future_spec.availability is ModuleAvailability.UNAVAILABLE
            assert future_spec.is_available is False
            assert future_spec.detail_key is not None
        for active_spec in MAIN_MENU_MODULES[4:]:
            assert active_spec.availability is ModuleAvailability.AVAILABLE
            assert active_spec.is_available is True


class TestAppInitializationAndNavigation:
    """Verify TUI initialization, main menu rendering, and navigation."""

    async def test_initializes_with_branding_and_exact_six_options(
        self, tmp_path: Path
    ) -> None:
        app = _make_app(tmp_path)
        async with app.run_test(size=(80, 24)) as pilot:
            await pilot.pause()
            assert isinstance(app.screen, MainMenuScreen)
            assert app.theme == ThemeName.CYAN_DARK.value
            assert app.animation_level == "none"

            header = app.screen.query_one(BrandHeader)
            assert header is not None

            menu = app.screen.query_one(MainMenuList)
            assert app.focused is menu
            assert menu.option_count == 6
            rendered = [
                str(menu.get_option_at_index(i).prompt)
                for i in range(menu.option_count)
            ]
            assert rendered == EXPECTED_MENU_LABELS
            assert menu.highlighted == 0

            notice = app.screen.query_one("#startup-notice", Static)
            assert "hidden" in notice.classes

    async def test_startup_config_warning_banner_shown_when_fallback_used(
        self, tmp_path: Path
    ) -> None:
        malformed = tmp_path / "bad.toml"
        malformed.write_text("[ui\nbroken", encoding="utf-8")
        loaded = load_config(malformed)

        app = PopTrapApp(config_result=loaded)
        async with app.run_test(size=(80, 24)) as pilot:
            await pilot.pause()
            notice = app.screen.query_one("#startup-notice", Static)
            assert "hidden" not in notice.classes
            assert "defaults applied" in str(notice.render()).lower()

    async def test_arrow_and_vim_keyboard_navigation(self, tmp_path: Path) -> None:
        app = _make_app(tmp_path)
        async with app.run_test(size=(80, 24)) as pilot:
            await pilot.pause()
            menu = app.screen.query_one(MainMenuList)

            await pilot.press("down")
            assert menu.highlighted == 1
            await pilot.press("j")
            assert menu.highlighted == 2
            await pilot.press("k")
            assert menu.highlighted == 1
            await pilot.press("up")
            assert menu.highlighted == 0

    async def test_create_prank_option_opens_engine_screen_and_returns(
        self, tmp_path: Path
    ) -> None:
        app = _make_app(tmp_path)
        async with app.run_test(size=(80, 24)) as pilot:
            await pilot.pause()
            await pilot.press("1")
            await pilot.pause()
            assert isinstance(app.screen, CreatePrankScreen)

            await pilot.press("escape")
            await pilot.pause()
            assert isinstance(app.screen, MainMenuScreen)
            assert app.screen.query_one(MainMenuList).highlighted == 0

    @pytest.mark.parametrize(
        ("shortcut", "expected_id", "back_key"),
        [
            ("3", ModuleId.QR_SHARE_LINK, "escape"),
            ("3", ModuleId.QR_SHARE_LINK, "backspace"),
            ("3", ModuleId.QR_SHARE_LINK, "b"),
            ("4", ModuleId.LOCAL_NETWORK, "enter"),
        ],
    )
    async def test_future_phase_modules_show_unavailable_and_navigate_back(
        self,
        tmp_path: Path,
        shortcut: str,
        expected_id: ModuleId,
        back_key: str,
    ) -> None:
        app = _make_app(tmp_path)
        async with app.run_test(size=(80, 24)) as pilot:
            await pilot.pause()
            await pilot.press(shortcut)
            await pilot.pause()

            assert isinstance(app.screen, ModuleStatusScreen)
            assert app.screen.module.module_id is expected_id
            assert app.screen.module.is_available is False

            status_text = str(
                app.screen.query_one("#module-status-text", Static).render()
            )
            detail_text = str(
                app.screen.query_one("#module-detail-text", Static).render()
            )
            assert "not available in phase 1" in status_text.lower()
            assert "not implemented" in detail_text.lower()

            # Strictly one actionable button ("Back") — no unfinished fake controls.
            buttons = list(app.screen.query(Button))
            assert [btn.id for btn in buttons] == ["btn-back"]
            assert app.focused is buttons[0]

            await pilot.press(back_key)
            await pilot.pause()
            assert isinstance(app.screen, MainMenuScreen)
            menu = app.screen.query_one(MainMenuList)
            assert menu.highlighted == int(shortcut) - 1


class TestSettingsScreen:
    """Verify real configuration editing, persistence, reset, and error handling."""

    async def test_edit_save_apply_and_reset_settings(self, tmp_path: Path) -> None:
        cfg_file = tmp_path / "config.toml"
        log_file = tmp_path / "poptrap.log"
        configure_logging(LogLevel.INFO, log_path=log_file)

        app = _make_app(tmp_path, config_path=cfg_file)
        async with app.run_test(size=(80, 24)) as pilot:
            await pilot.pause()
            await pilot.press("5")
            await pilot.pause()
            assert isinstance(app.screen, SettingsScreen)

            app.screen.query_one("#select-theme", Select).value = ThemeName.MONO_DARK
            app.screen.query_one("#switch-sound", Switch).value = True
            app.screen.query_one("#select-language", Select).value = LanguageCode.FR
            app.screen.query_one("#select-log-level", Select).value = LogLevel.DEBUG

            await pilot.press("ctrl+s")
            await pilot.pause()

            assert app.theme == ThemeName.MONO_DARK.value
            assert app.config.audio.sound_enabled is True
            assert app.config.ui.default_language is LanguageCode.FR
            assert app.config.logging.level is LogLevel.DEBUG
            assert app.sound_events == 1
            assert get_logger().level == 10

            persisted = load_config(cfg_file, strict=True).config
            assert persisted == app.config

            # Return to main menu and confirm French localization applied live.
            await pilot.press("escape")
            await pilot.pause()
            assert isinstance(app.screen, MainMenuScreen)
            menu = app.screen.query_one(MainMenuList)
            assert "Parametres" in str(menu.get_option_at_index(4).prompt)

            # Re-open settings and restore defaults.
            await pilot.press("5")
            await pilot.pause()
            await pilot.click("#btn-reset")
            await pilot.pause()

            assert app.config == AppConfig()
            assert app.theme == ThemeName.CYAN_DARK.value
            assert load_config(cfg_file, strict=True).config == AppConfig()

    async def test_back_without_save_discards_unsaved_changes(
        self, tmp_path: Path
    ) -> None:
        cfg_file = tmp_path / "config.toml"
        app = _make_app(tmp_path, config_path=cfg_file)
        async with app.run_test(size=(80, 24)) as pilot:
            await pilot.pause()
            await pilot.press("5")
            await pilot.pause()

            app.screen.query_one("#select-theme", Select).value = ThemeName.ICE_DARK
            await pilot.click("#btn-back")
            await pilot.pause()

            assert isinstance(app.screen, MainMenuScreen)
            assert app.config.ui.theme is ThemeName.CYAN_DARK
            assert not cfg_file.exists()

    async def test_save_error_surfaces_feedback_without_crashing(
        self, tmp_path: Path
    ) -> None:
        dir_as_file = tmp_path / "cannot_overwrite_dir"
        dir_as_file.mkdir()

        app = _make_app(tmp_path, config_path=dir_as_file)
        async with app.run_test(size=(80, 24)) as pilot:
            await pilot.pause()
            await pilot.press("5")
            await pilot.pause()
            await pilot.click("#btn-save")
            await pilot.pause()

            feedback = app.screen.query_one("#settings-feedback", Static)
            assert "error" in feedback.classes
            assert "directory" in str(feedback.render()).lower()


class TestResizeUnicodeFallbackAndGracefulExit:
    """Verify responsive terminal resizing, ASCII mode, and all exit paths."""

    async def test_terminal_resize_states_and_recovery(self, tmp_path: Path) -> None:
        app = _make_app(tmp_path)
        async with app.run_test(size=(80, 24)) as pilot:
            await pilot.pause()
            screen = app.screen
            assert "compact" not in screen.classes
            assert "too-small" not in screen.classes

            await pilot.resize_terminal(58, 17)
            await pilot.pause()
            assert "compact" in screen.classes
            assert "too-small" not in screen.classes

            await pilot.resize_terminal(36, 10)
            await pilot.pause()
            assert "too-small" in screen.classes
            notice_text = str(screen.query_one(ResizeNotice).render())
            assert "36x10" in notice_text

            await pilot.resize_terminal(100, 30)
            await pilot.pause()
            assert "compact" not in screen.classes
            assert "too-small" not in screen.classes

    async def test_settings_interactive_at_minimum_terminal_size(
        self, tmp_path: Path
    ) -> None:
        app = _make_app(tmp_path)
        async with app.run_test(size=(44, 12)) as pilot:
            await pilot.pause()
            await pilot.press("5")
            await pilot.pause()
            assert isinstance(app.screen, SettingsScreen)
            assert "compact" in app.screen.classes
            assert "too-small" not in app.screen.classes

            await pilot.click("#btn-back")
            await pilot.pause()
            assert isinstance(app.screen, MainMenuScreen)

    async def test_ascii_fallback_rendering(self, tmp_path: Path) -> None:
        app = _make_app(tmp_path, unicode_enabled=False)
        async with app.run_test(size=(80, 24)) as pilot:
            await pilot.pause()
            assert isinstance(app.screen, MainMenuScreen)
            await pilot.press("1")
            await pilot.pause()
            assert isinstance(app.screen, CreatePrankScreen)
            await pilot.press("escape")
            await pilot.pause()
            await pilot.press("2")
            await pilot.pause()
            assert isinstance(app.screen, BluetoothScreen)
            await pilot.press("escape")
            await pilot.pause()
            await pilot.press("3")
            await pilot.pause()
            assert isinstance(app.screen, ModuleStatusScreen)
            await pilot.press("escape")
            await pilot.pause()
            assert isinstance(app.screen, MainMenuScreen)

    @pytest.mark.parametrize(
        "key_sequence",
        [
            ("6",),
            ("down", "down", "down", "down", "down", "enter"),
            ("q",),
            ("ctrl+c",),
            ("2", "q"),
            ("1", "ctrl+q"),
        ],
    )
    async def test_graceful_exit_paths(
        self,
        tmp_path: Path,
        key_sequence: tuple[str, ...],
    ) -> None:
        app = _make_app(tmp_path)
        async with app.run_test(size=(80, 24)) as pilot:
            await pilot.pause()
            for key in key_sequence:
                await pilot.press(key)
                await pilot.pause()
        assert app.return_value == 0


class TestCliEntryPoint:
    """Verify ``poptrap.py`` and ``poptrap.cli`` startup behavior."""

    def test_cli_version_and_check_commands(
        self,
        tmp_path: Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        assert cli.main(["--version"]) == cli.EXIT_OK
        assert "POPTRAP" in capsys.readouterr().out

        assert cli.main(["--check"]) == cli.EXIT_OK
        check_out = capsys.readouterr().out
        assert "POPTRAP" in check_out
        assert "textual" in check_out

        bad_cfg = tmp_path / "invalid.toml"
        bad_cfg.write_text('[ui]\ntheme = "unknown"\n', encoding="utf-8")
        assert cli.main(["--check", "--config", str(bad_cfg)]) == cli.EXIT_OK
        warn_out = capsys.readouterr().out
        assert "Notice:" in warn_out

    def test_poptrap_script_subprocess_execution(self, tmp_path: Path) -> None:
        script = get_project_root() / "poptrap.py"
        env = os.environ.copy()
        env["POPTRAP_CONFIG_DIR"] = str(tmp_path / "cfg")
        env["POPTRAP_LOG_DIR"] = str(tmp_path / "log")

        proc_check = subprocess.run(
            [sys.executable, str(script), "--check"],
            capture_output=True,
            text=True,
            env=env,
            check=False,
            timeout=15,
        )
        assert proc_check.returncode == 0
        assert "POPTRAP" in proc_check.stdout

        # Non-interactive invocation reports clear guidance rather than crashing.
        proc_non_tty = subprocess.run(
            [sys.executable, str(script)],
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            env=env,
            check=False,
            timeout=15,
        )
        assert proc_non_tty.returncode == cli.EXIT_ENVIRONMENT_ERROR
        assert "interactive terminal" in proc_non_tty.stderr.lower()

    @pytest.mark.skipif(sys.platform == "win32", reason="POSIX pty module required")
    def test_poptrap_script_full_pty_launch_and_graceful_exit(
        self, tmp_path: Path
    ) -> None:
        import fcntl
        import pty
        import select
        import struct
        import termios
        import time

        script = get_project_root() / "poptrap.py"
        log_dir = tmp_path / "pty_log"
        env = os.environ.copy()
        env["TERM"] = "xterm-256color"
        env["COLORTERM"] = "truecolor"
        env["POPTRAP_CONFIG_DIR"] = str(tmp_path / "pty_cfg")
        env["POPTRAP_LOG_DIR"] = str(log_dir)

        master_fd, slave_fd = pty.openpty()
        fcntl.ioctl(slave_fd, termios.TIOCSWINSZ, struct.pack("HHHH", 24, 80, 0, 0))

        proc = subprocess.Popen(
            [sys.executable, str(script)],
            stdin=slave_fd,
            stdout=slave_fd,
            stderr=slave_fd,
            env=env,
            close_fds=True,
        )
        os.close(slave_fd)

        output = bytearray()
        exit_sent = False
        deadline = time.monotonic() + 10.0
        try:
            while time.monotonic() < deadline:
                ready, _, _ = select.select([master_fd], [], [], 0.05)
                if ready:
                    try:
                        chunk = os.read(master_fd, 65536)
                    except OSError:
                        break
                    if not chunk:
                        break
                    output.extend(chunk)
                    if not exit_sent and b"Create Prank" in output and b"Exit" in output:
                        os.write(master_fd, b"6")
                        exit_sent = True
                if exit_sent and proc.poll() is not None:
                    break

            rc = proc.wait(timeout=5)
        finally:
            os.close(master_fd)
            if proc.poll() is None:
                proc.kill()

        decoded = output.decode("utf-8", errors="replace")
        assert exit_sent is True
        assert rc == 0
        for label in (
            "Create Prank",
            "Bluetooth",
            "QR / Share Link",
            "Local Network",
            "Settings",
            "Exit",
        ):
            assert label in decoded
        log_content = (log_dir / "poptrap.log").read_text(encoding="utf-8")
        assert "Application exited cleanly with code 0" in log_content
