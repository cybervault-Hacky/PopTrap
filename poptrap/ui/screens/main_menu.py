"""Primary POPTRAP interactive main menu screen."""

from __future__ import annotations

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Container, Vertical
from textual.widgets import Static

from poptrap import __version__
from poptrap.core.i18n import t
from poptrap.core.modules import MAIN_MENU_MODULES
from poptrap.ui.screens.base import BasePopTrapScreen
from poptrap.ui.widgets import BrandHeader, KeyHintBar, MainMenuList, ResizeNotice


class MainMenuScreen(BasePopTrapScreen):
    """Main interactive screen presenting POPTRAP branding and six menu items."""

    BINDINGS = [
        Binding("1", "select_number(1)", "1", show=False),
        Binding("2", "select_number(2)", "2", show=False),
        Binding("3", "select_number(3)", "3", show=False),
        Binding("4", "select_number(4)", "4", show=False),
        Binding("5", "select_number(5)", "5", show=False),
        Binding("6", "select_number(6)", "6", show=False),
        Binding("q", "exit_app", "Exit", show=False),
    ]

    def compose(self) -> ComposeResult:
        app = self.poptrap_app
        cfg = app.config
        unicode_ok = app.terminal_capabilities.supports_unicode

        with Container(classes="screen-shell"):
            with Vertical(classes="content-frame menu-frame"):
                yield BrandHeader(
                    cfg.app.name,
                    __version__,
                    unicode_enabled=unicode_ok,
                    id="brand-header",
                )
                yield MainMenuList(
                    modules=MAIN_MENU_MODULES,
                    language=cfg.ui.default_language,
                    id="main-menu-list",
                )
                yield Static(
                    "",
                    id="startup-notice",
                    classes="hidden",
                )

        yield KeyHintBar(
            self._build_key_hints(),
            context_text=self._platform_context(),
            id="main-key-bar",
        )
        yield ResizeNotice(id="resize-notice")

    def on_mount(self) -> None:
        self._sync_startup_notice()
        self.apply_viewport_size(self.app.size.width, self.app.size.height)
        self.query_one(MainMenuList).focus()

    def on_screen_resume(self) -> None:
        self.refresh_from_config()
        self.apply_viewport_size(self.app.size.width, self.app.size.height)
        self.query_one(MainMenuList).focus()

    def _platform_context(self) -> str:
        app = self.poptrap_app
        summary = app.platform_info.summary
        if not app.terminal_capabilities.supports_unicode:
            return summary.replace("·", "/")
        return summary

    def _build_key_hints(self) -> list[tuple[str, str]]:
        app = self.poptrap_app
        lang = app.config.ui.default_language
        arrows = "↑/↓" if app.terminal_capabilities.supports_unicode else "Up/Down"
        return [
            (arrows, t("keys.navigate", lang)),
            ("Enter", t("keys.select", lang)),
            ("1-6", t("keys.jump", lang)),
            ("Ctrl+D", "Control Center"),
            ("Q", t("keys.exit", lang)),
        ]

    def _sync_startup_notice(self) -> None:
        app = self.poptrap_app
        notice = self.query_one("#startup-notice", Static)
        warning_count = len(app.config_warnings)
        if warning_count > 0:
            notice.update(
                t(
                    "notice.config_fallback",
                    app.config.ui.default_language,
                    count=warning_count,
                )
            )
            notice.remove_class("hidden")
        else:
            notice.update("")
            notice.add_class("hidden")

    def on_compact_changed(self, compact: bool) -> None:
        header = self.query_one(BrandHeader)
        header.set_compact(compact)

    def refresh_from_config(self) -> None:
        app = self.poptrap_app
        cfg = app.config
        unicode_ok = app.terminal_capabilities.supports_unicode

        self.query_one(BrandHeader).update_branding(
            cfg.app.name,
            __version__,
            unicode_enabled=unicode_ok,
        )
        self.query_one(MainMenuList).update_language(cfg.ui.default_language)
        self.query_one(KeyHintBar).set_hints(
            self._build_key_hints(),
            context_text=self._platform_context(),
        )
        self._sync_startup_notice()

    def on_main_menu_list_module_selected(
        self,
        event: MainMenuList.ModuleSelected,
    ) -> None:
        event.stop()
        self.poptrap_app.open_module(event.module.module_id)

    def action_select_number(self, index: int) -> None:
        menu = self.query_one(MainMenuList)
        menu.activate_index(index)

    def action_exit_app(self) -> None:
        self.poptrap_app.action_request_exit()
