"""Interactive Settings screen for inspecting and persisting POPTRAP config."""

from __future__ import annotations

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Container, Horizontal, Vertical, VerticalScroll
from textual.widgets import Button, Select, Static, Switch

from poptrap.core.config import LanguageCode, LogLevel, ThemeName
from poptrap.core.exceptions import ConfigurationError
from poptrap.core.i18n import LANGUAGE_LABELS, t
from poptrap.ui.screens.base import BasePopTrapScreen
from poptrap.ui.widgets import KeyHintBar, ResizeNotice, SectionHeader
from poptrap.utils.paths import format_display_path


class SettingsScreen(BasePopTrapScreen):
    """Minimal configuration editor backed by validated TOML persistence."""

    BINDINGS = [
        Binding("escape", "go_back", "Back", show=False),
        Binding("ctrl+s", "save_settings", "Save", show=False),
    ]

    def compose(self) -> ComposeResult:
        app = self.poptrap_app
        cfg = app.config
        lang = cfg.ui.default_language
        unicode_ok = app.terminal_capabilities.supports_unicode

        with Container(classes="screen-shell settings-shell"):
            with Vertical(classes="content-frame settings-frame"):
                yield SectionHeader(
                    t("settings.title", lang),
                    f"v{cfg.app.version}",
                    unicode_enabled=unicode_ok,
                    id="settings-header",
                )

                with VerticalScroll(id="settings-scroll", can_focus=False):
                    with Horizontal(classes="setting-row"):
                        yield Static(
                            t("settings.application", lang),
                            classes="setting-label",
                            id="lbl-application",
                        )
                        yield Static(
                            f"{cfg.app.name} {cfg.app.version}",
                            classes="setting-readonly",
                            id="val-application",
                        )

                    with Horizontal(classes="setting-row"):
                        yield Static(
                            t("settings.platform", lang),
                            classes="setting-label",
                            id="lbl-platform",
                        )
                        yield Static(
                            self._format_platform(),
                            classes="setting-readonly-muted",
                            id="val-platform",
                        )

                    with Horizontal(classes="setting-row"):
                        yield Static(
                            t("settings.theme", lang),
                            classes="setting-label",
                            id="lbl-theme",
                        )
                        yield Select[ThemeName](
                            self._theme_options(lang),
                            value=cfg.ui.theme,
                            allow_blank=False,
                            type_to_search=False,
                            compact=True,
                            id="select-theme",
                        )

                    with Horizontal(classes="setting-row"):
                        yield Static(
                            t("settings.sound", lang),
                            classes="setting-label",
                            id="lbl-sound",
                        )
                        with Horizontal(classes="sound-control-group"):
                            yield Switch(
                                value=cfg.audio.sound_enabled,
                                animate=False,
                                id="switch-sound",
                            )
                            yield Static(
                                self._sound_text(cfg.audio.sound_enabled, lang),
                                id="sound-state-label",
                            )

                    with Horizontal(classes="setting-row"):
                        yield Static(
                            t("settings.language", lang),
                            classes="setting-label",
                            id="lbl-language",
                        )
                        yield Select[LanguageCode](
                            self._language_options(),
                            value=cfg.ui.default_language,
                            allow_blank=False,
                            type_to_search=False,
                            compact=True,
                            id="select-language",
                        )

                    with Horizontal(classes="setting-row"):
                        yield Static(
                            t("settings.log_level", lang),
                            classes="setting-label",
                            id="lbl-log-level",
                        )
                        yield Select[LogLevel](
                            self._log_level_options(),
                            value=cfg.logging.level,
                            allow_blank=False,
                            type_to_search=False,
                            compact=True,
                            id="select-log-level",
                        )

                    with Horizontal(classes="setting-row"):
                        yield Static(
                            t("settings.config_path", lang),
                            classes="setting-label",
                            id="lbl-config-path",
                        )
                        yield Static(
                            format_display_path(app.config_path),
                            classes="setting-readonly-muted",
                            id="val-config-path",
                        )

                yield Static("", id="settings-feedback")

                with Horizontal(classes="settings-actions"):
                    yield Button(
                        t("settings.save", lang),
                        id="btn-save",
                        classes="action-btn",
                    )
                    yield Button(
                        t("settings.reset", lang),
                        id="btn-reset",
                        classes="action-btn",
                    )
                    yield Button(
                        t("action.back", lang),
                        id="btn-back",
                        classes="action-btn",
                    )

        yield KeyHintBar(self._build_key_hints(lang), id="settings-key-bar")
        yield ResizeNotice(id="resize-notice")

    def on_mount(self) -> None:
        self.apply_viewport_size(self.app.size.width, self.app.size.height)
        self.query_one("#select-theme", Select).focus()

    def _format_platform(self) -> str:
        info = self.poptrap_app.platform_info
        sep = "·" if self.poptrap_app.terminal_capabilities.supports_unicode else "/"
        return f"{info.system_name} ({info.architecture}) {sep} Python {info.python_version}"

    @staticmethod
    def _theme_options(lang: LanguageCode) -> list[tuple[str, ThemeName]]:
        return [(t(f"theme.{item.value}", lang), item) for item in ThemeName]

    @staticmethod
    def _language_options() -> list[tuple[str, LanguageCode]]:
        return [(LANGUAGE_LABELS[code], code) for code in LanguageCode]

    @staticmethod
    def _log_level_options() -> list[tuple[str, LogLevel]]:
        return [(level.value, level) for level in LogLevel]

    @staticmethod
    def _sound_text(enabled: bool, lang: LanguageCode) -> str:
        return t("settings.sound_on" if enabled else "settings.sound_off", lang)

    @staticmethod
    def _build_key_hints(lang: LanguageCode) -> list[tuple[str, str]]:
        return [
            ("Tab", t("keys.navigate", lang)),
            ("Enter", t("keys.select", lang)),
            ("Ctrl+S", t("keys.save", lang)),
            ("Esc", t("keys.back", lang)),
        ]

    def _set_feedback(self, message: str, *, is_error: bool = False) -> None:
        banner = self.query_one("#settings-feedback", Static)
        banner.set_class(is_error, "error")
        banner.update(message)

    def _read_selected_values(self) -> tuple[ThemeName, bool, LanguageCode, LogLevel]:
        cfg = self.poptrap_app.config

        raw_theme = self.query_one("#select-theme", Select).value
        theme = raw_theme if isinstance(raw_theme, ThemeName) else cfg.ui.theme

        sound_enabled = bool(self.query_one("#switch-sound", Switch).value)

        raw_lang = self.query_one("#select-language", Select).value
        language = raw_lang if isinstance(raw_lang, LanguageCode) else cfg.ui.default_language

        raw_level = self.query_one("#select-log-level", Select).value
        log_level = raw_level if isinstance(raw_level, LogLevel) else cfg.logging.level

        return theme, sound_enabled, language, log_level

    def refresh_from_config(self) -> None:
        """Synchronize all labels and controls with the active ``AppConfig``."""
        cfg = self.poptrap_app.config
        lang = cfg.ui.default_language

        self.query_one(SectionHeader).set_content(
            t("settings.title", lang),
            f"v{cfg.app.version}",
        )
        self.query_one("#lbl-application", Static).update(t("settings.application", lang))
        self.query_one("#val-application", Static).update(f"{cfg.app.name} {cfg.app.version}")
        self.query_one("#lbl-platform", Static).update(t("settings.platform", lang))
        self.query_one("#val-platform", Static).update(self._format_platform())
        self.query_one("#lbl-theme", Static).update(t("settings.theme", lang))
        self.query_one("#lbl-sound", Static).update(t("settings.sound", lang))
        self.query_one("#lbl-language", Static).update(t("settings.language", lang))
        self.query_one("#lbl-log-level", Static).update(t("settings.log_level", lang))
        self.query_one("#lbl-config-path", Static).update(t("settings.config_path", lang))
        self.query_one("#val-config-path", Static).update(
            format_display_path(self.poptrap_app.config_path)
        )

        theme_select = self.query_one("#select-theme", Select)
        theme_select.set_options(self._theme_options(lang))
        theme_select.value = cfg.ui.theme

        sound_switch = self.query_one("#switch-sound", Switch)
        sound_switch.value = cfg.audio.sound_enabled
        self.query_one("#sound-state-label", Static).update(
            self._sound_text(cfg.audio.sound_enabled, lang)
        )

        self.query_one("#select-language", Select).value = cfg.ui.default_language
        self.query_one("#select-log-level", Select).value = cfg.logging.level

        self.query_one("#btn-save", Button).label = t("settings.save", lang)
        self.query_one("#btn-reset", Button).label = t("settings.reset", lang)
        self.query_one("#btn-back", Button).label = t("action.back", lang)
        self.query_one(KeyHintBar).set_hints(self._build_key_hints(lang))

    def on_switch_changed(self, event: Switch.Changed) -> None:
        if event.switch.id == "switch-sound":
            lang = self.poptrap_app.config.ui.default_language
            self.query_one("#sound-state-label", Static).update(
                self._sound_text(event.value, lang)
            )

    def on_button_pressed(self, event: Button.Pressed) -> None:
        event.stop()
        btn_id = event.button.id
        if btn_id == "btn-save":
            self.action_save_settings()
        elif btn_id == "btn-reset":
            self.action_reset_defaults()
        elif btn_id == "btn-back":
            self.action_go_back()

    def action_save_settings(self) -> None:
        theme, sound_enabled, language, log_level = self._read_selected_values()
        try:
            updated = self.poptrap_app.update_preferences(
                theme=theme,
                sound_enabled=sound_enabled,
                default_language=language,
                log_level=log_level,
            )
        except ConfigurationError as exc:
            self._set_feedback(exc.message, is_error=True)
            return

        self.refresh_from_config()
        self._set_feedback(t("settings.saved", updated.ui.default_language), is_error=False)

    def action_reset_defaults(self) -> None:
        try:
            restored = self.poptrap_app.reset_preferences()
        except ConfigurationError as exc:
            self._set_feedback(exc.message, is_error=True)
            return

        self.refresh_from_config()
        self._set_feedback(
            t("settings.reset_done", restored.ui.default_language),
            is_error=False,
        )

    def action_go_back(self) -> None:
        self.poptrap_app.close_active_screen()
