"""Integration tests for the Phase 2 Create Prank terminal interface."""

from __future__ import annotations

from pathlib import Path

import httpx
import pytest
from textual.widgets import Input, Select, Static, Switch

from poptrap.app import PopTrapApp
from poptrap.core.config import AppConfig, LanguageCode, ThemeName, UISection
from poptrap.engine.models import (
    MAX_DURATION_SECONDS,
    MAX_POPUP_COUNT,
    SessionState,
    TemplateId,
)
from poptrap.ui.screens import (
    CreatePrankScreen,
    MainMenuScreen,
    ModuleStatusScreen,
    PrankPreviewScreen,
)
from poptrap.utils.platform import detect_platform, detect_terminal_capabilities


def _make_app(
    tmp_path: Path,
    *,
    language: LanguageCode = LanguageCode.EN,
    unicode_enabled: bool = True,
) -> PopTrapApp:
    plat = detect_platform()
    caps = detect_terminal_capabilities(
        plat,
        env={"TERM": "xterm-256color", "COLORTERM": "truecolor"},
        stdin_isatty=True,
        stdout_isatty=True,
        encoding="utf-8" if unicode_enabled else "ascii",
        terminal_size=(80, 24),
    )
    cfg = AppConfig(
        ui=UISection(theme=ThemeName.CYAN_DARK, default_language=language)
    )
    return PopTrapApp(
        config=cfg,
        config_path=tmp_path / "config.toml",
        platform_info=plat,
        terminal_capabilities=caps,
    )


