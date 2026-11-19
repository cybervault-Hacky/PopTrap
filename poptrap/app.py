"""Main POPTRAP Textual application controller."""

from __future__ import annotations

import threading
from pathlib import Path
from typing import ClassVar

from textual.app import App
from textual.binding import Binding, BindingType
from textual.message import Message

from poptrap import __version__
from poptrap.core.config import (
    AppConfig,
    ConfigLoadResult,
    LanguageCode,
    LogLevel,
    ThemeName,
    load_config,
    load_default_config,
    save_config,
)
from poptrap.core.exceptions import ConfigurationError, RuntimeServerError, SessionNotFoundError, SessionStateError
from poptrap.core.logging_setup import get_logger, set_log_level
from poptrap.core.modules import ModuleAvailability, ModuleId, get_module_spec
from poptrap.engine.activity import ActivityLog
from poptrap.engine.bluetooth import BluetoothEngine
from poptrap.engine.control.server import ControlServer
from poptrap.engine.control.service import ControlCenter, Preferences
from poptrap.engine.controller import PrankController
from poptrap.engine.delivery.server import DeliveryServer
from poptrap.engine.lifecycle import is_terminal_state
from poptrap.engine.models import PrankConfig, SessionSnapshot
from poptrap.engine.runtime.server import ExperienceServer
from poptrap.engine.studio import StudioConfig
from poptrap.ui.screens import (
    BasePopTrapScreen,
    BluetoothScreen,
    CreatePrankScreen,
    LocalNetworkScreen,
    MainMenuScreen,
    ModuleStatusScreen,
    SettingsScreen,
    ShareLinkScreen,
)
from poptrap.ui.screens.control import ControlCenterScreen
from poptrap.ui.themes import POPTRAP_THEMES
from poptrap.utils.paths import get_default_config_path, get_user_config_path
from poptrap.utils.platform import (
    PlatformInfo,
    TerminalCapabilities,
    detect_platform,
    detect_terminal_capabilities,
)


class ControlPreferencesChanged(Message):
    """A dashboard worker persisted validated preferences."""


