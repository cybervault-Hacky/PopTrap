"""Phase 4 Textual UI integration tests for Prank Studio & Preset Manager."""

from __future__ import annotations

from pathlib import Path

import pytest
from textual.widgets import Button, Input, OptionList, Select, Static, Switch

from poptrap.app import PopTrapApp
from poptrap.core.config import load_default_config
from poptrap.engine.controller import PrankController
from poptrap.engine.models import PrankTheme, SessionState, TemplateId, VisualIntensity
from poptrap.engine.studio import (
    CardPresentationStyle,
    CompletionStyle,
    PopupMessagePreset,
    PresetStore,
    RingPresentationStyle,
    StageKind,
    build_default_studio_config,
)
from poptrap.ui.screens import (
    CreatePrankScreen,
    LocalNetworkScreen,
    MainMenuScreen,
    PrankStudioScreen,
    PresetManagerScreen,
    ShareLinkScreen,
)
from poptrap.utils.platform import detect_platform, detect_terminal_capabilities


def _make_studio_app(tmp_path: Path) -> PopTrapApp:
    plat = detect_platform()
    caps = detect_terminal_capabilities(
        plat,
        env={"TERM": "xterm-256color", "COLORTERM": "truecolor"},
        stdin_isatty=True,
        stdout_isatty=True,
        encoding="utf-8",
        terminal_size=(100, 30),
    )
    app = PopTrapApp(
        config=load_default_config(),
        config_path=tmp_path / "config.toml",
        platform_info=plat,
        terminal_capabilities=caps,
    )
    app.prank_controller._preset_store = PresetStore(
        tmp_path / "presets", registry=app.prank_controller.registry
    )
    return app


class TestMenuIntegrationAndNavigation:
    async def test_main_menu_retains_exact_six_items_with_delivery(
        self, tmp_path: Path
    ) -> None:
        app = _make_studio_app(tmp_path)
        async with app.run_test(size=(80, 24)) as pilot:
            await pilot.pause()
            assert isinstance(app.screen, MainMenuScreen)
            menu = app.screen.query_one("#main-menu-list", OptionList)
            assert menu.option_count == 6

            # Phase 6 uses the existing two menu entries without adding to the menu.
            for key, screen in (("3", ShareLinkScreen), ("4", LocalNetworkScreen)):
                await pilot.press(key)
                await pilot.pause()
                assert isinstance(app.screen, screen)
                await pilot.press("escape")
                await pilot.pause()
                assert isinstance(app.screen, MainMenuScreen)

    async def test_create_prank_exposes_quick_create_studio_presets_and_back_at_80x24(
        self, tmp_path: Path
    ) -> None:
        app = _make_studio_app(tmp_path)
        async with app.run_test(size=(80, 24)) as pilot:
            await pilot.pause()
            await pilot.press("1")
            await pilot.pause()
            assert isinstance(app.screen, CreatePrankScreen)

            # Verify all four workflow actions exist and work cleanly.
            app.screen.query_one("#btn-quick-create", Button).press()
            await pilot.pause()
            assert isinstance(app.screen, CreatePrankScreen)

            app.screen.query_one("#btn-open-studio", Button).press()
            await pilot.pause()
            assert isinstance(app.screen, PrankStudioScreen)

            app.screen.query_one("#btn-studio-back", Button).press()
            await pilot.pause()
            assert isinstance(app.screen, CreatePrankScreen)

            app.screen.query_one("#btn-load-preset", Button).press()
            await pilot.pause()
            assert isinstance(app.screen, PresetManagerScreen)

            app.screen.query_one("#btn-preset-back", Button).press()
            await pilot.pause()
            assert isinstance(app.screen, CreatePrankScreen)

            app.screen.query_one("#btn-back", Button).press()
            await pilot.pause()
            assert isinstance(app.screen, MainMenuScreen)


