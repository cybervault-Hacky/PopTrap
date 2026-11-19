"""Terminal-issued, one-use entry to the browser Control Center."""

from __future__ import annotations

import time

from rich.text import Text
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Container, VerticalScroll
from textual.widgets import Button, Static

from poptrap.core.exceptions import RuntimeServerError
from poptrap.engine.control.server import UNLOCK_SECONDS
from poptrap.ui.screens.base import BasePopTrapScreen
from poptrap.ui.widgets import KeyHintBar, ResizeNotice, SectionHeader


class ControlCenterScreen(BasePopTrapScreen):
    BINDINGS = [Binding("escape", "back", "Back", show=False)]

    def __init__(self) -> None:
        super().__init__()
        self._url: str | None = None
        self._deadline = 0.0

    def compose(self) -> ComposeResult:
        with Container(classes="screen-shell"):
            with VerticalScroll(classes="content-frame", id="creator-launcher"):
                yield SectionHeader("Control Center", "Creator only", unicode_enabled=self.poptrap_app.terminal_capabilities.supports_unicode)
                yield Static("Open on this computer only. The access link works once and expires after 90 seconds. Do not share it.")
                yield Static("", id="creator-url", markup=False)
                yield Button("Open browser", id="creator-open", classes="action-btn")
                yield Button("Copy access link", id="creator-copy", classes="action-btn")
                yield Button("New access link", id="creator-new", classes="action-btn")
                yield Button("Back", id="creator-back", classes="action-btn")
                yield Static("", id="creator-feedback", markup=False)
        yield KeyHintBar([("Tab", "Controls"), ("Enter", "Select"), ("Esc", "Back")])
        yield ResizeNotice()

    def on_mount(self) -> None:
        self.apply_viewport_size(self.app.size.width, self.app.size.height)
        self._renew()
        self.set_interval(1, self._expire)
        self.query_one("#creator-open" if self._url else "#creator-new", Button).focus()

    def _display_link(self) -> None:
        self.query_one("#creator-url", Static).update(Text(self._url or "No access link", overflow="fold"))
        for key in ("open", "copy"):
            self.query_one(f"#creator-{key}", Button).disabled = self._url is None

    def _renew(self) -> None:
        try:
            self._url = self.poptrap_app.issue_control_url()
        except RuntimeServerError as exc:
            self._url = None
            self.query_one("#creator-feedback", Static).update(exc.message)
        else:
            self._deadline = time.monotonic() + UNLOCK_SECONDS
            self.query_one("#creator-feedback", Static).update("Issuing this link locked any previous dashboard tab.")
        self._display_link()

    def _expire(self) -> None:
        if self._url is not None and time.monotonic() >= self._deadline:
            self._url = None
            self._display_link()
            self.query_one("#creator-feedback", Static).update("Access link expired. Choose New access link if needed.")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        event.stop()
        key = event.button.id
        if key == "creator-back":
            self.action_back()
        elif key == "creator-new":
            self.action_new_access_link()
        else:
            self._expire()
            if self._url is not None:
                if key == "creator-copy":
                    self.app.copy_to_clipboard(self._url)
                    message = "Clipboard requested. If unsupported, copy the visible link manually."
                else:
                    self.app.open_url(self._url)
                    message = "Browser open requested. If it did not open, copy the access link."
                self.query_one("#creator-feedback", Static).update(message)

    def action_new_access_link(self) -> None:
        self._renew()

    def action_back(self) -> None:
        self._url = None
        self.app.pop_screen()
