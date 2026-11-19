"""Interactive Create Prank configuration, preview, and session control screens."""

from __future__ import annotations

from collections.abc import Callable

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Container, Horizontal, Vertical, VerticalScroll
from textual.timer import Timer
from textual.widgets import Button, Input, Select, Static, Switch

from poptrap.core.exceptions import PopTrapError, SessionNotFoundError
from poptrap.core.i18n import t
from poptrap.engine.lifecycle import is_terminal_state
from poptrap.engine.models import (
    AnimationSpeed,
    DEFAULT_POPUP_COUNT,
    MAX_DURATION_SECONDS,
    MAX_MESSAGE_LENGTH,
    MAX_POPUP_COUNT,
    MAX_REVEAL_LENGTH,
    MAX_TITLE_LENGTH,
    MIN_DURATION_SECONDS,
    MIN_POPUP_COUNT,
    PRIMARY_PRANK_THEMES,
    PrankConfig,
    PrankTheme,
    SessionState,
    TemplateId,
    TemplatePreview,
    VisualIntensity,
)
from poptrap.engine.validation import (
    parse_positive_int_input,
    parse_semicolon_messages,
)
from poptrap.ui.screens.base import BasePopTrapScreen
from poptrap.ui.widgets import KeyHintBar, ResizeNotice, SectionHeader


class PrankPreviewScreen(BasePopTrapScreen):
    """Structured terminal preview for a validated prank configuration."""

    BINDINGS = [
        Binding("escape", "go_back", "Back", show=False),
        Binding("b", "go_back", "Back", show=False),
        Binding("ctrl+r", "launch_from_preview", "Start", show=False),
    ]

    def __init__(
        self,
        preview: TemplatePreview,
        *,
        on_start_requested: Callable[[PrankConfig], None] | None = None,
    ) -> None:
        super().__init__()
        self._preview = preview
        self._on_start_requested = on_start_requested

    @property
    def preview(self) -> TemplatePreview:
        """Validated ``TemplatePreview`` displayed on this screen."""
        return self._preview

    def compose(self) -> ComposeResult:
        app = self.poptrap_app
        lang = app.config.ui.default_language
        unicode_ok = app.terminal_capabilities.supports_unicode
        cfg = self._preview.config
        sep = "·" if unicode_ok else "/"

        stage_lines = "\n".join(
            f"{st.start_second:04.1f}s–{st.end_second:04.1f}s  {st.label}: {st.description}"
            if unicode_ok
            else f"{st.start_second:04.1f}s-{st.end_second:04.1f}s  {st.label}: {st.description}"
            for st in self._preview.stages
        )
        param_parts = [
            f"theme={cfg.theme.value}",
            f"intensity={cfg.intensity.value}",
            f"speed={cfg.animation_speed.value}",
            f"sound={'on' if cfg.sound_enabled else 'off'}",
            f"auto_end={'on' if cfg.auto_terminate else 'off'}",
        ]
        if cfg.popup_count is not None:
            param_parts.append(f"popups={cfg.popup_count}/{MAX_POPUP_COUNT}")
        for key, value in self._preview.effect_summary.items():
            if key not in {"intensity", "animation_speed", "popup_count", "max_popup_limit"}:
                param_parts.append(f"{key}={value}")
        params_text = f" {sep} ".join(param_parts)
        safety_text = "\n".join(f"- {item}" for item in self._preview.safety_controls)

        header_title = f"{t('prank.preview_title', lang)}: {self._preview.template_name}"
        header_meta = f"{cfg.duration_seconds}s"

        with Container(classes="screen-shell"):
            with Vertical(classes="content-frame preview-frame"):
                yield SectionHeader(
                    header_title,
                    header_meta,
                    unicode_enabled=unicode_ok,
                    accent_meta=True,
                    id="preview-header",
                )
                with VerticalScroll(id="preview-scroll", can_focus=True):
                    with Horizontal(classes="preview-row"):
                        yield Static(t("prank.display_title", lang), classes="preview-key")
                        yield Static(cfg.title, classes="preview-val-accent", id="preview-title-val", markup=False)
                    with Horizontal(classes="preview-row"):
                        yield Static(t("prank.message", lang), classes="preview-key")
                        yield Static(cfg.custom_message, classes="preview-val", id="preview-msg-val", markup=False)
                    with Horizontal(classes="preview-row"):
                        yield Static(t("prank.reveal", lang), classes="preview-key")
                        yield Static(cfg.reveal_message, classes="preview-val", id="preview-reveal-val", markup=False)
                    with Horizontal(classes="preview-row"):
                        yield Static(t("prank.preview_params", lang), classes="preview-key")
                        yield Static(params_text, classes="preview-val", id="preview-params-val")
                    with Horizontal(classes="preview-row"):
                        yield Static(t("prank.preview_stages", lang), classes="preview-key")
                        yield Static(stage_lines, classes="preview-val", id="preview-stages-val", markup=False)
                    with Horizontal(classes="preview-row"):
                        yield Static(t("prank.preview_safety", lang), classes="preview-key")
                        yield Static(safety_text, classes="preview-val", id="preview-safety-val")

                with Horizontal(classes="preview-actions"):
                    yield Button(
                        t("prank.btn_start", lang),
                        id="btn-preview-start",
                        classes="action-btn",
                    )
                    yield Button(
                        t("action.back", lang),
                        id="btn-preview-back",
                        classes="action-btn",
                    )

        yield KeyHintBar(
            [
                ("Ctrl+R", t("keys.start", lang)),
                ("Esc", t("keys.back", lang)),
            ],
            id="preview-key-bar",
        )
        yield ResizeNotice(id="resize-notice")

    def on_mount(self) -> None:
        self.apply_viewport_size(self.app.size.width, self.app.size.height)
        self.query_one("#btn-preview-back", Button).focus()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        event.stop()
        if event.button.id == "btn-preview-start":
            self.action_launch_from_preview()
        elif event.button.id == "btn-preview-back":
            self.action_go_back()

    def action_launch_from_preview(self) -> None:
        callback = self._on_start_requested
        cfg = self._preview.config
        self.poptrap_app.close_active_screen()
        if callback is not None:
            callback(cfg)

    def action_go_back(self) -> None:
        self.poptrap_app.close_active_screen()