class TestPrankStudioCustomizationAndComposerUI:
    async def test_context_sensitive_controls_and_custom_builds_for_all_templates(
        self, tmp_path: Path
    ) -> None:
        app = _make_studio_app(tmp_path)
        async with app.run_test(size=(100, 32)) as pilot:
            await pilot.pause()
            await pilot.press("1")
            await pilot.pause()
            app.screen.query_one("#btn-open-studio", Button).press()
            await pilot.pause()
            studio = app.screen
            assert isinstance(studio, PrankStudioScreen)

            tpl_select = studio.query_one("#studio-template", Select)

            for tid in TemplateId:
                tpl_select.value = tid
                await pilot.pause()
                # ONLY the selected template's customization panel is visible.
                for other in TemplateId:
                    assert studio.is_template_group_visible(other) is (other is tid)

            # Customize Popup Storm specifically and validate
            tpl_select.value = TemplateId.POPUP_STORM
            await pilot.pause()
            studio.query_one("#studio-name", Input).value = "Custom Storm Build"
            studio.query_one("#studio-theme", Select).value = PrankTheme.NEON
            studio.query_one("#studio-intensity", Select).value = VisualIntensity.HIGH
            studio.query_one("#studio-sound", Switch).value = True
            studio.query_one("#storm-count", Input).value = "18"
            studio.query_one("#storm-card-style", Select).value = CardPresentationStyle.GRID
            studio.query_one("#storm-preset", Select).value = PopupMessagePreset.OFFICE_HUMOR
            studio.query_one("#storm-cleanup", Input).value = "4"
            cfg = studio.build_validated_studio_config()
            assert cfg.name == "Custom Storm Build"
            assert cfg.theme is PrankTheme.NEON
            assert cfg.sound_enabled is True
            assert cfg.popup_storm_settings is not None
            assert cfg.popup_storm_settings.popup_count == 18
            assert cfg.popup_storm_settings.card_style is CardPresentationStyle.GRID
            assert cfg.popup_storm_settings.message_preset is PopupMessagePreset.OFFICE_HUMOR
            assert cfg.popup_storm_settings.cleanup_duration_seconds == 4

            # Customize Virus Alert
            tpl_select.value = TemplateId.VIRUS_ALERT
            await pilot.pause()
            studio.query_one("#virus-stages", Input).value = "5"
            studio.query_one("#virus-completion", Select).value = CompletionStyle.MINIMAL
            v_cfg = studio.build_validated_studio_config()
            assert v_cfg.virus_alert_settings is not None
            assert v_cfg.virus_alert_settings.scan_stage_count == 5
            assert v_cfg.virus_alert_settings.completion_style is CompletionStyle.MINIMAL

            # Customize Countdown
            tpl_select.value = TemplateId.COUNTDOWN
            await pilot.pause()
            studio.query_one("#countdown-ring", Select).value = RingPresentationStyle.DOUBLE
            studio.query_one("#countdown-completion", Input).value = "ZERO HARM — ALL FUN!"
            c_cfg = studio.build_validated_studio_config()
            assert c_cfg.countdown_settings is not None
            assert c_cfg.countdown_settings.ring_style is RingPresentationStyle.DOUBLE
            assert c_cfg.countdown_settings.completion_message == "ZERO HARM — ALL FUN!"

    async def test_multi_stage_composer_add_edit_reorder_remove_and_limits(
        self, tmp_path: Path
    ) -> None:
        app = _make_studio_app(tmp_path)
        async with app.run_test(size=(100, 32)) as pilot:
            await pilot.pause()
            await pilot.press("1")
            await pilot.pause()
            app.screen.query_one("#btn-open-studio", Button).press()
            await pilot.pause()
            studio = app.screen
            assert isinstance(studio, PrankStudioScreen)

            # Starter 4-stage sequence
            studio.query_one("#btn-stage-starter", Button).press()
            await pilot.pause()
            assert len(studio.stages) == 4
            assert studio.query_one("#studio-duration", Input).disabled is True

            # Add 5th stage -> +Stage disables at 5-stage cap
            studio.query_one("#btn-stage-add", Button).press()
            await pilot.pause()
            assert len(studio.stages) == 5
            assert studio.query_one("#btn-stage-add", Button).disabled is True

            # Edit 5th stage into Countdown (7s)
            studio.query_one("#stage-kind", Select).value = StageKind.COUNTDOWN
            studio.query_one("#stage-label", Input).value = "Final Timer"
            studio.query_one("#stage-duration", Input).value = "7"
            studio.query_one("#btn-stage-apply", Button).press()
            await pilot.pause()
            assert studio.stages[4].label == "Final Timer"
            assert studio.stages[4].kind is StageKind.COUNTDOWN
            assert studio.stages[4].duration_seconds == 7

            # Move stage up -> index 3
            studio.query_one("#btn-stage-up", Button).press()
            await pilot.pause()
            assert studio.stages[3].label == "Final Timer"

            # Reject total > 120s with clear error without corrupting stages
            studio.query_one("#stage-duration", Input).value = "115"
            studio.query_one("#btn-stage-apply", Button).press()
            await pilot.pause()
            assert studio.query_one("#studio-feedback", Static).has_class("error")
            assert studio.stages[3].duration_seconds == 7

            # Remove stage -> count becomes 4
            studio.query_one("#btn-stage-remove", Button).press()
            await pilot.pause()
            assert len(studio.stages) == 4

            # Clear -> back to single-stage mode
            studio.query_one("#btn-stage-clear", Button).press()
            await pilot.pause()
            assert len(studio.stages) == 0
            assert studio.query_one("#studio-duration", Input).disabled is False