class TestCreatePrankScreenWorkflow:
    """Verify template listing, validation, preview, server startup, and shutdown."""

    async def test_lists_all_five_templates_and_toggles_popup_controls(
        self, tmp_path: Path
    ) -> None:
        app = _make_app(tmp_path)
        async with app.run_test(size=(80, 24)) as pilot:
            await pilot.pause()
            await pilot.press("1")
            await pilot.pause()
            assert isinstance(app.screen, CreatePrankScreen)

            select = app.screen.query_one("#prank-template", Select)
            assert select.value is TemplateId.GLITCH
            assert "hidden" in app.screen.query_one("#row-popup-count").classes

            for tid in TemplateId:
                select.value = tid
                await pilot.pause()
                meta = app.prank_controller.get_template(tid).metadata
                assert app.screen.query_one("#prank-title", Input).value == meta.default_title
                popup_hidden = "hidden" in app.screen.query_one("#row-popup-count").classes
                assert popup_hidden is (not meta.supports_popup_count)

    @pytest.mark.parametrize(
        ("template_id", "field_id", "bad_value"),
        [
            (TemplateId.GLITCH, "#prank-duration", "2"),
            (TemplateId.GLITCH, "#prank-duration", str(MAX_DURATION_SECONDS + 50)),
            (TemplateId.GLITCH, "#prank-duration", "forever"),
            (TemplateId.GLITCH, "#prank-title", "   "),
            (TemplateId.POPUP_STORM, "#prank-popup-count", "0"),
            (TemplateId.POPUP_STORM, "#prank-popup-count", str(MAX_POPUP_COUNT + 10)),
        ],
    )
    async def test_rejects_invalid_user_input_without_starting_server(
        self,
        tmp_path: Path,
        template_id: TemplateId,
        field_id: str,
        bad_value: str,
    ) -> None:
        app = _make_app(tmp_path)
        async with app.run_test(size=(80, 24)) as pilot:
            await pilot.pause()
            await pilot.press("1")
            await pilot.pause()
            assert isinstance(app.screen, CreatePrankScreen)

            app.screen.query_one("#prank-template", Select).value = template_id
            await pilot.pause()
            app.screen.query_one(field_id, Input).value = bad_value

            await pilot.click("#btn-start")
            await pilot.pause()

            feedback = app.screen.query_one("#prank-feedback", Static)
            assert "error" in feedback.classes
            assert len(str(feedback.render()).strip()) > 0
            assert app.experience_server.is_running is False
            assert app.active_session_id is None

    async def test_preview_launch_live_server_consent_and_creator_stop(
        self, tmp_path: Path
    ) -> None:
        app = _make_app(tmp_path)
        async with app.run_test(size=(80, 24)) as pilot:
            await pilot.pause()
            await pilot.press("1")
            await pilot.pause()
            assert isinstance(app.screen, CreatePrankScreen)

            # Configure Countdown Panic with sound enabled and custom message.
            app.screen.query_one("#prank-template", Select).value = TemplateId.COUNTDOWN
            await pilot.pause()
            app.screen.query_one("#prank-title", Input).value = "PARTY COUNTDOWN"
            app.screen.query_one("#prank-message", Input).value = "Stand by for confetti."
            app.screen.query_one("#prank-reveal", Input).value = "Surprise — purely simulated!"
            app.screen.query_one("#prank-duration", Input).value = "14"
            app.screen.query_one("#prank-sound", Switch).value = True
            await pilot.pause()

            # Preview first, verify content, return to form, then launch from preview.
            await pilot.press("ctrl+p")
            await pilot.pause()
            assert isinstance(app.screen, PrankPreviewScreen)
            assert app.screen.preview.config.title == "PARTY COUNTDOWN"
            assert app.screen.preview.config.sound_enabled is True

            await pilot.click("#btn-preview-back")
            await pilot.pause()
            assert isinstance(app.screen, CreatePrankScreen)

            # Start session directly via button.
            await pilot.click("#btn-start")
            await pilot.pause()

            assert app.experience_server.is_running is True
            assert app.active_session_id is not None
            sid = app.active_session_id

            server_url = str(app.screen.query_one("#val-server-url", Static).render())
            session_url = str(app.screen.query_one("#val-session-url", Static).render())
            assert server_url == app.experience_server.base_url
            assert server_url.startswith("http://127.0.0.1:")
            assert session_url == f"{server_url}/s/{sid}"

            # Exercise the actual displayed browser session URL end-to-end.
            async with httpx.AsyncClient(timeout=5.0) as client:
                html = await client.get(session_url)
                assert html.status_code == 200
                consent = await client.post(
                    f"{server_url}/api/v1/sessions/{sid}/consent",
                    json={"consent": True},
                )
                assert consent.status_code == 200
                assert consent.json()["runtime"]["countdown_seconds"] == 14

            app.screen._sync_session_panel()
            state_line = str(app.screen.query_one("#val-session-state", Static).render())
            assert "ACTIVE" in state_line

            # Creator stops session with Ctrl+X -> session terminated and server stopped.
            await pilot.press("ctrl+x")
            await pilot.pause()
            assert app.experience_server.is_running is False
            assert app.prank_controller.get_session(sid).state is SessionState.STOPPED

    async def test_app_exit_stops_running_server_and_active_session(
        self, tmp_path: Path
    ) -> None:
        app = _make_app(tmp_path)
        async with app.run_test(size=(80, 24)) as pilot:
            await pilot.pause()
            await pilot.press("1")
            await pilot.pause()
            await pilot.press("ctrl+r")
            await pilot.pause()

            sid = app.active_session_id
            assert sid is not None
            assert app.experience_server.is_running is True

            await pilot.press("ctrl+q")
            await pilot.pause()

        assert app.return_value == 0
        assert app.experience_server.is_running is False
        assert app.prank_controller.active_session_count == 0
        assert app.prank_controller.stored_session_count == 0

    async def test_future_delivery_options_remain_unavailable_in_phase2(
        self, tmp_path: Path
    ) -> None:
        app = _make_app(tmp_path)
        async with app.run_test(size=(80, 24)) as pilot:
            await pilot.pause()
            for key in ("3", "4"):
                await pilot.press(key)
                await pilot.pause()
                assert isinstance(app.screen, ModuleStatusScreen)
                assert app.screen.module.is_available is False
                await pilot.press("escape")
                await pilot.pause()
                assert isinstance(app.screen, MainMenuScreen)

    @pytest.mark.parametrize("lang", [LanguageCode.ES, LanguageCode.FR, LanguageCode.DE])
    async def test_localized_and_minimum_size_create_prank_screen(
        self, tmp_path: Path, lang: LanguageCode
    ) -> None:
        app = _make_app(tmp_path, language=lang, unicode_enabled=False)
        async with app.run_test(size=(44, 12)) as pilot:
            await pilot.pause()
            await pilot.press("1")
            await pilot.pause()
            assert isinstance(app.screen, CreatePrankScreen)
            assert app.screen.has_class("compact")

            await pilot.press("ctrl+p")
            await pilot.pause()
            assert isinstance(app.screen, PrankPreviewScreen)

            await pilot.press("escape")
            await pilot.pause()
            assert isinstance(app.screen, CreatePrankScreen)

            await pilot.press("escape")
            await pilot.pause()
            assert isinstance(app.screen, MainMenuScreen)