class CreatePrankScreen(BasePopTrapScreen):
    """Terminal workflow to select a prank template, configure, preview, and run."""

    BINDINGS = [
        Binding("escape", "go_back", "Back", show=False),
        Binding("ctrl+p", "preview_prank", "Preview", show=False),
        Binding("ctrl+s", "open_studio", "Studio", show=False),
        Binding("ctrl+o", "open_presets", "Presets", show=False),
        Binding("ctrl+r", "start_session", "Start", show=False),
        Binding("ctrl+x", "stop_session", "Stop", show=False),
    ]

    def __init__(self) -> None:
        super().__init__()
        self._refresh_timer: Timer | None = None
        self._suppress_template_autofill: bool = False

    def compose(self) -> ComposeResult:
        app = self.poptrap_app
        lang = app.config.ui.default_language
        unicode_ok = app.terminal_capabilities.supports_unicode
        templates = app.prank_controller.list_templates()
        initial_meta = templates[0]
        tui_theme_map: dict[str, PrankTheme] = {
            "cyan-dark": PrankTheme.DARK,
            "ice-dark": PrankTheme.NEON,
            "mono-dark": PrankTheme.MINIMAL,
        }
        initial_theme = tui_theme_map.get(app.config.ui.theme.value, PrankTheme.DARK)
        initial_sound = app.config.audio.sound_enabled

        with Container(classes="screen-shell"):
            with Vertical(classes="content-frame prank-frame"):
                yield SectionHeader(
                    t("prank.title", lang),
                    t("prank.meta_count", lang, count=len(templates)),
                    unicode_enabled=unicode_ok,
                    id="prank-header",
                )

                with Horizontal(id="prank-mode-bar"):
                    yield Button(
                        t("prank.mode_quick", lang),
                        id="btn-quick-create",
                        classes="action-btn mode-active",
                    )
                    yield Button(
                        t("prank.mode_studio", lang),
                        id="btn-open-studio",
                        classes="action-btn",
                    )
                    yield Button(
                        t("prank.mode_presets", lang),
                        id="btn-load-preset",
                        classes="action-btn",
                    )

                with VerticalScroll(id="prank-scroll", can_focus=False):
                    with Horizontal(classes="prank-row"):
                        yield Static(t("prank.template", lang), classes="prank-label")
                        yield Select[TemplateId](
                            [(meta.name, meta.template_id) for meta in templates],
                            value=initial_meta.template_id,
                            allow_blank=False,
                            type_to_search=False,
                            compact=True,
                            id="prank-template",
                        )

                    yield Static(initial_meta.summary, id="prank-template-summary")

                    with Horizontal(classes="prank-row"):
                        yield Static(t("prank.display_title", lang), classes="prank-label")
                        yield Input(
                            value=initial_meta.default_title,
                            max_length=MAX_TITLE_LENGTH,
                            id="prank-title",
                        )

                    with Horizontal(classes="prank-row"):
                        yield Static(t("prank.message", lang), classes="prank-label")
                        yield Input(
                            value=initial_meta.default_message,
                            max_length=MAX_MESSAGE_LENGTH,
                            id="prank-message",
                        )

                    with Horizontal(
                        classes="prank-row hidden",
                        id="row-extra-messages",
                    ):
                        yield Static(t("prank.extra_cards", lang), classes="prank-label")
                        yield Input(
                            value="",
                            max_length=MAX_MESSAGE_LENGTH,
                            id="prank-extra-messages",
                        )

                    with Horizontal(classes="prank-row"):
                        yield Static(t("prank.reveal", lang), classes="prank-label")
                        yield Input(
                            value=initial_meta.default_reveal_message,
                            max_length=MAX_REVEAL_LENGTH,
                            id="prank-reveal",
                        )

                    with Horizontal(classes="prank-row"):
                        yield Static(t("prank.theme", lang), classes="prank-label")
                        yield Select[PrankTheme](
                            [
                                (t(f"theme.{th.value}", lang), th)
                                for th in PRIMARY_PRANK_THEMES
                            ],
                            value=initial_theme,
                            allow_blank=False,
                            type_to_search=False,
                            compact=True,
                            id="prank-theme",
                        )

                    with Horizontal(classes="prank-row"):
                        yield Static(t("prank.duration", lang), classes="prank-label")
                        yield Input(
                            value=str(initial_meta.default_duration_seconds),
                            max_length=4,
                            classes="short-input",
                            id="prank-duration",
                        )

                    with Horizontal(
                        classes="prank-row hidden",
                        id="row-popup-count",
                    ):
                        yield Static(t("prank.popup_count", lang), classes="prank-label")
                        yield Input(
                            value=str(DEFAULT_POPUP_COUNT),
                            max_length=3,
                            classes="short-input",
                            id="prank-popup-count",
                        )

                    with Horizontal(classes="prank-row"):
                        yield Static(t("prank.intensity", lang), classes="prank-label")
                        yield Select[VisualIntensity](
                            [
                                (t(f"prank.intensity.{lv.value}", lang), lv)
                                for lv in VisualIntensity
                            ],
                            value=VisualIntensity.MEDIUM,
                            allow_blank=False,
                            type_to_search=False,
                            compact=True,
                            id="prank-intensity",
                        )

                    with Horizontal(classes="prank-row"):
                        yield Static(t("prank.speed", lang), classes="prank-label")
                        yield Select[AnimationSpeed](
                            [
                                (t(f"prank.speed.{sp.value}", lang), sp)
                                for sp in AnimationSpeed
                            ],
                            value=AnimationSpeed.NORMAL,
                            allow_blank=False,
                            type_to_search=False,
                            compact=True,
                            id="prank-speed",
                        )

                    with Horizontal(classes="prank-row"):
                        yield Static(t("prank.sound", lang), classes="prank-label")
                        with Horizontal(classes="toggle-group"):
                            yield Switch(
                                value=initial_sound,
                                animate=False,
                                id="prank-sound",
                            )
                            yield Static(
                                self._on_off_text(initial_sound, lang),
                                classes="toggle-state-label",
                                id="prank-sound-label",
                            )

                    with Horizontal(classes="prank-row"):
                        yield Static(t("prank.auto_terminate", lang), classes="prank-label")
                        with Horizontal(classes="toggle-group"):
                            yield Switch(
                                value=True,
                                animate=False,
                                id="prank-auto-terminate",
                            )
                            yield Static(
                                self._on_off_text(True, lang),
                                classes="toggle-state-label",
                                id="prank-auto-label",
                            )

                with Vertical(id="session-panel", classes="hidden"):
                    with Horizontal(classes="session-line"):
                        yield Static(t("prank.server_addr", lang), classes="session-key")
                        yield Static("", classes="session-val", id="val-server-url", markup=False)
                    with Horizontal(classes="session-line"):
                        yield Static(t("prank.session_url", lang), classes="session-key")
                        yield Static("", classes="session-url", id="val-session-url", markup=False)
                    with Horizontal(classes="session-line"):
                        yield Static(t("prank.session_state", lang), classes="session-key")
                        yield Static("", classes="session-val", id="val-session-state")

                yield Static("", id="prank-feedback", markup=False)

                with Horizontal(classes="prank-actions"):
                    yield Button(
                        t("prank.btn_preview", lang),
                        id="btn-preview",
                        classes="action-btn",
                    )
                    yield Button(
                        t("prank.btn_start", lang),
                        id="btn-start",
                        classes="action-btn",
                    )
                    yield Button(
                        t("prank.btn_stop", lang),
                        id="btn-stop",
                        classes="action-btn hidden",
                    )
                    yield Button(
                        t("action.back", lang),
                        id="btn-back",
                        classes="action-btn",
                    )

        yield KeyHintBar(self._build_key_hints(lang), id="prank-key-bar")
        yield ResizeNotice(id="resize-notice")

    def on_mount(self) -> None:
        self.apply_viewport_size(self.app.size.width, self.app.size.height)
        self._sync_template_visibility(self._selected_template_id())
        self._sync_session_panel()
        self._refresh_timer = self.set_interval(0.5, self._sync_session_panel)
        self.query_one("#prank-template", Select).focus()

    def on_unmount(self) -> None:
        if self._refresh_timer is not None:
            self._refresh_timer.stop()
            self._refresh_timer = None

    def on_screen_resume(self) -> None:
        self.apply_viewport_size(self.app.size.width, self.app.size.height)
        self._sync_session_panel()

    @staticmethod
    def _on_off_text(enabled: bool, lang: LanguageCode | object) -> str:
        from poptrap.core.config import LanguageCode as _LC

        resolved = lang if isinstance(lang, _LC) else _LC.EN
        return t("settings.sound_on" if enabled else "settings.sound_off", resolved)

    @staticmethod
    def _build_key_hints(lang: object) -> list[tuple[str, str]]:
        from poptrap.core.config import LanguageCode as _LC

        resolved = lang if isinstance(lang, _LC) else _LC.EN
        return [
            ("Tab", t("keys.navigate", resolved)),
            ("Ctrl+P", t("keys.preview", resolved)),
            ("Ctrl+R", t("keys.start", resolved)),
            ("Ctrl+X", t("keys.stop", resolved)),
            ("Esc", t("keys.back", resolved)),
        ]

    def _selected_template_id(self) -> TemplateId:
        raw = self.query_one("#prank-template", Select).value
        return raw if isinstance(raw, TemplateId) else TemplateId.GLITCH

    def _sync_template_visibility(self, template_id: TemplateId) -> None:
        meta = self.poptrap_app.prank_controller.get_template(template_id).metadata
        self.query_one("#prank-template-summary", Static).update(meta.summary)
        supports_popups = meta.supports_popup_count
        self.query_one("#row-popup-count").set_class(not supports_popups, "hidden")
        self.query_one("#row-extra-messages").set_class(not supports_popups, "hidden")

    def _populate_template_defaults(self, template_id: TemplateId) -> None:
        meta = self.poptrap_app.prank_controller.get_template(template_id).metadata
        self.query_one("#prank-title", Input).value = meta.default_title
        self.query_one("#prank-message", Input).value = meta.default_message
        self.query_one("#prank-reveal", Input).value = meta.default_reveal_message
        self.query_one("#prank-duration", Input).value = str(
            meta.default_duration_seconds
        )
        if meta.supports_popup_count and meta.default_popup_count is not None:
            self.query_one("#prank-popup-count", Input).value = str(
                meta.default_popup_count
            )
        self._sync_template_visibility(template_id)

    def _set_feedback(self, message: str, *, is_error: bool = False) -> None:
        fb = self.query_one("#prank-feedback", Static)
        fb.set_class(is_error, "error")
        fb.update(message)

    def build_validated_config(self) -> PrankConfig:
        """Read current UI controls and return a validated ``PrankConfig``.

        Raises:
            PrankConfigError: If any user-entered field is invalid or out of bounds.
        """
        lang = self.poptrap_app.config.ui.default_language
        template_id = self._selected_template_id()
        meta = self.poptrap_app.prank_controller.get_template(template_id).metadata

        duration = parse_positive_int_input(
            self.query_one("#prank-duration", Input).value,
            field_name="duration_seconds",
            label=f"{t('prank.duration', lang)} ({MIN_DURATION_SECONDS}-{MAX_DURATION_SECONDS})",
        )

        popup_count: int | None = None
        extra_messages: tuple[str, ...] = ()
        if meta.supports_popup_count:
            popup_count = parse_positive_int_input(
                self.query_one("#prank-popup-count", Input).value,
                field_name="popup_count",
                label=f"{t('prank.popup_count', lang)} ({MIN_POPUP_COUNT}-{MAX_POPUP_COUNT})",
            )
            extra_messages = parse_semicolon_messages(
                self.query_one("#prank-extra-messages", Input).value
            )

        raw_theme = self.query_one("#prank-theme", Select).value
        theme = raw_theme if isinstance(raw_theme, PrankTheme) else PrankTheme.DARK

        raw_intensity = self.query_one("#prank-intensity", Select).value
        intensity = (
            raw_intensity
            if isinstance(raw_intensity, VisualIntensity)
            else VisualIntensity.MEDIUM
        )

        raw_speed = self.query_one("#prank-speed", Select).value
        speed = (
            raw_speed
            if isinstance(raw_speed, AnimationSpeed)
            else AnimationSpeed.NORMAL
        )

        payload: dict[str, object] = {
            "template_id": template_id.value,
            "title": self.query_one("#prank-title", Input).value,
            "custom_message": self.query_one("#prank-message", Input).value,
            "reveal_message": self.query_one("#prank-reveal", Input).value,
            "theme": theme.value,
            "duration_seconds": duration,
            "intensity": intensity.value,
            "animation_speed": speed.value,
            "sound_enabled": bool(self.query_one("#prank-sound", Switch).value),
            "popup_count": popup_count,
            "custom_messages": list(extra_messages),
            "auto_terminate": bool(
                self.query_one("#prank-auto-terminate", Switch).value
            ),
        }
        return self.poptrap_app.prank_controller.validate_config(payload)

    def _sync_session_panel(self) -> None:
        app = self.poptrap_app
        lang = app.config.ui.default_language
        sep = "·" if app.terminal_capabilities.supports_unicode else "/"
        templates_count = len(app.prank_controller.list_templates())

        panel = self.query_one("#session-panel", Vertical)
        stop_btn = self.query_one("#btn-stop", Button)
        header = self.query_one("#prank-header", SectionHeader)

        session_id = app.active_session_id
        server_url = app.experience_server.base_url

        if session_id is None:
            panel.add_class("hidden")
            stop_btn.add_class("hidden")
            header.set_content(
                t("prank.title", lang),
                t("prank.meta_count", lang, count=templates_count),
                accent_meta=False,
            )
            return

        try:
            snap = app.prank_controller.get_session(session_id)
        except SessionNotFoundError:
            app.clear_tracked_session()
            panel.add_class("hidden")
            stop_btn.add_class("hidden")
            header.set_content(
                t("prank.title", lang),
                t("prank.meta_count", lang, count=templates_count),
                accent_meta=False,
            )
            return

        live = not is_terminal_state(snap.state) and server_url is not None
        panel.remove_class("hidden")
        stop_btn.set_class(not live, "hidden")

        header.set_content(
            t("prank.title", lang),
            t("prank.meta_live", lang)
            if live
            else t("prank.meta_count", lang, count=templates_count),
            accent_meta=live,
        )

        self.query_one("#val-server-url", Static).update(
            server_url if server_url else "stopped"
        )
        self.query_one("#val-session-url", Static).update(
            f"{server_url}/s/{snap.session_id}" if server_url else snap.session_id
        )

        parts = [snap.state.value.upper(), snap.template_name]
        if snap.state is SessionState.READY:
            parts.append(t("prank.awaiting_consent", lang))
        elif snap.state is SessionState.ACTIVE and snap.remaining_seconds is not None:
            parts.append(f"{int(round(snap.remaining_seconds))}s left")
            parts.append(f"clients={snap.connected_clients}")
        elif snap.termination_reason:
            parts.append(snap.termination_reason)
        self.query_one("#val-session-state", Static).update(f" {sep} ".join(parts))

    def on_select_changed(self, event: Select.Changed) -> None:
        if (
            event.select.id == "prank-template"
            and not self._suppress_template_autofill
            and isinstance(event.value, TemplateId)
        ):
            self._populate_template_defaults(event.value)
            self._set_feedback("")

    def on_switch_changed(self, event: Switch.Changed) -> None:
        lang = self.poptrap_app.config.ui.default_language
        if event.switch.id == "prank-sound":
            self.query_one("#prank-sound-label", Static).update(
                self._on_off_text(event.value, lang)
            )
        elif event.switch.id == "prank-auto-terminate":
            self.query_one("#prank-auto-label", Static).update(
                self._on_off_text(event.value, lang)
            )

    def on_button_pressed(self, event: Button.Pressed) -> None:
        event.stop()
        btn_id = event.button.id
        if btn_id == "btn-quick-create":
            self.query_one("#prank-template", Select).focus()
        elif btn_id == "btn-open-studio":
            self.action_open_studio()
        elif btn_id == "btn-load-preset":
            self.action_open_presets()
        elif btn_id == "btn-preview":
            self.action_preview_prank()
        elif btn_id == "btn-start":
            self.action_start_session()
        elif btn_id == "btn-stop":
            self.action_stop_session()
        elif btn_id == "btn-back":
            self.action_go_back()

    def action_open_studio(self) -> None:
        from poptrap.ui.screens.prank_studio import PrankStudioScreen

        self._set_feedback("")
        self.poptrap_app.emit_feedback_sound()
        self.app.push_screen(PrankStudioScreen())

    def action_open_presets(self) -> None:
        from poptrap.ui.screens.prank_studio import PresetManagerScreen

        self._set_feedback("")
        self.poptrap_app.emit_feedback_sound()
        self.app.push_screen(
            PresetManagerScreen(
                on_session_launched=lambda url: (
                    self._sync_session_panel(),
                    self._set_feedback(f"Local session live: {url}", is_error=False),
                )[-1]
            )
        )

    def action_preview_prank(self) -> None:
        try:
            config = self.build_validated_config()
            preview = self.poptrap_app.prank_controller.preview_config(config)
        except PopTrapError as exc:
            self._set_feedback(exc.message, is_error=True)
            return

        self._set_feedback("")
        self.poptrap_app.emit_feedback_sound()
        self.app.push_screen(
            PrankPreviewScreen(preview, on_start_requested=self._start_with_config)
        )

    def _start_with_config(self, config: PrankConfig) -> None:
        lang = self.poptrap_app.config.ui.default_language
        try:
            self.poptrap_app.launch_prank_session(config)
        except PopTrapError as exc:
            self._set_feedback(exc.message, is_error=True)
            return

        self._sync_session_panel()
        self._set_feedback(t("prank.started", lang), is_error=False)

    def action_start_session(self) -> None:
        try:
            config = self.build_validated_config()
        except PopTrapError as exc:
            self._set_feedback(exc.message, is_error=True)
            return
        self._start_with_config(config)

    def action_stop_session(self) -> None:
        lang = self.poptrap_app.config.ui.default_language
        stopped = self.poptrap_app.stop_prank_session()
        self._sync_session_panel()
        if stopped:
            self._set_feedback(t("prank.stopped", lang), is_error=False)

    def action_go_back(self) -> None:
        self.poptrap_app.close_active_screen()