class TestStudioLivePreviewAndPresetsUI:
    async def test_live_preview_start_restart_stop_and_return_to_editor(
        self, tmp_path: Path
    ) -> None:
        app = _make_studio_app(tmp_path)
        async with app.run_test(size=(100, 32)) as pilot:
            await pilot.pause()
            await pilot.press("1")
            await pilot.pause()
            app.screen.query_one("#btn-open-studio", Button).press()
            await pilot.pause()
            studio = app.screen
            assert isinstance(studio, PrankStudioScreen)

            # Start local live preview
            studio.query_one("#btn-studio-preview", Button).press()
            await pilot.pause()
            first_id = studio.preview_session_id
            assert first_id is not None
            assert app.experience_server.is_running
            assert (
                app.prank_controller.get_session(first_id).state is SessionState.READY
            )

            # Restart preview with updated settings
            studio.query_one("#glitch-rgb", Input).value = "11"
            studio.query_one("#btn-studio-restart", Button).press()
            await pilot.pause()
            second_id = studio.preview_session_id
            assert second_id is not None and second_id != first_id
            assert (
                app.prank_controller.get_session(second_id).state is SessionState.READY
            )

            # Stop preview -> immediately stopped, user remains in PrankStudioScreen
            studio.query_one("#btn-studio-stop", Button).press()
            await pilot.pause()
            assert isinstance(app.screen, PrankStudioScreen)
            assert (
                app.prank_controller.get_session(second_id).state is SessionState.STOPPED
            )

    async def test_preset_save_overwrite_confirm_load_rename_duplicate_delete_and_launch(
        self, tmp_path: Path
    ) -> None:
        app = _make_studio_app(tmp_path)
        async with app.run_test(size=(100, 32)) as pilot:
            await pilot.pause()
            await pilot.press("1")
            await pilot.pause()
            app.screen.query_one("#btn-open-studio", Button).press()
            await pilot.pause()
            studio = app.screen
            assert isinstance(studio, PrankStudioScreen)

            # Save new preset
            studio.query_one("#studio-name", Input).value = "Cyber Glitch Show"
            studio.query_one("#studio-theme", Select).value = PrankTheme.TERMINAL
            studio.query_one("#glitch-rgb", Input).value = "9"
            studio.query_one("#btn-studio-save", Button).press()
            await pilot.pause()
            preset_id = studio.loaded_preset_id
            assert preset_id == "cyber-glitch-show"

            # Edit & Save -> prompts overwrite confirmation first
            studio.query_one("#studio-reveal", Input).value = "Edited safe reveal!"
            studio.query_one("#btn-studio-save", Button).press()
            await pilot.pause()
            overwrite_bar = studio.query_one("#studio-overwrite-bar")
            assert not overwrite_bar.has_class("hidden")

            # Confirm overwrite
            studio.query_one("#btn-confirm-overwrite", Button).press()
            await pilot.pause()
            assert overwrite_bar.has_class("hidden")
            reloaded = app.prank_controller.presets.load_preset(preset_id)
            assert reloaded.config.reveal_message == "Edited safe reveal!"

            # Plant a malformed file to ensure PresetManagerScreen handles it gracefully
            (app.prank_controller.presets.presets_dir / "corrupt.json").write_text(
                "{invalid", encoding="utf-8"
            )

            # Open PresetManagerScreen
            studio.query_one("#btn-studio-presets", Button).press()
            await pilot.pause()
            mgr = app.screen
            assert isinstance(mgr, PresetManagerScreen)
            assert not mgr.query_one("#preset-warnings").has_class("hidden")

            # Rename
            mgr.query_one("#preset-rename-input", Input).value = "Matrix Glitch Show"
            mgr.query_one("#btn-preset-rename", Button).press()
            await pilot.pause()
            assert (
                app.prank_controller.presets.load_preset(preset_id).name
                == "Matrix Glitch Show"
            )

            # Duplicate
            mgr.query_one("#btn-preset-duplicate", Button).press()
            await pilot.pause()
            summaries, _ = app.prank_controller.presets.list_presets()
            assert len(summaries) == 2

            # Delete the duplicate with explicit confirmation
            mgr.query_one("#btn-preset-delete", Button).press()
            await pilot.pause()
            assert not mgr.query_one("#preset-delete-bar").has_class("hidden")
            mgr.query_one("#btn-confirm-delete", Button).press()
            await pilot.pause()
            summaries_after, _ = app.prank_controller.presets.list_presets()
            assert len(summaries_after) == 1

            # Load remaining preset back into Studio & launch live session
            mgr.query_one("#btn-preset-load", Button).press()
            await pilot.pause()
            assert isinstance(app.screen, PrankStudioScreen)
            assert (
                app.screen.query_one("#studio-name", Input).value
                == "Matrix Glitch Show"
            )

            app.screen.query_one("#btn-studio-start", Button).press()
            await pilot.pause()
            assert app.active_session_id is not None
            assert app.experience_server.is_running

            # Navigate back to Create Prank and test direct Load Saved Preset -> Launch
            app.screen.query_one("#btn-studio-back", Button).press()
            await pilot.pause()
            assert isinstance(app.screen, CreatePrankScreen)

            app.screen.query_one("#btn-load-preset", Button).press()
            await pilot.pause()
            assert isinstance(app.screen, PresetManagerScreen)
            app.screen.query_one("#btn-preset-run", Button).press()
            await pilot.pause()
            assert isinstance(app.screen, CreatePrankScreen)
            assert app.active_session_id is not None
            assert app.experience_server.is_running
