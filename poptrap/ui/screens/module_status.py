"""Screen shown for future-phase modules not yet implemented in Phase 1."""

from __future__ import annotations

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Container, Horizontal, Vertical
from textual.widgets import Button, Static

from poptrap.core.i18n import t
from poptrap.core.modules import MenuModuleSpec
from poptrap.ui.screens.base import BasePopTrapScreen
from poptrap.ui.widgets import KeyHintBar, ResizeNotice, SectionHeader


class ModuleStatusScreen(BasePopTrapScreen):
    """Honest, minimal status view for modules outside Phase 1 scope."""

    BINDINGS = [
        Binding("escape", "go_back", "Back", show=False),
        Binding("backspace", "go_back", "Back", show=False),
        Binding("b", "go_back", "Back", show=False),
        Binding("q", "exit_app", "Exit", show=False),
    ]

    def __init__(self, module: MenuModuleSpec) -> None:
        super().__init__()
        self._module = module

    @property
    def module(self) -> MenuModuleSpec:
        """Return the ``MenuModuleSpec`` presented on this screen."""
        return self._module

    def compose(self) -> ComposeResult:
        app = self.poptrap_app
        lang = app.config.ui.default_language
        unicode_ok = app.terminal_capabilities.supports_unicode
        title = t(self._module.label_key, lang)
        badge = t("module.badge_unavailable", lang)
        detail = (
            t(self._module.detail_key, lang)
            if self._module.detail_key
            else t("module.status_value", lang)
        )

        with Container(classes="screen-shell"):
            with Vertical(classes="content-frame module-frame"):
                yield SectionHeader(
                    title,
                    badge,
                    unicode_enabled=unicode_ok,
                    accent_meta=True,
                    id="module-header",
                )
                with Horizontal(classes="status-row"):
                    yield Static(t("module.status_label", lang), classes="status-key", id="lbl-status")
                    yield Static(
                        t("module.status_value", lang),
                        classes="status-value-accent",
                        id="module-status-text",
                    )
                with Horizontal(classes="status-row"):
                    yield Static(t("module.detail_label", lang), classes="status-key", id="lbl-detail")
                    yield Static(
                        detail,
                        classes="status-value",
                        id="module-detail-text",
                    )
                with Horizontal(classes="module-actions"):
                    yield Button(
                        t("action.back", lang),
                        id="btn-back",
                        classes="action-btn",
                    )

        yield KeyHintBar(
            [
                ("Esc", t("keys.back", lang)),
                ("Q", t("keys.exit", lang)),
            ],
            id="module-key-bar",
        )
        yield ResizeNotice(id="resize-notice")

    def on_mount(self) -> None:
        self.apply_viewport_size(self.app.size.width, self.app.size.height)
        self.query_one("#btn-back", Button).focus()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        event.stop()
        if event.button.id == "btn-back":
            self.action_go_back()

    def action_go_back(self) -> None:
        self.poptrap_app.close_active_screen()

    def action_exit_app(self) -> None:
        self.poptrap_app.action_request_exit()
