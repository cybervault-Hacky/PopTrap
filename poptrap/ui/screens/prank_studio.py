"""Phase 4 Prank Studio & Local Preset Manager screens for the POPTRAP TUI.

Provides custom template parameter controls (strictly filtered to the selected
template), a bounded multi-stage experience composer (<= 5 stages, <= 120s),
real local engine live preview (start, restart, stop, return to editor), and
atomic local preset management (save with overwrite confirmation, load, view
details, rename, duplicate, delete).
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Container, Horizontal, Vertical, VerticalScroll
from textual.timer import Timer
from textual.widgets import Button, Input, Select, Static, Switch

from poptrap.core.exceptions import (
    PopTrapError,
    PresetExistsError,
    SessionNotFoundError,
    StudioValidationError,
)
from poptrap.core.i18n import t
from poptrap.engine.lifecycle import is_terminal_state
from poptrap.engine.models import (
    AnimationSpeed,
    DEFAULT_POPUP_COUNT,
    MAX_MESSAGE_LENGTH,
    MAX_POPUP_COUNT,
    MAX_REVEAL_LENGTH,
    MAX_TITLE_LENGTH,
    MIN_DURATION_SECONDS,
    MIN_POPUP_COUNT,
    PRIMARY_PRANK_THEMES,
    PrankTheme,
    SessionState,
    TemplateId,
    VisualIntensity,
)
from poptrap.engine.studio import (
    MAX_COMPOSITION_DURATION_SECONDS,
    MAX_EXPERIENCE_NAME_LENGTH,
    MAX_STAGE_DURATION_SECONDS,
    MAX_STUDIO_STAGES,
    MIN_STAGE_DURATION_SECONDS,
    CardPresentationStyle,
    CompletionStyle,
    PopupMessagePreset,
    PresetRecord,
    PresetSummary,
    RingPresentationStyle,
    StageKind,
    StudioConfig,
    StudioStage,
    add_stage,
    build_default_stages,
    build_default_studio_config,
    build_studio_timeline,
    move_stage,
    remove_stage,
    update_stage,
)
from poptrap.engine.studio.models import (
    MAX_CLEANUP_SECONDS,
    MAX_CRASH_TRANSITION_MS,
    MAX_RECOVERY_SECONDS,
    MAX_RGB_OFFSET_PX,
    MAX_SCAN_STAGES,
    MAX_SCANLINE_OPACITY,
    MAX_SHORT_STATUS_LENGTH,
    MAX_STAGE_CAPTION_LENGTH,
    MAX_STAGE_LABEL_LENGTH,
    MIN_CLEANUP_SECONDS,
    MIN_CRASH_TRANSITION_MS,
    MIN_RECOVERY_SECONDS,
    MIN_RGB_OFFSET_PX,
    MIN_SCAN_STAGES,
    MIN_SCANLINE_OPACITY,
)
from poptrap.engine.validation import (
    parse_positive_int_input,
    parse_semicolon_messages,
)
from poptrap.ui.screens.base import BasePopTrapScreen
from poptrap.ui.widgets import KeyHintBar, ResizeNotice, SectionHeader


def _parse_float_input(raw: str, *, field_name: str, label: str) -> float:
    cleaned = raw.strip()
    if not cleaned:
        raise StudioValidationError(
            f"{label} is required.",
            field_errors=(f"{field_name}: Required number.",),
        )
    try:
        value = float(cleaned)
    except ValueError as exc:
        raise StudioValidationError(
            f"{label} must be a valid decimal number.",
            field_errors=(f"{field_name}: Must be numeric.",),
        ) from exc
    return value


def _map_app_theme(theme_value: str) -> PrankTheme:
    mapping: dict[str, PrankTheme] = {
        "cyan-dark": PrankTheme.DARK,
        "ice-dark": PrankTheme.NEON,
        "mono-dark": PrankTheme.MINIMAL,
    }
    return mapping.get(theme_value, PrankTheme.DARK)


_TEMPLATE_GROUP_IDS: dict[TemplateId, str] = {
    TemplateId.GLITCH: "group-glitch",
    TemplateId.VIRUS_ALERT: "group-virus-alert",
    TemplateId.SYSTEM_CRASH: "group-system-crash",
    TemplateId.POPUP_STORM: "group-popup-storm",
    TemplateId.COUNTDOWN: "group-countdown",
}


class PresetManagerScreen(BasePopTrapScreen):
    """Manage local Prank Studio presets: load, inspect, rename, duplicate, delete."""

    BINDINGS = [
        Binding("escape", "go_back", "Back", show=False),
        Binding("ctrl+o", "load_selected", "Load", show=False),
        Binding("ctrl+r", "run_selected", "Launch", show=False),
    ]

    def __init__(
        self,
        *,
        on_preset_loaded: Callable[[PresetRecord], None] | None = None,
        on_session_launched: Callable[[str], None] | None = None,
    ) -> None:
        super().__init__()
        self._on_preset_loaded = on_preset_loaded
        self._on_session_launched = on_session_launched
        self._summaries: tuple[PresetSummary, ...] = ()
        self._selected_id: str | None = None
        self._pending_delete_id: str | None = None

    def compose(self) -> ComposeResult:
        app = self.poptrap_app
        lang = app.config.ui.default_language
        unicode_ok = app.terminal_capabilities.supports_unicode

        with Container(classes="screen-shell"):
            with Vertical(classes="content-frame studio-frame"):
                yield SectionHeader(
                    t("studio.presets_title", lang),
                    "0 PRESETS",
                    unicode_enabled=unicode_ok,
                    id="presets-header",
                )

                with VerticalScroll(id="presets-scroll", can_focus=True):
                    yield Static("", id="preset-warnings", classes="studio-warn hidden")

                    with Horizontal(classes="studio-row", id="row-preset-picker"):
                        yield Static(t("studio.preset_label", lang), classes="studio-label")
                        yield Select[str](
                            [("(No saved presets)", "")],
                            value="",
                            allow_blank=False,
                            type_to_search=False,
                            compact=True,
                            id="preset-select",
                        )

                    yield Static(
                        t("studio.presets_empty", lang),
                        id="preset-details",
                        classes="studio-detail-box",
                    )

                    with Horizontal(classes="studio-row", id="row-preset-rename"):
                        yield Static(t("studio.rename_label", lang), classes="studio-label")
                        yield Input(
                            value="",
                            max_length=MAX_EXPERIENCE_NAME_LENGTH,
                            id="preset-rename-input",
                        )
                        yield Button(
                            t("studio.btn_rename", lang),
                            id="btn-preset-rename",
                            classes="action-btn",
                        )

                with Horizontal(id="preset-delete-bar", classes="confirm-bar hidden"):
                    yield Static("", id="preset-delete-msg", classes="confirm-msg")
                    yield Button(
                        t("studio.btn_confirm_delete", lang),
                        id="btn-confirm-delete",
                        classes="action-btn",
                    )
                    yield Button(
                        t("studio.btn_cancel", lang),
                        id="btn-cancel-delete",
                        classes="action-btn",
                    )

                yield Static("", id="preset-feedback", classes="studio-feedback")

                with Horizontal(classes="studio-actions"):
                    yield Button(
                        t("studio.btn_load_edit", lang),
                        id="btn-preset-load",
                        classes="action-btn",
                    )
                    yield Button(
                        t("studio.btn_launch", lang),
                        id="btn-preset-run",
                        classes="action-btn",
                    )
                    yield Button(
                        t("studio.btn_duplicate", lang),
                        id="btn-preset-duplicate",
                        classes="action-btn",
                    )
                    yield Button(
                        t("studio.btn_delete", lang),
                        id="btn-preset-delete",
                        classes="action-btn",
                    )
                    yield Button(
                        t("action.back", lang),
                        id="btn-preset-back",
                        classes="action-btn",
                    )

        yield KeyHintBar(
            [
                ("Tab", t("keys.navigate", lang)),
                ("Ctrl+O", t("studio.key_load", lang)),
                ("Ctrl+R", t("keys.start", lang)),
                ("Esc", t("keys.back", lang)),
            ],
            id="presets-key-bar",
        )
        yield ResizeNotice(id="resize-notice")

    def on_mount(self) -> None:
        self.apply_viewport_size(self.app.size.width, self.app.size.height)
        self.refresh_presets()
        self.query_one("#btn-preset-back", Button).focus()

    def on_screen_resume(self) -> None:
        self.apply_viewport_size(self.app.size.width, self.app.size.height)
        self.refresh_presets(preferred_id=self._selected_id)

    def _set_feedback(self, message: str, *, is_error: bool = False) -> None:
        fb = self.query_one("#preset-feedback", Static)
        fb.set_class(is_error, "error")
        fb.update(message)

    def _hide_delete_confirm(self) -> None:
        self._pending_delete_id = None
        self.query_one("#preset-delete-bar", Horizontal).add_class("hidden")

    def refresh_presets(self, *, preferred_id: str | None = None) -> None:
        """Reload the local preset list and details cleanly."""
        app = self.poptrap_app
        lang = app.config.ui.default_language
        store = app.prank_controller.presets
        summaries, warnings = store.list_presets()
        self._summaries = summaries
        self._hide_delete_confirm()

        self.query_one("#presets-header", SectionHeader).set_content(
            t("studio.presets_title", lang),
            t("studio.presets_count", lang, count=len(summaries)),
            accent_meta=len(summaries) > 0,
        )

        warn_box = self.query_one("#preset-warnings", Static)
        if warnings:
            warn_box.update(
                f"Skipped {len(warnings)} invalid local preset file(s) safely."
            )
            warn_box.remove_class("hidden")
        else:
            warn_box.update("")
            warn_box.add_class("hidden")

        picker = self.query_one("#preset-select", Select)
        has_items = len(summaries) > 0

        with picker.prevent(Select.Changed):
            if has_items:
                options = [
                    (f"{s.name} ({s.template_id.value}, {s.duration_seconds}s)", s.preset_id)
                    for s in summaries
                ]
                ids = {s.preset_id for s in summaries}
                chosen = preferred_id if preferred_id in ids else summaries[0].preset_id
                picker.set_options(options)
                picker.value = chosen
                self._selected_id = chosen
            else:
                picker.set_options([("(No saved local presets)", "")])
                picker.value = ""
                self._selected_id = None

        for btn_id in (
            "#btn-preset-load",
            "#btn-preset-run",
            "#btn-preset-duplicate",
            "#btn-preset-delete",
            "#btn-preset-rename",
        ):
            self.query_one(btn_id, Button).disabled = not has_items
        self.query_one("#preset-rename-input", Input).disabled = not has_items
        self._render_selected_details()

    def _render_selected_details(self) -> None:
        app = self.poptrap_app
        lang = app.config.ui.default_language
        box = self.query_one("#preset-details", Static)
        rename_input = self.query_one("#preset-rename-input", Input)

        if not self._selected_id:
            rename_input.value = ""
            box.update(
                f"{t('studio.presets_empty', lang)}\n"
                f"Storage: {app.prank_controller.presets.presets_dir}"
            )
            return

        try:
            record = app.prank_controller.presets.load_preset(self._selected_id)
            summary = app.prank_controller.preview_studio(record.config)
        except PopTrapError as exc:
            box.update(exc.message)
            return

        cfg = record.config
        rename_input.value = record.name
        stages_desc = (
            " -> ".join(
                f"{st.label} ({st.duration_seconds}s)" for st in cfg.stages
            )
            if cfg.stages
            else f"Single-stage ({cfg.duration_seconds}s)"
        )
        updated_str = record.updated_at.strftime("%Y-%m-%d %H:%M UTC")
        lines = [
            f"Name:     {record.name}  [id: {record.preset_id}]",
            f"Template: {summary['template_name']}  |  Theme: {cfg.theme.value}",
            f"Timing:   {cfg.effective_duration_seconds}s  |  Speed: {cfg.animation_speed.value}  |  Intensity: {cfg.intensity.value}  |  Sound: {'on' if cfg.sound_enabled else 'off'}",
            f"Title:    {cfg.title}",
            f"Reveal:   {cfg.reveal_message}",
            f"Sequence: {stages_desc}",
            f"Updated:  {updated_str}",
        ]
        box.update("\n".join(lines))

    def on_select_changed(self, event: Select.Changed) -> None:
        if (
            event.select.id == "preset-select"
            and isinstance(event.value, str)
            and bool(event.value)
            and event.value != self._selected_id
        ):
            self._selected_id = event.value
            self._hide_delete_confirm()
            self._render_selected_details()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        event.stop()
        bid = event.button.id
        if bid == "btn-preset-load":
            self.action_load_selected()
        elif bid == "btn-preset-run":
            self.action_run_selected()
        elif bid == "btn-preset-rename":
            self.action_rename_selected()
        elif bid == "btn-preset-duplicate":
            self.action_duplicate_selected()
        elif bid == "btn-preset-delete":
            self.action_request_delete()
        elif bid == "btn-confirm-delete":
            self.action_confirm_delete()
        elif bid == "btn-cancel-delete":
            self._hide_delete_confirm()
        elif bid == "btn-preset-back":
            self.action_go_back()

    def action_load_selected(self) -> None:
        if not self._selected_id:
            return
        try:
            record = self.poptrap_app.prank_controller.presets.load_preset(
                self._selected_id
            )
        except PopTrapError as exc:
            self._set_feedback(exc.message, is_error=True)
            return

        self.poptrap_app.emit_feedback_sound()
        if self._on_preset_loaded is not None:
            callback = self._on_preset_loaded
            self.poptrap_app.close_active_screen()
            callback(record)
        else:
            # Replace preset screen with Prank Studio loaded with the preset.
            self.poptrap_app.close_active_screen()
            self.app.push_screen(PrankStudioScreen(initial_preset=record))

    def action_run_selected(self) -> None:
        if not self._selected_id:
            return
        try:
            record = self.poptrap_app.prank_controller.presets.load_preset(
                self._selected_id
            )
            _, url = self.poptrap_app.launch_studio_session(record.config)
        except PopTrapError as exc:
            self._set_feedback(exc.message, is_error=True)
            return

        if self._on_session_launched is not None:
            cb = self._on_session_launched
            self.poptrap_app.close_active_screen()
            cb(url)
        else:
            self._set_feedback(f"Local session live: {url}", is_error=False)

    def action_rename_selected(self) -> None:
        if not self._selected_id:
            return
        new_name = self.query_one("#preset-rename-input", Input).value
        try:
            updated = self.poptrap_app.prank_controller.presets.rename_preset(
                self._selected_id, new_name
            )
        except PopTrapError as exc:
            self._set_feedback(exc.message, is_error=True)
            return

        self.poptrap_app.emit_feedback_sound()
        self.refresh_presets(preferred_id=updated.preset_id)
        self._set_feedback(f"Renamed preset to '{updated.name}'.", is_error=False)

    def action_duplicate_selected(self) -> None:
        if not self._selected_id:
            return
        try:
            copy_rec = self.poptrap_app.prank_controller.presets.duplicate_preset(
                self._selected_id
            )
        except PopTrapError as exc:
            self._set_feedback(exc.message, is_error=True)
            return

        self.poptrap_app.emit_feedback_sound()
        self.refresh_presets(preferred_id=copy_rec.preset_id)
        self._set_feedback(f"Duplicated as '{copy_rec.name}'.", is_error=False)

    def action_request_delete(self) -> None:
        if not self._selected_id:
            return
        self._pending_delete_id = self._selected_id
        current_name = next(
            (s.name for s in self._summaries if s.preset_id == self._selected_id),
            self._selected_id,
        )
        self.query_one("#preset-delete-msg", Static).update(
            f"Delete '{current_name}'?"
        )
        self.query_one("#preset-delete-bar", Horizontal).remove_class("hidden")

    def action_confirm_delete(self) -> None:
        target = self._pending_delete_id
        if not target:
            return
        try:
            self.poptrap_app.prank_controller.presets.delete_preset(target)
        except PopTrapError as exc:
            self._hide_delete_confirm()
            self._set_feedback(exc.message, is_error=True)
            return

        self.poptrap_app.emit_feedback_sound()
        self.refresh_presets()
        self._set_feedback("Preset deleted.", is_error=False)

    def action_go_back(self) -> None:
        self.poptrap_app.close_active_screen()


class PrankStudioScreen(BasePopTrapScreen):
    """Phase 4 Prank Studio custom experience builder, stage composer, & preview."""

    BINDINGS = [
        Binding("escape", "go_back", "Back", show=False),
        Binding("ctrl+p", "start_preview", "Preview", show=False),
        Binding("ctrl+s", "save_preset", "Save", show=False),
        Binding("ctrl+o", "open_presets", "Presets", show=False),
        Binding("ctrl+r", "launch_session", "Launch", show=False),
        Binding("ctrl+x", "stop_preview", "Stop Preview", show=False),
    ]

    def __init__(
        self,
        *,
        initial_preset: PresetRecord | None = None,
        initial_config: StudioConfig | None = None,
    ) -> None:
        super().__init__()
        self._initial_preset = initial_preset
        self._initial_config = initial_config
        self._loaded_preset_id: str | None = (
            initial_preset.preset_id if initial_preset is not None else None
        )
        self._stages: list[StudioStage] = []
        self._selected_stage_idx: int = 0
        self._preview_session_id: str | None = None
        self._pending_overwrite: tuple[StudioConfig, str | None] | None = None
        self._current_template_id: TemplateId = TemplateId.GLITCH
        self._refresh_timer: Timer | None = None

    @property
    def loaded_preset_id(self) -> str | None:
        """Preset ID currently being edited, or None for an unsaved draft."""
        return self._loaded_preset_id

    @property
    def preview_session_id(self) -> str | None:
        """Session ID of the active/most recent local Studio preview, if any."""
        return self._preview_session_id

    @property
    def stages(self) -> tuple[StudioStage, ...]:
        """Current composed stage sequence."""
        return tuple(self._stages)

    def compose(self) -> ComposeResult:
        app = self.poptrap_app
        lang = app.config.ui.default_language
        unicode_ok = app.terminal_capabilities.supports_unicode
        registry = app.prank_controller.registry
        templates = app.prank_controller.list_templates()

        if self._initial_preset is not None:
            seed = self._initial_preset.config
        elif self._initial_config is not None:
            seed = self._initial_config
        else:
            seed = build_default_studio_config(
                templates[0].template_id,
                registry=registry,
                theme=_map_app_theme(app.config.ui.theme.value),
                sound_enabled=app.config.audio.sound_enabled,
            )
        self._stages = list(seed.stages)

        with Container(classes="screen-shell"):
            with Vertical(classes="content-frame studio-frame"):
                yield SectionHeader(
                    t("studio.title", lang),
                    t("prank.meta_count", lang, count=len(templates)),
                    unicode_enabled=unicode_ok,
                    id="studio-header",
                )

                with VerticalScroll(id="studio-scroll", can_focus=False):
                    # --- 1. Template selection ---
                    with Horizontal(classes="studio-row"):
                        yield Static(t("prank.template", lang), classes="studio-label")
                        yield Select[TemplateId](
                            [(m.name, m.template_id) for m in templates],
                            value=seed.template_id,
                            allow_blank=False,
                            type_to_search=False,
                            compact=True,
                            id="studio-template",
                        )
                        yield Button(
                            t("studio.btn_defaults", lang),
                            id="btn-studio-defaults",
                            classes="action-btn",
                        )
                    yield Static("", id="studio-template-summary", classes="studio-subtext")

                    # --- 2. General customization ---
                    with Horizontal(classes="studio-row"):
                        yield Static(t("studio.exp_name", lang), classes="studio-label")
                        yield Input(
                            value=seed.name,
                            max_length=MAX_EXPERIENCE_NAME_LENGTH,
                            id="studio-name",
                        )
                    with Horizontal(classes="studio-row"):
                        yield Static(t("prank.display_title", lang), classes="studio-label")
                        yield Input(
                            value=seed.title,
                            max_length=MAX_TITLE_LENGTH,
                            id="studio-title",
                        )
                    with Horizontal(classes="studio-row"):
                        yield Static(t("prank.message", lang), classes="studio-label")
                        yield Input(
                            value=seed.custom_message,
                            max_length=MAX_MESSAGE_LENGTH,
                            id="studio-message",
                        )
                    with Horizontal(classes="studio-row"):
                        yield Static(t("prank.reveal", lang), classes="studio-label")
                        yield Input(
                            value=seed.reveal_message,
                            max_length=MAX_REVEAL_LENGTH,
                            id="studio-reveal",
                        )
                    with Horizontal(classes="studio-row"):
                        yield Static(t("prank.theme", lang), classes="studio-label")
                        yield Select[PrankTheme](
                            [(t(f"theme.{th.value}", lang), th) for th in PrankTheme],
                            value=seed.theme,
                            allow_blank=False,
                            type_to_search=False,
                            compact=True,
                            id="studio-theme",
                        )
                    with Horizontal(classes="studio-row"):
                        yield Static(t("prank.duration", lang), classes="studio-label")
                        yield Input(
                            value=str(seed.duration_seconds),
                            max_length=4,
                            classes="short-input",
                            id="studio-duration",
                        )
                    with Horizontal(classes="studio-row"):
                        yield Static(t("prank.intensity", lang), classes="studio-label")
                        yield Select[VisualIntensity](
                            [
                                (t(f"prank.intensity.{lv.value}", lang), lv)
                                for lv in VisualIntensity
                            ],
                            value=seed.intensity,
                            allow_blank=False,
                            type_to_search=False,
                            compact=True,
                            id="studio-intensity",
                        )
                    with Horizontal(classes="studio-row"):
                        yield Static(t("prank.speed", lang), classes="studio-label")
                        yield Select[AnimationSpeed](
                            [
                                (t(f"prank.speed.{sp.value}", lang), sp)
                                for sp in AnimationSpeed
                            ],
                            value=seed.animation_speed,
                            allow_blank=False,
                            type_to_search=False,
                            compact=True,
                            id="studio-speed",
                        )
                    with Horizontal(classes="studio-row"):
                        yield Static(t("prank.sound", lang), classes="studio-label")
                        with Horizontal(classes="toggle-group"):
                            yield Switch(
                                value=seed.sound_enabled,
                                animate=False,
                                id="studio-sound",
                            )
                            yield Static(
                                t(
                                    "settings.sound_on"
                                    if seed.sound_enabled
                                    else "settings.sound_off",
                                    lang,
                                ),
                                classes="toggle-state-label",
                                id="studio-sound-label",
                            )

                    # --- 3. Context-sensitive template controls ---
                    yield Static(
                        t("studio.section_specific", lang),
                        classes="studio-section-divider",
                        id="studio-specific-heading",
                    )

                    # 3A: Glitch Attack only
                    with Vertical(id="group-glitch", classes="specific-group"):
                        with Horizontal(classes="studio-row"):
                            yield Static("Scanlines", classes="studio-label")
                            yield Input(
                                value="0.20",
                                max_length=5,
                                classes="short-input",
                                id="glitch-scanline",
                            )
                        with Horizontal(classes="studio-row"):
                            yield Static("RGB Offset (px)", classes="studio-label")
                            yield Input(
                                value="6",
                                max_length=3,
                                classes="short-input",
                                id="glitch-rgb",
                            )
                        with Horizontal(classes="studio-row"):
                            yield Static("Recovery (s)", classes="studio-label")
                            yield Input(
                                value="4",
                                max_length=3,
                                classes="short-input",
                                id="glitch-recovery",
                            )

                    # 3B: Fake Virus Alert only
                    with Vertical(id="group-virus-alert", classes="specific-group hidden"):
                        with Horizontal(classes="studio-row"):
                            yield Static("Scan Stages", classes="studio-label")
                            yield Input(
                                value="4",
                                max_length=2,
                                classes="short-input",
                                id="virus-stages",
                            )
                        with Horizontal(classes="studio-row"):
                            yield Static("Completion", classes="studio-label")
                            yield Select[CompletionStyle](
                                [
                                    ("Celebration", CompletionStyle.CELEBRATION),
                                    ("Standard", CompletionStyle.STANDARD),
                                    ("Minimal", CompletionStyle.MINIMAL),
                                ],
                                value=CompletionStyle.CELEBRATION,
                                allow_blank=False,
                                type_to_search=False,
                                compact=True,
                                id="virus-completion",
                            )

                    # 3C: Fake System Crash only
                    with Vertical(id="group-system-crash", classes="specific-group hidden"):
                        with Horizontal(classes="studio-row"):
                            yield Static("Error Title", classes="studio-label")
                            yield Input(
                                value="SIMULATED SUBSYSTEM PAUSE",
                                max_length=MAX_TITLE_LENGTH,
                                id="crash-error-title",
                            )
                        with Horizontal(classes="studio-row"):
                            yield Static("Transition (ms)", classes="studio-label")
                            yield Input(
                                value="360",
                                max_length=4,
                                classes="short-input",
                                id="crash-transition",
                            )
                        with Horizontal(classes="studio-row"):
                            yield Static("Recovery (s)", classes="studio-label")
                            yield Input(
                                value="5",
                                max_length=3,
                                classes="short-input",
                                id="crash-recovery",
                            )
                        with Horizontal(classes="studio-row"):
                            yield Static("Recovery Msg", classes="studio-label")
                            yield Input(
                                value="100% — Simulated recovery complete. Preparing reveal.",
                                max_length=MAX_SHORT_STATUS_LENGTH,
                                id="crash-recovery-msg",
                            )

                    # 3D: Popup Storm only
                    with Vertical(id="group-popup-storm", classes="specific-group hidden"):
                        with Horizontal(classes="studio-row"):
                            yield Static("Card Count", classes="studio-label")
                            yield Input(
                                value=str(DEFAULT_POPUP_COUNT),
                                max_length=3,
                                classes="short-input",
                                id="storm-count",
                            )
                        with Horizontal(classes="studio-row"):
                            yield Static("Card Style", classes="studio-label")
                            yield Select[CardPresentationStyle](
                                [
                                    ("Cascade", CardPresentationStyle.CASCADE),
                                    ("Grid", CardPresentationStyle.GRID),
                                    ("Stacked", CardPresentationStyle.STACKED),
                                ],
                                value=CardPresentationStyle.CASCADE,
                                allow_blank=False,
                                type_to_search=False,
                                compact=True,
                                id="storm-card-style",
                            )
                        with Horizontal(classes="studio-row"):
                            yield Static("Msg Preset", classes="studio-label")
                            yield Select[PopupMessagePreset](
                                [
                                    ("Classic", PopupMessagePreset.CLASSIC),
                                    ("Office Humor", PopupMessagePreset.OFFICE_HUMOR),
                                    ("Pixel Drama", PopupMessagePreset.PIXEL_DRAMA),
                                    ("Custom", PopupMessagePreset.CUSTOM),
                                ],
                                value=PopupMessagePreset.CLASSIC,
                                allow_blank=False,
                                type_to_search=False,
                                compact=True,
                                id="storm-preset",
                            )
                        with Horizontal(classes="studio-row"):
                            yield Static("Custom Lines", classes="studio-label")
                            yield Input(
                                value="",
                                max_length=MAX_MESSAGE_LENGTH,
                                id="storm-extra",
                            )
                        with Horizontal(classes="studio-row"):
                            yield Static("Cleanup (s)", classes="studio-label")
                            yield Input(
                                value="3",
                                max_length=3,
                                classes="short-input",
                                id="storm-cleanup",
                            )

                    # 3E: Countdown Panic only
                    with Vertical(id="group-countdown", classes="specific-group hidden"):
                        with Horizontal(classes="studio-row"):
                            yield Static("Ring Style", classes="studio-label")
                            yield Select[RingPresentationStyle](
                                [
                                    ("Calibrated Ticks", RingPresentationStyle.TICKS),
                                    ("Concentric Double", RingPresentationStyle.DOUBLE),
                                    ("Clean Minimal", RingPresentationStyle.MINIMAL),
                                ],
                                value=RingPresentationStyle.TICKS,
                                allow_blank=False,
                                type_to_search=False,
                                compact=True,
                                id="countdown-ring",
                            )
                        with Horizontal(classes="studio-row"):
                            yield Static("Complete Msg", classes="studio-label")
                            yield Input(
                                value="SURPRISE — ZERO CONSEQUENCES!",
                                max_length=MAX_SHORT_STATUS_LENGTH,
                                id="countdown-completion",
                            )

                    # --- 4. Multi-Stage Experience Composer ---
                    yield Static(
                        t("studio.section_composer", lang),
                        classes="studio-section-divider",
                        id="studio-composer-heading",
                    )
                    yield Static("", id="stage-summary", classes="studio-subtext")

                    with Horizontal(classes="composer-btn-row"):
                        yield Button("4-Stage", id="btn-stage-starter", classes="action-btn")
                        yield Button("+Stage", id="btn-stage-add", classes="action-btn")
                        yield Button("Up", id="btn-stage-up", classes="action-btn")
                        yield Button("Down", id="btn-stage-down", classes="action-btn")
                        yield Button("Remove", id="btn-stage-remove", classes="action-btn")
                        yield Button("Clear", id="btn-stage-clear", classes="action-btn")

                    with Vertical(id="stage-editor", classes="hidden"):
                        with Horizontal(classes="studio-row"):
                            yield Static("Edit Stage", classes="studio-label")
                            yield Select[int](
                                [("1. Intro", 0)],
                                value=0,
                                allow_blank=False,
                                type_to_search=False,
                                compact=True,
                                id="stage-select",
                            )
                        with Horizontal(classes="studio-row"):
                            yield Static("Stage Type", classes="studio-label")
                            yield Select[StageKind](
                                [
                                    ("Introduction", StageKind.INTRO),
                                    ("Visual Effect", StageKind.EFFECT),
                                    ("Countdown", StageKind.COUNTDOWN),
                                    ("Recovery", StageKind.RECOVERY),
                                    ("Reveal", StageKind.REVEAL),
                                ],
                                value=StageKind.INTRO,
                                allow_blank=False,
                                type_to_search=False,
                                compact=True,
                                id="stage-kind",
                            )
                        with Horizontal(classes="studio-row", id="row-stage-effect"):
                            yield Static("Effect", classes="studio-label")
                            yield Select[TemplateId](
                                [(m.name, m.template_id) for m in templates],
                                value=seed.template_id,
                                allow_blank=False,
                                type_to_search=False,
                                compact=True,
                                id="stage-effect-template",
                            )
                        with Horizontal(classes="studio-row"):
                            yield Static("Stage Label", classes="studio-label")
                            yield Input(
                                value="Stage",
                                max_length=MAX_STAGE_LABEL_LENGTH,
                                id="stage-label",
                            )
                        with Horizontal(classes="studio-row"):
                            yield Static("Stage Secs", classes="studio-label")
                            yield Input(
                                value="5",
                                max_length=3,
                                classes="short-input",
                                id="stage-duration",
                            )
                            yield Button(
                                "Apply Stage",
                                id="btn-stage-apply",
                                classes="action-btn",
                            )
                        with Horizontal(classes="studio-row"):
                            yield Static("Caption", classes="studio-label")
                            yield Input(
                                value="",
                                max_length=MAX_STAGE_CAPTION_LENGTH,
                                id="stage-caption",
                            )

                # --- Live Preview Status Panel ---
                with Vertical(id="studio-preview-panel", classes="hidden"):
                    with Horizontal(classes="session-line"):
                        yield Static("Preview URL", classes="session-key")
                        yield Static("", classes="session-url", id="val-preview-url", markup=False)
                    with Horizontal(classes="session-line"):
                        yield Static("Status", classes="session-key")
                        yield Static("", classes="session-val", id="val-preview-state")

                yield Button("New Browser Studio link", id="btn-studio-web", classes="action-btn")
                yield Static("", classes="session-val", id="val-studio-url", markup=False)

                # --- Inline Preset Overwrite Confirmation ---
                with Horizontal(id="studio-overwrite-bar", classes="confirm-bar hidden"):
                    yield Static("", id="studio-overwrite-msg", classes="confirm-msg")
                    yield Button(
                        t("studio.btn_overwrite", lang),
                        id="btn-confirm-overwrite",
                        classes="action-btn",
                    )
                    yield Button(
                        t("studio.btn_cancel", lang),
                        id="btn-cancel-overwrite",
                        classes="action-btn",
                    )

                yield Static("", id="studio-feedback", classes="studio-feedback")

                with Horizontal(classes="studio-actions"):
                    yield Button(
                        t("prank.btn_preview", lang),
                        id="btn-studio-preview",
                        classes="action-btn",
                    )
                    yield Button(
                        t("studio.btn_restart", lang),
                        id="btn-studio-restart",
                        classes="action-btn hidden",
                    )
                    yield Button(
                        t("studio.btn_stop", lang),
                        id="btn-studio-stop",
                        classes="action-btn hidden",
                    )
                    yield Button(
                        t("settings.save", lang),
                        id="btn-studio-save",
                        classes="action-btn",
                    )
                    yield Button(
                        t("studio.btn_presets", lang),
                        id="btn-studio-presets",
                        classes="action-btn",
                    )
                    yield Button(
                        t("studio.btn_launch", lang),
                        id="btn-studio-start",
                        classes="action-btn",
                    )
                    yield Button(
                        t("action.back", lang),
                        id="btn-studio-back",
                        classes="action-btn",
                    )

        yield KeyHintBar(
            [
                ("Ctrl+P", t("keys.preview", lang)),
                ("Ctrl+S", t("keys.save", lang)),
                ("Ctrl+O", t("studio.btn_presets", lang)),
                ("Ctrl+R", t("keys.start", lang)),
                ("Esc", t("keys.back", lang)),
            ],
            id="studio-key-bar",
        )
        yield ResizeNotice(id="resize-notice")

    def on_mount(self) -> None:
        self.apply_viewport_size(self.app.size.width, self.app.size.height)
        seed = (
            self._initial_preset.config
            if self._initial_preset is not None
            else self._initial_config
        )
        if seed is not None:
            self.apply_studio_config(seed, preset_id=self._loaded_preset_id)
        else:
            self._current_template_id = self._selected_template_id()
            self._sync_template_group(self._current_template_id)
            self._sync_stage_ui()
            self._sync_header()
        self._refresh_timer = self.set_interval(0.5, self._sync_preview_panel)
        self.query_one("#studio-template", Select).focus()

    def on_unmount(self) -> None:
        if self._refresh_timer is not None:
            self._refresh_timer.stop()
            self._refresh_timer = None
        if self._preview_session_id is not None:
            self.poptrap_app.stop_studio_preview(self._preview_session_id)
            self._preview_session_id = None

    def on_screen_resume(self) -> None:
        self.apply_viewport_size(self.app.size.width, self.app.size.height)
        self._sync_preview_panel()

    def _selected_template_id(self) -> TemplateId:
        raw = self.query_one("#studio-template", Select).value
        return raw if isinstance(raw, TemplateId) else TemplateId.GLITCH

    def is_template_group_visible(self, template_id: TemplateId) -> bool:
        """Return True iff the customization section for ``template_id`` is visible."""
        group = self.query_one(f"#{_TEMPLATE_GROUP_IDS[template_id]}", Vertical)
        return not group.has_class("hidden")

    def _sync_template_group(self, template_id: TemplateId) -> None:
        meta = self.poptrap_app.prank_controller.get_template(template_id).metadata
        self.query_one("#studio-template-summary", Static).update(meta.summary)
        self.query_one("#studio-specific-heading", Static).update(
            f"TEMPLATE SETTINGS — {meta.name.upper()}"
        )
        for tid, gid in _TEMPLATE_GROUP_IDS.items():
            self.query_one(f"#{gid}", Vertical).set_class(tid is not template_id, "hidden")

    def _sync_header(self, *, preview_live: bool = False) -> None:
        app = self.poptrap_app
        lang = app.config.ui.default_language
        if preview_live:
            meta_txt = "PREVIEW LIVE"
            accent = True
        elif self._loaded_preset_id:
            meta_txt = f"PRESET: {self._loaded_preset_id}"
            accent = True
        else:
            meta_txt = t(
                "prank.meta_count",
                lang,
                count=len(app.prank_controller.list_templates()),
            )
            accent = False
        self.query_one("#studio-header", SectionHeader).set_content(
            t("studio.title", lang),
            meta_txt,
            accent_meta=accent,
        )

    def _set_feedback(self, message: str, *, is_error: bool = False) -> None:
        fb = self.query_one("#studio-feedback", Static)
        fb.set_class(is_error, "error")
        fb.update(message)

    def _hide_overwrite_bar(self) -> None:
        self._pending_overwrite = None
        self.query_one("#studio-overwrite-bar", Horizontal).add_class("hidden")

    def apply_studio_config(
        self,
        config: StudioConfig,
        *,
        preset_id: str | None = None,
    ) -> None:
        """Populate every Studio control from a validated ``StudioConfig``."""
        lang = self.poptrap_app.config.ui.default_language
        self._loaded_preset_id = preset_id
        self._hide_overwrite_bar()
        tpl_sel = self.query_one("#studio-template", Select)
        theme_sel = self.query_one("#studio-theme", Select)
        int_sel = self.query_one("#studio-intensity", Select)
        spd_sel = self.query_one("#studio-speed", Select)
        snd_sw = self.query_one("#studio-sound", Switch)
        with (
            tpl_sel.prevent(Select.Changed),
            theme_sel.prevent(Select.Changed),
            int_sel.prevent(Select.Changed),
            spd_sel.prevent(Select.Changed),
            snd_sw.prevent(Switch.Changed),
        ):
            tpl_sel.value = config.template_id
            self.query_one("#studio-name", Input).value = config.name
            self.query_one("#studio-title", Input).value = config.title
            self.query_one("#studio-message", Input).value = config.custom_message
            self.query_one("#studio-reveal", Input).value = config.reveal_message
            self.query_one("#studio-theme", Select).value = config.theme
            self.query_one("#studio-duration", Input).value = str(
                config.duration_seconds
            )
            self.query_one("#studio-intensity", Select).value = config.intensity
            self.query_one("#studio-speed", Select).value = config.animation_speed
            self.query_one("#studio-sound", Switch).value = config.sound_enabled
            self.query_one("#studio-sound-label", Static).update(
                t(
                    "settings.sound_on"
                    if config.sound_enabled
                    else "settings.sound_off",
                    lang,
                )
            )

            if config.glitch_settings is not None:
                self.query_one("#glitch-scanline", Input).value = (
                    f"{config.glitch_settings.scanline_opacity:.2f}"
                )
                self.query_one("#glitch-rgb", Input).value = str(
                    config.glitch_settings.rgb_offset_px
                )
                self.query_one("#glitch-recovery", Input).value = str(
                    config.glitch_settings.recovery_duration_seconds
                )
            if config.virus_alert_settings is not None:
                self.query_one("#virus-stages", Input).value = str(
                    config.virus_alert_settings.scan_stage_count
                )
                self.query_one("#virus-completion", Select).value = (
                    config.virus_alert_settings.completion_style
                )
            if config.system_crash_settings is not None:
                self.query_one("#crash-error-title", Input).value = (
                    config.system_crash_settings.error_title
                )
                self.query_one("#crash-transition", Input).value = str(
                    config.system_crash_settings.transition_ms
                )
                self.query_one("#crash-recovery", Input).value = str(
                    config.system_crash_settings.recovery_duration_seconds
                )
                self.query_one("#crash-recovery-msg", Input).value = (
                    config.system_crash_settings.recovery_message
                )
            if config.popup_storm_settings is not None:
                self.query_one("#storm-count", Input).value = str(
                    config.popup_storm_settings.popup_count
                )
                self.query_one("#storm-card-style", Select).value = (
                    config.popup_storm_settings.card_style
                )
                self.query_one("#storm-preset", Select).value = (
                    config.popup_storm_settings.message_preset
                )
                self.query_one("#storm-extra", Input).value = "; ".join(
                    config.popup_storm_settings.custom_messages
                )
                self.query_one("#storm-cleanup", Input).value = str(
                    config.popup_storm_settings.cleanup_duration_seconds
                )
            if config.countdown_settings is not None:
                self.query_one("#countdown-ring", Select).value = (
                    config.countdown_settings.ring_style
                )
                self.query_one("#countdown-completion", Input).value = (
                    config.countdown_settings.completion_message
                )

            self._stages = list(config.stages)
            self._selected_stage_idx = 0

        self._current_template_id = config.template_id
        self._sync_template_group(config.template_id)
        self._sync_stage_ui()
        self._sync_header()

    def load_preset_record(self, record: PresetRecord) -> None:
        """Load a validated ``PresetRecord`` into the Studio editor."""
        self.apply_studio_config(record.config, preset_id=record.preset_id)
        self._set_feedback(f"Loaded preset '{record.name}'.", is_error=False)

    def _sync_stage_ui(self) -> None:
        count = len(self._stages)
        dur_input = self.query_one("#studio-duration", Input)
        dur_input.disabled = count > 0

        self.query_one("#btn-stage-add", Button).disabled = count >= MAX_STUDIO_STAGES
        self.query_one("#btn-stage-clear", Button).disabled = count == 0
        self.query_one("#btn-stage-remove", Button).disabled = count == 0

        editor = self.query_one("#stage-editor", Vertical)
        summary = self.query_one("#stage-summary", Static)

        if count == 0:
            editor.add_class("hidden")
            self.query_one("#btn-stage-up", Button).disabled = True
            self.query_one("#btn-stage-down", Button).disabled = True
            summary.update(
                f"Single-stage mode (max {MAX_STUDIO_STAGES} stages, {MAX_COMPOSITION_DURATION_SECONDS}s total)."
            )
            return

        self._selected_stage_idx = max(0, min(self._selected_stage_idx, count - 1))
        total = sum(s.duration_seconds for s in self._stages)
        chain = " -> ".join(
            f"{i + 1}.{s.label}({s.duration_seconds}s)"
            for i, s in enumerate(self._stages)
        )
        summary.update(
            f"{count}/{MAX_STUDIO_STAGES} stages | {total}s/{MAX_COMPOSITION_DURATION_SECONDS}s: {chain}"
        )

        self.query_one("#btn-stage-up", Button).disabled = self._selected_stage_idx == 0
        self.query_one("#btn-stage-down", Button).disabled = (
            self._selected_stage_idx >= count - 1
        )

        editor.remove_class("hidden")
        picker = self.query_one("#stage-select", Select)
        kind_sel = self.query_one("#stage-kind", Select)
        eff_sel = self.query_one("#stage-effect-template", Select)
        with (
            picker.prevent(Select.Changed),
            kind_sel.prevent(Select.Changed),
            eff_sel.prevent(Select.Changed),
        ):
            picker.set_options(
                [
                    (
                        f"{i + 1}. {st.label} [{st.kind.value}] ({st.duration_seconds}s)",
                        i,
                    )
                    for i, st in enumerate(self._stages)
                ]
            )
            picker.value = self._selected_stage_idx
            current = self._stages[self._selected_stage_idx]
            kind_sel.value = current.kind
            self.query_one("#row-stage-effect", Horizontal).set_class(
                current.kind is not StageKind.EFFECT, "hidden"
            )
            if current.kind is StageKind.EFFECT:
                eff_sel.value = current.template_id or self._selected_template_id()
            self.query_one("#stage-label", Input).value = current.label
            self.query_one("#stage-duration", Input).value = str(
                current.duration_seconds
            )
            self.query_one("#stage-caption", Input).value = current.caption or ""

    def build_validated_studio_config(self) -> StudioConfig:
        """Read current Studio controls and return a centrally validated ``StudioConfig``."""
        tid = self._selected_template_id()
        duration = parse_positive_int_input(
            self.query_one("#studio-duration", Input).value,
            field_name="duration_seconds",
            label=f"Duration ({MIN_DURATION_SECONDS}-{MAX_COMPOSITION_DURATION_SECONDS})",
        )

        raw_theme = self.query_one("#studio-theme", Select).value
        theme = raw_theme if isinstance(raw_theme, PrankTheme) else PrankTheme.DARK

        raw_intensity = self.query_one("#studio-intensity", Select).value
        intensity = (
            raw_intensity
            if isinstance(raw_intensity, VisualIntensity)
            else VisualIntensity.MEDIUM
        )

        raw_speed = self.query_one("#studio-speed", Select).value
        speed = (
            raw_speed
            if isinstance(raw_speed, AnimationSpeed)
            else AnimationSpeed.NORMAL
        )

        payload: dict[str, Any] = {
            "name": self.query_one("#studio-name", Input).value,
            "template_id": tid.value,
            "title": self.query_one("#studio-title", Input).value,
            "custom_message": self.query_one("#studio-message", Input).value,
            "reveal_message": self.query_one("#studio-reveal", Input).value,
            "theme": theme.value,
            "duration_seconds": duration,
            "intensity": intensity.value,
            "animation_speed": speed.value,
            "sound_enabled": bool(self.query_one("#studio-sound", Switch).value),
            "auto_terminate": True,
            "stages": [st.model_dump(mode="json") for st in self._stages],
        }

        if tid is TemplateId.GLITCH:
            scan = _parse_float_input(
                self.query_one("#glitch-scanline", Input).value,
                field_name="scanline_opacity",
                label=f"Scanline visibility ({MIN_SCANLINE_OPACITY}-{MAX_SCANLINE_OPACITY})",
            )
            rgb = parse_positive_int_input(
                self.query_one("#glitch-rgb", Input).value,
                field_name="rgb_offset_px",
                label=f"RGB separation ({MIN_RGB_OFFSET_PX}-{MAX_RGB_OFFSET_PX})",
            )
            rec = parse_positive_int_input(
                self.query_one("#glitch-recovery", Input).value,
                field_name="recovery_duration_seconds",
                label=f"Recovery duration ({MIN_RECOVERY_SECONDS}-{MAX_RECOVERY_SECONDS})",
            )
            payload["glitch_settings"] = {
                "scanline_opacity": scan,
                "rgb_offset_px": rgb,
                "recovery_duration_seconds": rec,
            }

        elif tid is TemplateId.VIRUS_ALERT:
            scan_stages = parse_positive_int_input(
                self.query_one("#virus-stages", Input).value,
                field_name="scan_stage_count",
                label=f"Scan stage count ({MIN_SCAN_STAGES}-{MAX_SCAN_STAGES})",
            )
            raw_comp = self.query_one("#virus-completion", Select).value
            comp = (
                raw_comp
                if isinstance(raw_comp, CompletionStyle)
                else CompletionStyle.CELEBRATION
            )
            payload["virus_alert_settings"] = {
                "scan_stage_count": scan_stages,
                "completion_style": comp.value,
            }

        elif tid is TemplateId.SYSTEM_CRASH:
            err_title = self.query_one("#crash-error-title", Input).value
            trans = parse_positive_int_input(
                self.query_one("#crash-transition", Input).value,
                field_name="transition_ms",
                label=f"Transition duration ({MIN_CRASH_TRANSITION_MS}-{MAX_CRASH_TRANSITION_MS}ms)",
            )
            c_rec = parse_positive_int_input(
                self.query_one("#crash-recovery", Input).value,
                field_name="recovery_duration_seconds",
                label=f"Recovery duration ({MIN_RECOVERY_SECONDS}-{MAX_RECOVERY_SECONDS}s)",
            )
            rec_msg = self.query_one("#crash-recovery-msg", Input).value
            payload["title"] = err_title
            payload["system_crash_settings"] = {
                "error_title": err_title,
                "transition_ms": trans,
                "recovery_duration_seconds": c_rec,
                "recovery_message": rec_msg,
            }

        elif tid is TemplateId.POPUP_STORM:
            cards = parse_positive_int_input(
                self.query_one("#storm-count", Input).value,
                field_name="popup_count",
                label=f"Card count ({MIN_POPUP_COUNT}-{MAX_POPUP_COUNT})",
            )
            raw_style = self.query_one("#storm-card-style", Select).value
            style = (
                raw_style
                if isinstance(raw_style, CardPresentationStyle)
                else CardPresentationStyle.CASCADE
            )
            raw_preset = self.query_one("#storm-preset", Select).value
            preset = (
                raw_preset
                if isinstance(raw_preset, PopupMessagePreset)
                else PopupMessagePreset.CLASSIC
            )
            extra = parse_semicolon_messages(
                self.query_one("#storm-extra", Input).value
            )
            cleanup = parse_positive_int_input(
                self.query_one("#storm-cleanup", Input).value,
                field_name="cleanup_duration_seconds",
                label=f"Auto-cleanup duration ({MIN_CLEANUP_SECONDS}-{MAX_CLEANUP_SECONDS}s)",
            )
            payload["popup_storm_settings"] = {
                "popup_count": cards,
                "card_style": style.value,
                "message_preset": preset.value,
                "custom_messages": list(extra),
                "cleanup_duration_seconds": cleanup,
            }

        elif tid is TemplateId.COUNTDOWN:
            raw_ring = self.query_one("#countdown-ring", Select).value
            ring = (
                raw_ring
                if isinstance(raw_ring, RingPresentationStyle)
                else RingPresentationStyle.TICKS
            )
            done_msg = self.query_one("#countdown-completion", Input).value
            payload["countdown_settings"] = {
                "ring_style": ring.value,
                "completion_message": done_msg,
            }

        return self.poptrap_app.prank_controller.validate_studio(payload)

    def _sync_preview_panel(self) -> None:
        app = self.poptrap_app
        panel = self.query_one("#studio-preview-panel", Vertical)
        restart_btn = self.query_one("#btn-studio-restart", Button)
        stop_btn = self.query_one("#btn-studio-stop", Button)

        pid = self._preview_session_id
        base_url = app.experience_server.base_url
        if pid is None or base_url is None:
            panel.add_class("hidden")
            restart_btn.add_class("hidden")
            stop_btn.add_class("hidden")
            self._sync_header(preview_live=False)
            return

        try:
            snap = app.prank_controller.get_session(pid)
        except SessionNotFoundError:
            self._preview_session_id = None
            panel.add_class("hidden")
            restart_btn.add_class("hidden")
            stop_btn.add_class("hidden")
            self._sync_header(preview_live=False)
            return

        live = not is_terminal_state(snap.state)
        panel.remove_class("hidden")
        restart_btn.remove_class("hidden")
        stop_btn.set_class(not live, "hidden")
        self._sync_header(preview_live=live)

        self.query_one("#val-preview-url", Static).update(f"{base_url}/s/{snap.session_id}")
        state_txt = f"LOCAL PREVIEW · {snap.state.value.upper()} ({snap.config.duration_seconds}s)"
        if snap.state is SessionState.READY:
            state_txt += " · awaiting consent"
        elif snap.state is SessionState.ACTIVE and snap.remaining_seconds is not None:
            state_txt += f" · {int(round(snap.remaining_seconds))}s left"
        self.query_one("#val-preview-state", Static).update(state_txt)

    # ------------------------------------------------------------------
    # Event handlers
    # ------------------------------------------------------------------

    def on_select_changed(self, event: Select.Changed) -> None:
        sid = event.select.id
        if sid == "studio-template" and isinstance(event.value, TemplateId):
            if event.value is not self._current_template_id:
                self._reset_to_template(event.value, keep_preset_id=True)
                self._set_feedback("")
        elif sid == "stage-select" and isinstance(event.value, int):
            if event.value != self._selected_stage_idx:
                self._selected_stage_idx = event.value
                self._sync_stage_ui()
        elif sid == "stage-kind" and isinstance(event.value, StageKind):
            self.query_one("#row-stage-effect", Horizontal).set_class(
                event.value is not StageKind.EFFECT, "hidden"
            )

    def on_switch_changed(self, event: Switch.Changed) -> None:
        if event.switch.id == "studio-sound":
            lang = self.poptrap_app.config.ui.default_language
            self.query_one("#studio-sound-label", Static).update(
                t(
                    "settings.sound_on" if event.value else "settings.sound_off",
                    lang,
                )
            )

    def on_button_pressed(self, event: Button.Pressed) -> None:
        event.stop()
        bid = event.button.id
        if bid == "btn-studio-defaults":
            self._reset_to_template(self._selected_template_id(), keep_preset_id=True)
            self._set_feedback("Template defaults restored.", is_error=False)
        elif bid == "btn-stage-starter":
            self.action_load_starter_stages()
        elif bid == "btn-stage-add":
            self.action_add_stage()
        elif bid == "btn-stage-apply":
            self.action_apply_stage_edit()
        elif bid == "btn-stage-up":
            self.action_move_stage(-1)
        elif bid == "btn-stage-down":
            self.action_move_stage(+1)
        elif bid == "btn-stage-remove":
            self.action_remove_stage()
        elif bid == "btn-stage-clear":
            self.action_clear_stages()
        elif bid == "btn-studio-web":
            try:
                self._show_editor_link(self.poptrap_app.issue_studio_url())
            except PopTrapError as exc:
                self._set_feedback(exc.message, is_error=True)
        elif bid == "btn-studio-preview":
            self.action_start_preview()
        elif bid == "btn-studio-restart":
            self.action_restart_preview()
        elif bid == "btn-studio-stop":
            self.action_stop_preview()
        elif bid == "btn-studio-save":
            self.action_save_preset()
        elif bid == "btn-confirm-overwrite":
            self.action_confirm_overwrite()
        elif bid == "btn-cancel-overwrite":
            self._hide_overwrite_bar()
            self._set_feedback("Save cancelled.", is_error=False)
        elif bid == "btn-studio-presets":
            self.action_open_presets()
        elif bid == "btn-studio-start":
            self.action_launch_session()
        elif bid == "btn-studio-back":
            self.action_go_back()

    def _reset_to_template(
        self, template_id: TemplateId, *, keep_preset_id: bool = False
    ) -> None:
        raw_theme = self.query_one("#studio-theme", Select).value
        theme = raw_theme if isinstance(raw_theme, PrankTheme) else PrankTheme.DARK
        sound = bool(self.query_one("#studio-sound", Switch).value)
        fresh = build_default_studio_config(
            template_id,
            registry=self.poptrap_app.prank_controller.registry,
            theme=theme,
            sound_enabled=sound,
        )
        self.apply_studio_config(
            fresh,
            preset_id=self._loaded_preset_id if keep_preset_id else None,
        )

    # ------------------------------------------------------------------
    # Multi-Stage Composer actions
    # ------------------------------------------------------------------

    def action_load_starter_stages(self) -> None:
        try:
            saved = list(self._stages)
            self._stages = []
            try:
                base = self.build_validated_studio_config()
            except PopTrapError:
                self._stages = saved
                raise
            starter = build_default_stages(
                base, registry=self.poptrap_app.prank_controller.registry
            )
            validated = self.poptrap_app.prank_controller.validate_studio(
                {
                    **base.model_dump(mode="json"),
                    "stages": [s.model_dump(mode="json") for s in starter],
                }
            )
        except PopTrapError as exc:
            self._set_feedback(exc.message, is_error=True)
            return

        self._stages = list(validated.stages)
        self._selected_stage_idx = 0
        self._sync_stage_ui()
        self._set_feedback(
            f"Loaded {len(self._stages)}-stage sequence ({validated.effective_duration_seconds}s).",
            is_error=False,
        )

    def action_add_stage(self) -> None:
        try:
            current = self.build_validated_studio_config()
            default_kind = StageKind.EFFECT if not self._stages else StageKind.REVEAL
            default_dur = 8 if not self._stages else 4
            updated = add_stage(
                current,
                {
                    "kind": default_kind.value,
                    "label": (
                        "Main Effect"
                        if not self._stages
                        else f"Stage {len(self._stages) + 1}"
                    ),
                    "duration_seconds": default_dur,
                    "template_id": (
                        current.template_id.value
                        if default_kind is StageKind.EFFECT
                        else None
                    ),
                },
                registry=self.poptrap_app.prank_controller.registry,
            )
        except PopTrapError as exc:
            self._set_feedback(exc.message, is_error=True)
            return

        self._stages = list(updated.stages)
        self._selected_stage_idx = len(self._stages) - 1
        self._sync_stage_ui()
        self._set_feedback(
            f"Added stage ({len(self._stages)}/{MAX_STUDIO_STAGES}, {updated.effective_duration_seconds}s total).",
            is_error=False,
        )

    def action_apply_stage_edit(self) -> None:
        if not self._stages:
            return
        try:
            current = self.build_validated_studio_config()
            stage_dur = parse_positive_int_input(
                self.query_one("#stage-duration", Input).value,
                field_name="duration_seconds",
                label=f"Stage duration ({MIN_STAGE_DURATION_SECONDS}-{MAX_STAGE_DURATION_SECONDS}s)",
            )
            raw_kind = self.query_one("#stage-kind", Select).value
            kind = raw_kind if isinstance(raw_kind, StageKind) else StageKind.INTRO
            raw_eff = self.query_one("#stage-effect-template", Select).value
            eff_tid = (
                raw_eff.value
                if kind is StageKind.EFFECT and isinstance(raw_eff, TemplateId)
                else None
            )
            caption_raw = self.query_one("#stage-caption", Input).value
            updated = update_stage(
                current,
                self._selected_stage_idx,
                {
                    "kind": kind.value,
                    "label": self.query_one("#stage-label", Input).value,
                    "duration_seconds": stage_dur,
                    "template_id": eff_tid,
                    "caption": caption_raw.strip() or None,
                },
                registry=self.poptrap_app.prank_controller.registry,
            )
        except PopTrapError as exc:
            self._set_feedback(exc.message, is_error=True)
            return

        self._stages = list(updated.stages)
        self._sync_stage_ui()
        self._set_feedback(
            f"Updated stage {self._selected_stage_idx + 1} ({updated.effective_duration_seconds}s total).",
            is_error=False,
        )

    def action_move_stage(self, delta: int) -> None:
        if not self._stages:
            return
        target = self._selected_stage_idx + delta
        if target < 0 or target >= len(self._stages):
            return
        try:
            current = self.build_validated_studio_config()
            updated = move_stage(
                current,
                self._selected_stage_idx,
                target,
                registry=self.poptrap_app.prank_controller.registry,
            )
        except PopTrapError as exc:
            self._set_feedback(exc.message, is_error=True)
            return

        self._stages = list(updated.stages)
        self._selected_stage_idx = target
        self._sync_stage_ui()
        self._set_feedback(
            f"Moved stage to position {target + 1}.", is_error=False
        )

    def action_remove_stage(self) -> None:
        if not self._stages:
            return
        try:
            if len(self._stages) == 1:
                self._stages = []
                self._selected_stage_idx = 0
                self._sync_stage_ui()
                self._set_feedback("Returned to single-stage mode.", is_error=False)
                return
            current = self.build_validated_studio_config()
            updated = remove_stage(
                current,
                self._selected_stage_idx,
                registry=self.poptrap_app.prank_controller.registry,
            )
        except PopTrapError as exc:
            self._set_feedback(exc.message, is_error=True)
            return

        self._stages = list(updated.stages)
        self._selected_stage_idx = max(0, min(self._selected_stage_idx, len(self._stages) - 1))
        self._sync_stage_ui()
        self._set_feedback(
            f"Removed stage ({len(self._stages)} left, {updated.effective_duration_seconds}s total).",
            is_error=False,
        )

    def action_clear_stages(self) -> None:
        self._stages = []
        self._selected_stage_idx = 0
        self._sync_stage_ui()
        self._set_feedback("Single-stage mode active.", is_error=False)

    # ------------------------------------------------------------------
    # Live Local Preview, Preset Saving, & Session Launch
    # ------------------------------------------------------------------

    def _show_editor_link(self, url: str) -> None:
        self.query_one("#val-studio-url", Static).update(
            "Browser Studio (private, one use, 90s): " + url
        )

    def action_start_preview(self) -> None:
        try:
            config = self.build_validated_studio_config()
            timeline = build_studio_timeline(
                config, registry=self.poptrap_app.prank_controller.registry
            )
            snap, preview_url, editor_url = self.poptrap_app.launch_studio_preview(config)
        except PopTrapError as exc:
            self._set_feedback(exc.message, is_error=True)
            return

        self._preview_session_id = snap.session_id
        self._show_editor_link(editor_url)
        self._sync_preview_panel()
        self._set_feedback(
            f"Local preview active ({len(timeline)} step(s), {config.effective_duration_seconds}s): {preview_url}",
            is_error=False,
        )

    def action_restart_preview(self) -> None:
        if self._preview_session_id is None:
            self.action_start_preview()
            return
        try:
            config = self.build_validated_studio_config()
            snap, preview_url, editor_url = self.poptrap_app.restart_studio_preview(
                self._preview_session_id, config
            )
        except PopTrapError as exc:
            self._set_feedback(exc.message, is_error=True)
            return

        self._preview_session_id = snap.session_id
        self._show_editor_link(editor_url)
        self._sync_preview_panel()
        self._set_feedback(f"Local preview restarted: {preview_url}", is_error=False)

    def action_stop_preview(self) -> None:
        if self._preview_session_id is None:
            return
        self.poptrap_app.stop_studio_preview(self._preview_session_id)
        self._sync_preview_panel()
        self._set_feedback("Local preview stopped — returned to editor.", is_error=False)

    def action_save_preset(self) -> None:
        self._hide_overwrite_bar()
        try:
            config = self.build_validated_studio_config()
        except PopTrapError as exc:
            self._set_feedback(exc.message, is_error=True)
            return

        try:
            saved = self.poptrap_app.prank_controller.presets.save_preset(
                config,
                preset_id=self._loaded_preset_id,
                overwrite=False,
            )
        except PresetExistsError as exc:
            self._pending_overwrite = (config, exc.existing_id)
            self.query_one("#studio-overwrite-msg", Static).update(
                f"Overwrite '{config.name}'?"
            )
            self.query_one("#studio-overwrite-bar", Horizontal).remove_class("hidden")
            self._set_feedback(exc.message, is_error=False)
            return
        except PopTrapError as exc:
            self._set_feedback(exc.message, is_error=True)
            return

        self._loaded_preset_id = saved.preset_id
        self._sync_header()
        self.poptrap_app.emit_feedback_sound()
        self._set_feedback(
            f"Saved local preset '{saved.name}' ({saved.preset_id}).",
            is_error=False,
        )

    def action_confirm_overwrite(self) -> None:
        pending = self._pending_overwrite
        if pending is None:
            return
        config, target_id = pending
        self._hide_overwrite_bar()
        try:
            saved = self.poptrap_app.prank_controller.presets.save_preset(
                config,
                preset_id=target_id,
                overwrite=True,
            )
        except PopTrapError as exc:
            self._set_feedback(exc.message, is_error=True)
            return

        self._loaded_preset_id = saved.preset_id
        self._sync_header()
        self.poptrap_app.emit_feedback_sound()
        self._set_feedback(f"Updated preset '{saved.name}'.", is_error=False)

    def action_open_presets(self) -> None:
        self.poptrap_app.emit_feedback_sound()
        self.app.push_screen(
            PresetManagerScreen(
                on_preset_loaded=self.load_preset_record,
                on_session_launched=lambda url: self._set_feedback(
                    f"Local session live: {url}", is_error=False
                ),
            )
        )

    def action_launch_session(self) -> None:
        try:
            config = self.build_validated_studio_config()
            if self._preview_session_id is not None:
                self.poptrap_app.stop_studio_preview(self._preview_session_id)
                self._preview_session_id = None
            _, url = self.poptrap_app.launch_studio_session(config)
        except PopTrapError as exc:
            self._set_feedback(exc.message, is_error=True)
            return

        self._sync_preview_panel()
        self._set_feedback(f"Local session live: {url}", is_error=False)

    def action_go_back(self) -> None:
        if self._preview_session_id is not None:
            self.poptrap_app.stop_studio_preview(self._preview_session_id)
            self._preview_session_id = None
        self.poptrap_app.close_active_screen()