class PopTrapApp(App[int]):
    """Interactive terminal UI application for POPTRAP."""

    ENABLE_COMMAND_PALETTE: ClassVar[bool] = False

    CSS_PATH: ClassVar[list[str]] = [
        "ui/theme.tcss",
        "ui/styles/main_menu.tcss",
        "ui/styles/module_status.tcss",
        "ui/styles/settings.tcss",
        "ui/styles/create_prank.tcss",
        "ui/styles/prank_studio.tcss",
        "ui/styles/bluetooth.tcss",
        "ui/styles/delivery.tcss",
        "ui/styles/control.tcss",
    ]

    BINDINGS: ClassVar[list[BindingType]] = [
        Binding("ctrl+c", "request_exit", "Exit", show=False, priority=True),
        Binding("ctrl+q", "request_exit", "Exit", show=False),
        Binding("ctrl+d", "open_control_center", "Control Center", show=False),
    ]

    def __init__(
        self,
        *,
        config_result: ConfigLoadResult | None = None,
        config: AppConfig | None = None,
        config_path: Path | None = None,
        default_config_path: Path | None = None,
        config_warnings: tuple[str, ...] = (),
        platform_info: PlatformInfo | None = None,
        terminal_capabilities: TerminalCapabilities | None = None,
        prank_controller: PrankController | None = None,
        experience_server: ExperienceServer | None = None,
        bluetooth_engine: BluetoothEngine | None = None,
    ) -> None:
        super().__init__()
        self._app_logger = get_logger("app")
        self._config_lock = threading.RLock()

        if config_result is not None:
            self._config = config_result.config
            self._config_path = config_result.config_path
            self._default_config_path = config_result.default_path
            self._config_warnings = config_result.warnings
        elif config is not None:
            self._config = config
            self._config_path = (
                get_user_config_path() if config_path is None else config_path
            )
            self._default_config_path = (
                get_default_config_path()
                if default_config_path is None
                else default_config_path
            )
            self._config_warnings = tuple(config_warnings)
        else:
            loaded = load_config(config_path, default_path=default_config_path)
            self._config = loaded.config
            self._config_path = loaded.config_path
            self._default_config_path = loaded.default_path
            self._config_warnings = loaded.warnings

        self._platform_info = (
            detect_platform() if platform_info is None else platform_info
        )
        self._terminal_capabilities = (
            detect_terminal_capabilities(self._platform_info)
            if terminal_capabilities is None
            else terminal_capabilities
        )

        self._owns_controller = prank_controller is None
        self._prank_controller = (
            PrankController() if prank_controller is None else prank_controller
        )
        self._experience_server = (
            ExperienceServer(self._prank_controller)
            if experience_server is None
            else experience_server
        )
        activity = ActivityLog()
        self._delivery_server = DeliveryServer(registry=self._prank_controller.registry, activity=activity)
        self._owns_bluetooth = bluetooth_engine is None
        self._bluetooth_engine = (
            BluetoothEngine(
                prank_controller=self._prank_controller,
                platform_info=self._platform_info,
            )
            if bluetooth_engine is None
            else bluetooth_engine
        )
        self._active_session_id: str | None = None
        self._runtime_cleaned = False
        self._control_center = ControlCenter(
            self._prank_controller, self._experience_server, self._delivery_server, self._bluetooth_engine, activity,
            read_config=lambda: self.config, save_preferences=self._save_control_preferences,
            config_path=self._config_path, default_path=self._default_config_path,
        )
        self._control_server = ControlServer(self._control_center)

        # Disable decorative transitions to keep navigation crisp and immediate.
        self.animation_level = "none"

        for custom_theme in POPTRAP_THEMES.values():
            self.register_theme(custom_theme)

        self.title = self._config.app.name
        self.sub_title = f"v{__version__}"
        self.theme = self._config.ui.theme.value
        self._sound_events: int = 0

    @property
    def config(self) -> AppConfig:
        """Currently active validated application configuration."""
        return self._config

    @property
    def config_path(self) -> Path:
        """Filesystem path used when saving user configuration."""
        return self._config_path

    @property
    def default_config_path(self) -> Path:
        """Filesystem path to the bundled ``default.toml`` configuration."""
        return self._default_config_path

    @property
    def config_warnings(self) -> tuple[str, ...]:
        """Active configuration fallback warnings recorded during startup."""
        return self._config_warnings

    @property
    def platform_info(self) -> PlatformInfo:
        """Detected host operating system and Python metadata."""
        return self._platform_info

    @property
    def terminal_capabilities(self) -> TerminalCapabilities:
        """Detected terminal capabilities snapshot."""
        return self._terminal_capabilities

    @property
    def sound_events(self) -> int:
        """Count of audible terminal bells emitted during this session."""
        return self._sound_events

    @property
    def prank_controller(self) -> PrankController:
        """Prank engine controller bound to this application instance."""
        return self._prank_controller

    @property
    def experience_server(self) -> ExperienceServer:
        """Local FastAPI browser experience server manager."""
        return self._experience_server

    @property
    def delivery_server(self) -> DeliveryServer:
        """Isolated Phase 6 recipient listener; idle until explicitly started."""
        return self._delivery_server

    @property
    def bluetooth_engine(self) -> BluetoothEngine:
        """Phase 5 Bluetooth capability, discovery, pairing, and invitation engine."""
        return self._bluetooth_engine

    @property
    def active_session_id(self) -> str | None:
        """Identifier of the most recently launched session, if tracked."""
        return self._active_session_id

    def clear_tracked_session(self) -> None:
        """Clear tracked active session ID after cleanup."""
        self._active_session_id = None

    def on_mount(self) -> None:
        self._app_logger.info(
            "UI mounted (%s v%s, theme=%s, lang=%s, platform=%s)",
            self._config.app.name,
            __version__,
            self._config.ui.theme.value,
            self._config.ui.default_language.value,
            self._platform_info.os_type.value,
        )
        self.push_screen(MainMenuScreen())

    def on_unmount(self) -> None:
        self._shutdown_runtime()

    def emit_feedback_sound(self) -> None:
        """Emit a terminal bell only when ``audio.sound_enabled`` is enabled."""
        if self._config.audio.sound_enabled:
            self._sound_events += 1
            self.bell()

    def issue_control_url(self) -> str:
        """Access is minted by an explicit terminal action, never by an HTTP route."""
        if self._runtime_cleaned:
            raise RuntimeServerError("POPTRAP is shutting down.")
        return self._control_server.issue_url()

    def action_open_control_center(self) -> None:
        if isinstance(self.screen, ControlCenterScreen):
            self.screen.action_new_access_link()
        else:
            self.push_screen(ControlCenterScreen())

    def open_module(self, module_id: ModuleId) -> None:
        """Route selection of one of the six main menu items."""
        spec = get_module_spec(module_id)
        self._app_logger.info(
            "Menu selected: %s (availability=%s)",
            spec.module_id.value,
            spec.availability.value,
        )

        if spec.module_id is ModuleId.EXIT:
            self.action_request_exit()
            return

        if spec.module_id is ModuleId.CREATE_PRANK:
            self.emit_feedback_sound()
            self.push_screen(CreatePrankScreen())
            return

        if spec.module_id is ModuleId.BLUETOOTH:
            self.emit_feedback_sound()
            self.push_screen(BluetoothScreen())
            return

        if spec.module_id in (ModuleId.QR_SHARE_LINK, ModuleId.LOCAL_NETWORK):
            self.emit_feedback_sound()
            screen = ShareLinkScreen() if spec.module_id is ModuleId.QR_SHARE_LINK else LocalNetworkScreen()
            self.push_screen(screen)
            return

        if spec.module_id is ModuleId.SETTINGS:
            self.emit_feedback_sound()
            self.push_screen(SettingsScreen())
            return

        if spec.availability is ModuleAvailability.UNAVAILABLE:
            self.emit_feedback_sound()
            self.push_screen(ModuleStatusScreen(spec))

    def launch_prank_session(
        self, config: PrankConfig
    ) -> tuple[SessionSnapshot, str]:
        """Start localhost runtime server, create and start a session, and return its URL."""
        self._prank_controller.stop_local_preview(purge=True)
        # Terminate any previously tracked live session before starting a replacement.
        if self._active_session_id is not None:
            try:
                existing = self._prank_controller.get_session(self._active_session_id)
                if not is_terminal_state(existing.state):
                    self._prank_controller.stop_session(
                        self._active_session_id,
                        reason="Replaced by new creator session.",
                    )
            except (SessionNotFoundError, SessionStateError):
                pass

        self._experience_server.start()
        created = self._prank_controller.create_session(config)
        started = self._prank_controller.start_session(created.session_id)
        url = self._experience_server.session_url(started.session_id)
        self._active_session_id = started.session_id
        self.emit_feedback_sound()
        self._app_logger.info(
            "Prank session ready: template=%s",
            config.template_id.value,
        )
        return started, url

    def issue_studio_url(self) -> str:
        """The terminal can refresh browser editor authorization without starting playback."""
        if self._runtime_cleaned:
            raise RuntimeServerError("POPTRAP is shutting down.")
        self._experience_server.start()
        return self._experience_server.studio_url()

    def launch_studio_session(
        self, studio_config: StudioConfig
    ) -> tuple[SessionSnapshot, str]:
        """Start localhost server and launch a full Phase 4 Studio session."""
        self._prank_controller.stop_local_preview(purge=True)
        if self._active_session_id is not None:
            try:
                existing = self._prank_controller.get_session(self._active_session_id)
                if not is_terminal_state(existing.state):
                    self._prank_controller.stop_session(
                        self._active_session_id,
                        reason="Replaced by new Studio session.",
                    )
            except (SessionNotFoundError, SessionStateError):
                pass

        self._experience_server.start()
        created = self._prank_controller.create_studio_session(
            studio_config, is_preview=False
        )
        started = self._prank_controller.start_session(created.session_id)
        url = self._experience_server.session_url(started.session_id)
        self._active_session_id = started.session_id
        self.emit_feedback_sound()
        self._app_logger.info(
            "Studio session ready: template=%s stages=%d",
            studio_config.template_id.value,
            len(studio_config.stages),
        )
        return started, url

    def launch_studio_preview(
        self, studio_config: StudioConfig
    ) -> tuple[SessionSnapshot, str, str]:
        """Start a local-only Live Preview session and return ``(snap, preview_url, studio_url)``."""
        self._experience_server.start()
        snap = self._prank_controller.start_local_preview(
            studio_config, grant_consent=False
        )
        preview_url = self._experience_server.session_url(snap.session_id)
        studio_url = self._experience_server.studio_url()
        self.emit_feedback_sound()
        self._app_logger.info("Studio local preview ready.")
        return snap, preview_url, studio_url

    def restart_studio_preview(
        self, session_id: str, studio_config: StudioConfig
    ) -> tuple[SessionSnapshot, str, str]:
        """Restart an existing local Studio preview cleanly and return its URLs."""
        self._experience_server.start()
        snap = self._prank_controller.restart_local_preview(
            session_id, studio_config, grant_consent=False
        )
        preview_url = self._experience_server.session_url(snap.session_id)
        studio_url = self._experience_server.studio_url()
        self.emit_feedback_sound()
        return snap, preview_url, studio_url

    def stop_studio_preview(
        self, session_id: str | None = None
    ) -> SessionSnapshot | None:
        """Stop the active local Studio preview and leave the editor ready."""
        snap = self._prank_controller.stop_local_preview(session_id)
        if snap is not None:
            self.emit_feedback_sound()
        return snap

    def stop_prank_session(self) -> bool:
        """Stop the tracked session and stop the local experience server."""
        stopped_any = False
        if self._prank_controller.stop_local_preview() is not None:
            stopped_any = True
        if self._active_session_id is not None:
            try:
                current = self._prank_controller.get_session(self._active_session_id)
                if not is_terminal_state(current.state):
                    self._prank_controller.stop_session(
                        self._active_session_id,
                        reason="Stopped by session creator.",
                    )
                    stopped_any = True
            except (SessionNotFoundError, SessionStateError):
                pass

        if self._experience_server.is_running:
            self._experience_server.stop()
            stopped_any = True

        if stopped_any:
            self.emit_feedback_sound()
        return stopped_any

    def _shutdown_runtime(self) -> None:
        """Ensure server sockets and engine scheduler threads exit cleanly."""
        if self._runtime_cleaned:
            return
        self._runtime_cleaned = True
        try:
            self._control_server.stop()
        finally:
            self._control_center.close()
            try:
                self._delivery_server.stop()
            finally:
                try:
                    if self._owns_bluetooth:
                        self._bluetooth_engine.shutdown()
                finally:
                    try:
                        self._experience_server.stop()
                    finally:
                        if self._owns_controller:
                            self._prank_controller.shutdown()

    def close_active_screen(self) -> None:
        """Return to the previous screen when a sub-screen is open."""
        if len(self.screen_stack) > 2:
            popped = self.screen.__class__.__name__
            self.pop_screen()
            self._app_logger.debug("Closed screen %s", popped)

    def _apply_runtime_config(self) -> None:
        """Apply the latest persisted preferences to Textual on its own thread."""
        new_config = self._config
        self.title = new_config.app.name
        self.sub_title = f"v{__version__}"
        self.theme = new_config.ui.theme.value
        set_log_level(new_config.logging.level)

        for screen in self.screen_stack:
            if isinstance(screen, BasePopTrapScreen) and screen.is_mounted:
                screen.refresh(repaint=True, layout=True)

    def _persist_config(self, updated: AppConfig) -> None:
        try:
            save_config(updated, self._config_path)
        except ConfigurationError:
            self._app_logger.error("Failed to persist settings.")
            raise
        self._config = updated
        self._config_warnings = ()

    def _persist_preferences(self, preferences: Preferences) -> AppConfig:
        with self._config_lock:
            if self._runtime_cleaned:
                raise ConfigurationError("POPTRAP is shutting down.")
            updated = self._config.with_preferences(
                theme=preferences.theme, sound_enabled=preferences.sound,
                default_language=preferences.language, log_level=preferences.log_level,
            )
            self._persist_config(updated)
            return updated

    def _save_control_preferences(self, preferences: Preferences) -> AppConfig:
        updated = self._persist_preferences(preferences)
        # No synchronous wait on the UI thread: it may be closing the server.
        self.post_message(ControlPreferencesChanged())
        return updated

    def on_control_preferences_changed(self, _message: ControlPreferencesChanged) -> None:
        if not self._runtime_cleaned:
            self._apply_runtime_config()
            if isinstance(self.screen, MainMenuScreen):
                self.screen.refresh_from_config()

    def update_preferences(
        self,
        *,
        theme: ThemeName,
        sound_enabled: bool,
        default_language: LanguageCode,
        log_level: LogLevel,
    ) -> AppConfig:
        """Validate, persist, and apply updated settings preferences."""
        updated = self._persist_preferences(Preferences(
            theme=theme, sound=sound_enabled, language=default_language, log_level=log_level,
        ))
        self._apply_runtime_config()
        self.emit_feedback_sound()
        return updated

    def reset_preferences(self) -> AppConfig:
        """Restore bundled default configuration, persist it, and apply live."""
        with self._config_lock:
            defaults = load_default_config(self._default_config_path)
            self._persist_config(defaults)
        self._apply_runtime_config()
        self.emit_feedback_sound()
        return defaults

    def action_request_exit(self) -> None:
        """Perform clean, graceful shutdown of server, sessions, and terminal UI."""
        self._app_logger.info("Graceful shutdown requested.")
        self._shutdown_runtime()
        self.exit(0)
