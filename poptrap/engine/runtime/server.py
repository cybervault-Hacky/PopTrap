"""FastAPI browser experience server, WebSocket sync hub, and localhost runner."""

from __future__ import annotations

import asyncio
import ipaddress
import json
import socket
import threading
import time
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

import uvicorn
from fastapi import Body, FastAPI, Request, Response, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, JSONResponse

from poptrap import APP_NAME, __version__
from poptrap.core.exceptions import (
    PrankConfigError,
    PresetExistsError,
    PresetFormatError,
    PresetIdentifierError,
    PresetNotFoundError,
    PresetStorageError,
    RuntimeServerError,
    SessionIdentifierError,
    SessionLimitError,
    SessionNotFoundError,
    SessionStateError,
    TemplateNotFoundError,
)
from poptrap.core.logging_setup import get_logger
from poptrap.engine.controller import PrankController
from poptrap.engine.lifecycle import is_terminal_state
from poptrap.engine.models import (
    ClientEventType,
    ConsentRequest,
    LEGACY_PRANK_THEMES,
    MAX_DURATION_SECONDS,
    MAX_POPUP_COUNT,
    MIN_DURATION_SECONDS,
    MIN_POPUP_COUNT,
    PRANK_THEME_CATALOG,
    SessionSnapshot,
    SessionState,
)
from poptrap.engine.runtime.assets import (
    SECURITY_HEADERS,
    get_web_root,
    resolve_web_asset,
)
from poptrap.engine.studio.presets import validate_preset_id
from poptrap.engine.validation import validate_client_event, validate_session_id

DEFAULT_BIND_HOST: str = "127.0.0.1"
DEFAULT_BIND_PORT: int = 0  # Ephemeral OS-assigned free localhost port
MAX_WS_CLIENTS_PER_SESSION: int = 8
MAX_WS_MESSAGE_BYTES: int = 2048


class _WebSocketHub:
    """Thread-safe dispatcher broadcasting live session changes to browsers."""

    def __init__(self, controller: PrankController) -> None:
        self._controller = controller
        self._lock = threading.RLock()
        self._clients: dict[str, list[tuple[WebSocket, asyncio.AbstractEventLoop]]] = {}
        self._unsub_update = controller.subscribe(self._on_session_update)
        self._unsub_purge = controller.subscribe_purge(self._on_session_purge)

    def close(self) -> None:
        """Unsubscribe controller hooks and clear tracked sockets."""
        self._unsub_update()
        self._unsub_purge()
        with self._lock:
            self._clients.clear()

    def register(
        self,
        session_id: str,
        websocket: WebSocket,
        loop: asyncio.AbstractEventLoop,
    ) -> bool:
        """Track ``websocket``; returns False if the per-session client cap is full."""
        with self._lock:
            bucket = self._clients.setdefault(session_id, [])
            if len(bucket) >= MAX_WS_CLIENTS_PER_SESSION:
                return False
            bucket.append((websocket, loop))
            return True

    def unregister(self, session_id: str, websocket: WebSocket) -> None:
        """Remove ``websocket`` from tracking."""
        with self._lock:
            bucket = self._clients.get(session_id)
            if not bucket:
                return
            self._clients[session_id] = [item for item in bucket if item[0] is not websocket]
            if not self._clients[session_id]:
                self._clients.pop(session_id, None)

    def _on_session_purge(self, session_id: str) -> None:
        with self._lock:
            self._clients.pop(session_id, None)

    def _on_session_update(self, snapshot: SessionSnapshot) -> None:
        with self._lock:
            targets = list(self._clients.get(snapshot.session_id, ()))
        if not targets:
            return

        try:
            envelope = self._controller.build_browser_payload(snapshot.session_id)
        except SessionNotFoundError:
            return

        message = {"type": "session_update", **envelope}
        should_close = is_terminal_state(snapshot.state)

        for ws, loop in targets:
            if loop.is_closed():
                continue
            try:
                loop.call_soon_threadsafe(
                    self._schedule_send, loop, ws, message, should_close
                )
            except RuntimeError:
                continue

    @staticmethod
    def _schedule_send(
        loop: asyncio.AbstractEventLoop,
        ws: WebSocket,
        message: dict[str, Any],
        should_close: bool,
    ) -> None:
        async def _deliver() -> None:
            try:
                await ws.send_json(message)
                if should_close:
                    await ws.close(code=1000)
            except Exception:  # noqa: BLE001
                pass

        loop.create_task(_deliver())


def _error_response(status_code: int, code: str, message: str) -> JSONResponse:
    return JSONResponse(
        status_code=status_code,
        content={"error": {"code": code, "message": message}},
    )


def create_runtime_app(
    controller: PrankController | None = None,
    *,
    web_root: Path | None = None,
) -> FastAPI:
    """Construct the FastAPI browser experience application."""
    engine = PrankController() if controller is None else controller
    assets_dir = get_web_root() if web_root is None else web_root
    hub = _WebSocketHub(engine)

    app = FastAPI(
        title=f"{APP_NAME} Browser Experience Runtime",
        version=__version__,
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
    )
    app.state.controller = engine
    app.state.ws_hub = hub

    @app.middleware("http")
    async def apply_security_headers(
        request: Request,
        call_next: Callable[[Request], Awaitable[Response]],
    ) -> Response:
        response = await call_next(request)
        for header, value in SECURITY_HEADERS.items():
            response.headers.setdefault(header, value)
        return response

    @app.exception_handler(SessionIdentifierError)
    async def handle_bad_session_id(
        _request: Request, exc: SessionIdentifierError
    ) -> JSONResponse:
        return _error_response(400, "invalid_session_id", exc.message)

    @app.exception_handler(SessionNotFoundError)
    async def handle_missing_session(
        _request: Request, exc: SessionNotFoundError
    ) -> JSONResponse:
        return _error_response(404, "session_not_found", exc.message)

    @app.exception_handler(TemplateNotFoundError)
    async def handle_missing_template(
        _request: Request, exc: TemplateNotFoundError
    ) -> JSONResponse:
        return _error_response(404, "template_not_found", exc.message)

    @app.exception_handler(SessionStateError)
    async def handle_state_conflict(
        _request: Request, exc: SessionStateError
    ) -> JSONResponse:
        return _error_response(409, "invalid_session_state", exc.message)

    @app.exception_handler(SessionLimitError)
    async def handle_session_limit(
        _request: Request, exc: SessionLimitError
    ) -> JSONResponse:
        return _error_response(429, "session_limit_reached", exc.message)

    @app.exception_handler(PrankConfigError)
    async def handle_prank_config_error(
        _request: Request, exc: PrankConfigError
    ) -> JSONResponse:
        return JSONResponse(
            status_code=422,
            content={
                "error": {
                    "code": "validation_error",
                    "message": exc.message,
                    "fields": list(exc.field_errors),
                }
            },
        )

    @app.exception_handler(PresetIdentifierError)
    async def handle_bad_preset_id(
        _request: Request, exc: PresetIdentifierError
    ) -> JSONResponse:
        return _error_response(400, "invalid_preset_id", exc.message)

    @app.exception_handler(PresetNotFoundError)
    async def handle_missing_preset(
        _request: Request, exc: PresetNotFoundError
    ) -> JSONResponse:
        return _error_response(404, "preset_not_found", exc.message)

    @app.exception_handler(PresetExistsError)
    async def handle_preset_exists(
        _request: Request, exc: PresetExistsError
    ) -> JSONResponse:
        return JSONResponse(
            status_code=409,
            content={
                "error": {
                    "code": "preset_exists",
                    "message": exc.message,
                    "existing_id": exc.existing_id,
                }
            },
        )

    @app.exception_handler(PresetFormatError)
    async def handle_preset_format(
        _request: Request, exc: PresetFormatError
    ) -> JSONResponse:
        return _error_response(422, "invalid_preset_format", exc.message)

    @app.exception_handler(PresetStorageError)
    async def handle_preset_storage(
        _request: Request, exc: PresetStorageError
    ) -> JSONResponse:
        return _error_response(500, "preset_storage_error", exc.message)

    # ------------------------------------------------------------------
    # Browser HTML & static asset routes
    # ------------------------------------------------------------------

    @app.get("/health")
    async def health() -> dict[str, Any]:
        return {
            "status": "ok",
            "service": "poptrap-runtime",
            "version": __version__,
            "active_sessions": engine.active_session_count,
        }

    @app.get("/")
    async def root_page() -> FileResponse:
        index_file, media_type = resolve_web_asset("index.html", web_root=assets_dir)
        return FileResponse(index_file, media_type=media_type)

    @app.get("/studio")
    async def studio_page() -> Response:
        try:
            studio_file, media_type = resolve_web_asset(
                "studio.html", web_root=assets_dir
            )
        except FileNotFoundError:
            return _error_response(
                404, "asset_not_found", "Prank Studio web asset was not found."
            )
        return FileResponse(studio_file, media_type=media_type)

    @app.get("/s/{session_id}")
    async def experience_page(session_id: str) -> Response:
        validate_session_id(session_id)
        engine.get_session(session_id)
        index_file, media_type = resolve_web_asset("index.html", web_root=assets_dir)
        return FileResponse(index_file, media_type=media_type)

    @app.get("/static/{asset_path:path}")
    async def static_asset(asset_path: str) -> Response:
        try:
            file_path, media_type = resolve_web_asset(asset_path, web_root=assets_dir)
        except FileNotFoundError:
            return _error_response(404, "asset_not_found", "Requested asset was not found.")
        return FileResponse(file_path, media_type=media_type)

    # ------------------------------------------------------------------
    # Template and configuration validation API
    # ------------------------------------------------------------------

    @app.get("/api/v1/templates")
    async def list_templates() -> dict[str, Any]:
        templates = [meta.model_dump(mode="json") for meta in engine.list_templates()]
        return {
            "templates": templates,
            "themes": [dict(entry) for entry in PRANK_THEME_CATALOG],
            "legacy_themes": [th.value for th in LEGACY_PRANK_THEMES],
            "limits": {
                "min_duration_seconds": MIN_DURATION_SECONDS,
                "max_duration_seconds": MAX_DURATION_SECONDS,
                "min_popup_count": MIN_POPUP_COUNT,
                "max_popup_count": MAX_POPUP_COUNT,
            },
        }

    @app.get("/api/v1/templates/{template_id}")
    async def get_template(template_id: str) -> dict[str, Any]:
        template = engine.get_template(template_id)
        return {
            "template": template.metadata.model_dump(mode="json"),
            "default_config": template.build_default_config().model_dump(mode="json"),
        }

    @app.post("/api/v1/validate")
    async def validate_configuration(
        payload: dict[str, Any] = Body(...),
    ) -> dict[str, Any]:
        preview = engine.preview_config(payload)
        return {
            "valid": True,
            "config": preview.config.model_dump(mode="json"),
            "preview": preview.model_dump(mode="json"),
        }

    # ------------------------------------------------------------------
    # Session lifecycle API
    # ------------------------------------------------------------------

    @app.post("/api/v1/sessions", status_code=201)
    async def create_session(
        payload: dict[str, Any] = Body(...),
    ) -> dict[str, Any]:
        auto_start = bool(payload.get("auto_start", False))
        raw_config = payload.get("config", payload)
        if not isinstance(raw_config, dict):
            raise PrankConfigError(
                "Request body must contain a valid configuration object.",
                field_errors=("config: Expected JSON object.",),
            )
        snapshot = engine.create_session(raw_config)
        if auto_start:
            snapshot = engine.start_session(snapshot.session_id)
        return {
            "session": snapshot.model_dump(mode="json"),
            "experience_path": f"/s/{snapshot.session_id}",
        }

    @app.get("/api/v1/sessions/{session_id}")
    async def get_session(session_id: str) -> dict[str, Any]:
        return engine.build_browser_payload(session_id)

    @app.post("/api/v1/sessions/{session_id}/start")
    async def start_session(session_id: str) -> dict[str, Any]:
        snapshot = engine.start_session(session_id)
        return {
            "session": snapshot.model_dump(mode="json"),
            "experience_path": f"/s/{snapshot.session_id}",
        }

    @app.post("/api/v1/sessions/{session_id}/consent")
    async def grant_consent(
        session_id: str,
        body: ConsentRequest,
    ) -> dict[str, Any]:
        if not body.consent:
            raise PrankConfigError(
                "Explicit consent (consent=true) is required to start the simulation.",
                field_errors=("consent: Must be true.",),
            )
        engine.activate_session(session_id)
        return engine.build_browser_payload(session_id)

    @app.post("/api/v1/sessions/{session_id}/exit")
    async def recipient_exit(session_id: str) -> dict[str, Any]:
        current = engine.get_session(session_id)
        if not is_terminal_state(current.state):
            if current.state is SessionState.CREATED:
                engine.cancel_session(session_id, reason="Session closed before start.")
            else:
                engine.stop_session(session_id, reason="Exited by recipient.")
        return engine.build_browser_payload(session_id)

    @app.post("/api/v1/sessions/{session_id}/complete")
    async def complete_session(session_id: str) -> dict[str, Any]:
        current = engine.get_session(session_id)
        if current.state is SessionState.ACTIVE:
            engine.complete_session(session_id)
        elif not is_terminal_state(current.state):
            raise SessionStateError(
                f"Cannot complete session in state '{current.state.value}'."
            )
        return engine.build_browser_payload(session_id)

    @app.post("/api/v1/sessions/{session_id}/stop")
    async def stop_session(session_id: str) -> dict[str, Any]:
        current = engine.get_session(session_id)
        if not is_terminal_state(current.state):
            engine.stop_session(session_id, reason="Stopped by session creator.")
        return engine.build_browser_payload(session_id)

    @app.post("/api/v1/sessions/{session_id}/cancel")
    async def cancel_session(session_id: str) -> dict[str, Any]:
        current = engine.get_session(session_id)
        if not is_terminal_state(current.state):
            engine.cancel_session(session_id, reason="Cancelled by session creator.")
        return engine.build_browser_payload(session_id)

    # ------------------------------------------------------------------
    # Phase 4: Local Prank Studio, Live Preview, & Preset Management API
    # ------------------------------------------------------------------

    @app.get("/api/v1/studio/catalog")
    async def studio_catalog() -> dict[str, Any]:
        return engine.studio_catalog()

    @app.post("/api/v1/studio/validate")
    async def studio_validate(
        payload: dict[str, Any] = Body(...),
    ) -> dict[str, Any]:
        raw_config = payload.get("config", payload) if "config" in payload else payload
        if not isinstance(raw_config, dict):
            raise PrankConfigError(
                "Request body must contain a valid Studio configuration object.",
                field_errors=("config: Expected JSON object.",),
            )
        summary = engine.preview_studio(raw_config)
        return {"valid": True, **summary}

    @app.post("/api/v1/studio/preview", status_code=201)
    async def studio_start_preview(
        payload: dict[str, Any] = Body(...),
    ) -> dict[str, Any]:
        consent = payload.get("consent", False) if "config" in payload else False
        if not isinstance(consent, bool):
            raise PrankConfigError(
                "Preview consent flag must be a boolean.",
                field_errors=("consent: Expected boolean.",),
            )
        raw_config = payload.get("config", payload) if "config" in payload else payload
        if not isinstance(raw_config, dict):
            raise PrankConfigError(
                "Request body must contain a valid Studio configuration object.",
                field_errors=("config: Expected JSON object.",),
            )
        summary = engine.preview_studio(raw_config)
        snapshot = engine.start_local_preview(raw_config, grant_consent=consent)
        browser = engine.build_browser_payload(snapshot.session_id)
        return {
            **browser,
            "preview_path": f"/s/{snapshot.session_id}",
            "timeline": summary["timeline"],
            "effective_duration_seconds": summary["effective_duration_seconds"],
        }

    @app.post("/api/v1/studio/preview/{session_id}/restart")
    async def studio_restart_preview(
        session_id: str,
        payload: dict[str, Any] | None = Body(default=None),
    ) -> dict[str, Any]:
        body = payload or {}
        consent = body.get("consent", False)
        if not isinstance(consent, bool):
            raise PrankConfigError(
                "Preview consent flag must be a boolean.",
                field_errors=("consent: Expected boolean.",),
            )
        raw_config = body.get("config")
        if raw_config is not None and not isinstance(raw_config, dict):
            raise PrankConfigError(
                "Preview config must be a JSON object when supplied.",
                field_errors=("config: Expected JSON object.",),
            )
        snapshot = engine.restart_local_preview(
            session_id,
            raw_config,
            grant_consent=consent,
        )
        browser = engine.build_browser_payload(snapshot.session_id)
        return {
            **browser,
            "preview_path": f"/s/{snapshot.session_id}",
        }

    @app.post("/api/v1/studio/preview/{session_id}/stop")
    async def studio_stop_preview(session_id: str) -> dict[str, Any]:
        engine.stop_local_preview(session_id)
        return engine.build_browser_payload(session_id)

    @app.post("/api/v1/studio/sessions", status_code=201)
    async def studio_create_session(
        payload: dict[str, Any] = Body(...),
    ) -> dict[str, Any]:
        auto_start = bool(payload.get("auto_start", False)) if "config" in payload else False
        raw_config = payload.get("config", payload) if "config" in payload else payload
        if not isinstance(raw_config, dict):
            raise PrankConfigError(
                "Request body must contain a valid Studio configuration object.",
                field_errors=("config: Expected JSON object.",),
            )
        snapshot = engine.create_studio_session(raw_config, is_preview=False)
        if auto_start:
            snapshot = engine.start_session(snapshot.session_id)
        return {
            "session": snapshot.model_dump(mode="json"),
            "experience_path": f"/s/{snapshot.session_id}",
        }

    @app.get("/api/v1/studio/presets")
    async def studio_list_presets() -> dict[str, Any]:
        summaries, warnings = engine.presets.list_presets()
        return {
            "presets": [item.model_dump(mode="json") for item in summaries],
            "warnings": list(warnings),
        }

    @app.post("/api/v1/studio/presets", status_code=201)
    async def studio_save_preset(
        payload: dict[str, Any] = Body(...),
    ) -> dict[str, Any]:
        overwrite = payload.get("overwrite", False)
        if not isinstance(overwrite, bool):
            raise PrankConfigError(
                "Preset overwrite flag must be a boolean.",
                field_errors=("overwrite: Expected boolean.",),
            )
        preset_id = payload.get("preset_id")
        if preset_id is not None and not isinstance(preset_id, str):
            raise PresetIdentifierError("Preset identifier must be a string.")
        raw_config = payload.get("config")
        if not isinstance(raw_config, dict):
            raise PrankConfigError(
                "Preset save request requires a 'config' JSON object.",
                field_errors=("config: Expected JSON object.",),
            )
        record = engine.presets.save_preset(
            raw_config,
            preset_id=preset_id,
            overwrite=overwrite,
        )
        return {"preset": record.model_dump(mode="json")}

    @app.get("/api/v1/studio/presets/{preset_id:path}")
    async def studio_get_preset(preset_id: str) -> dict[str, Any]:
        record = engine.presets.load_preset(preset_id)
        summary = engine.preview_studio(record.config)
        return {
            "preset": record.model_dump(mode="json"),
            "details": {
                "template_name": summary["template_name"],
                "template_summary": summary["template_summary"],
                "effective_duration_seconds": summary["effective_duration_seconds"],
                "stage_count": summary["stage_count"],
                "timeline": summary["timeline"],
            },
        }

    @app.post("/api/v1/studio/presets/{preset_id}/duplicate", status_code=201)
    async def studio_duplicate_preset(
        preset_id: str,
        payload: dict[str, Any] | None = Body(default=None),
    ) -> dict[str, Any]:
        body = payload or {}
        new_name = body.get("name")
        if new_name is not None and not isinstance(new_name, str):
            raise PrankConfigError(
                "Duplicate preset name must be a string.",
                field_errors=("name: Expected string.",),
            )
        record = engine.presets.duplicate_preset(preset_id, new_name=new_name)
        return {"preset": record.model_dump(mode="json")}

    @app.patch("/api/v1/studio/presets/{preset_id:path}")
    async def studio_update_preset(
        preset_id: str,
        payload: dict[str, Any] = Body(...),
    ) -> dict[str, Any]:
        checked_id = validate_preset_id(preset_id)
        if "config" in payload:
            overwrite = payload.get("overwrite", False)
            if overwrite is not True:
                raise PresetExistsError(
                    "Overwriting an existing preset requires explicit confirmation (overwrite=true).",
                    existing_id=checked_id,
                )
            raw_config = payload.get("config")
            if not isinstance(raw_config, dict):
                raise PrankConfigError(
                    "Preset update 'config' must be a JSON object.",
                    field_errors=("config: Expected JSON object.",),
                )
            record = engine.presets.save_preset(
                raw_config, preset_id=checked_id, overwrite=True
            )
            return {"preset": record.model_dump(mode="json")}

        new_name = payload.get("name")
        if not isinstance(new_name, str):
            raise PrankConfigError(
                "Preset rename requires a non-empty 'name' string.",
                field_errors=("name: Expected string.",),
            )
        record = engine.presets.rename_preset(checked_id, new_name)
        return {"preset": record.model_dump(mode="json")}

    @app.delete("/api/v1/studio/presets/{preset_id:path}")
    async def studio_delete_preset(preset_id: str) -> dict[str, Any]:
        checked_id = validate_preset_id(preset_id)
        engine.presets.delete_preset(checked_id)
        return {"deleted": True, "preset_id": checked_id}

    # ------------------------------------------------------------------
    # Real-time WebSocket session synchronization
    # ------------------------------------------------------------------

    @app.websocket("/ws/sessions/{session_id}")
    async def session_websocket(websocket: WebSocket, session_id: str) -> None:
        try:
            checked = validate_session_id(session_id)
            initial = engine.get_session(checked)
        except SessionIdentifierError:
            await websocket.close(code=1008)
            return
        except SessionNotFoundError:
            await websocket.close(code=1008)
            return

        loop = asyncio.get_running_loop()
        if not hub.register(checked, websocket, loop):
            await websocket.close(code=1013)
            return

        await websocket.accept()
        registered_client = False
        try:
            if not is_terminal_state(initial.state):
                engine.register_client_connection(checked)
                registered_client = True

            await websocket.send_json(
                {"type": "session_update", **engine.build_browser_payload(checked)}
            )

            if is_terminal_state(initial.state):
                await websocket.close(code=1000)
                return

            while True:
                raw_text = await websocket.receive_text()
                if len(raw_text.encode("utf-8")) > MAX_WS_MESSAGE_BYTES:
                    await websocket.send_json(
                        {"type": "error", "message": "Message exceeds size limit."}
                    )
                    continue

                try:
                    parsed = json.loads(raw_text)
                    event = validate_client_event(parsed)
                except (json.JSONDecodeError, PrankConfigError) as exc:
                    await websocket.send_json(
                        {"type": "error", "message": f"Invalid client event: {exc}"}
                    )
                    continue

                if event.type is ClientEventType.PING:
                    snap = engine.get_session(checked)
                    await websocket.send_json(
                        {
                            "type": "pong",
                            "state": snap.state.value,
                            "remaining_seconds": snap.remaining_seconds,
                        }
                    )
                elif event.type is ClientEventType.CONSENT_START:
                    try:
                        engine.activate_session(checked)
                    except SessionStateError as exc:
                        await websocket.send_json({"type": "error", "message": exc.message})
                elif event.type is ClientEventType.EXIT:
                    snap = engine.get_session(checked)
                    if not is_terminal_state(snap.state):
                        engine.stop_session(checked, reason="Exited by recipient.")
                    break
                elif event.type is ClientEventType.COMPLETE:
                    snap = engine.get_session(checked)
                    if snap.state is SessionState.ACTIVE:
                        engine.complete_session(checked)
                    break

        except WebSocketDisconnect:
            pass
        except SessionNotFoundError:
            pass
        finally:
            hub.unregister(checked, websocket)
            if registered_client:
                engine.unregister_client_connection(checked)

    return app


def _format_url_host(host: str) -> str:
    """Format an IPv4, IPv6, or hostname safely for HTTP URL display."""
    try:
        addr = ipaddress.ip_address(host)
    except ValueError:
        return host
    if isinstance(addr, ipaddress.IPv6Address):
        return f"[{host}]"
    return host


class ExperienceServer:
    """Managed background Uvicorn server bound strictly to localhost by default."""

    def __init__(
        self,
        controller: PrankController,
        *,
        host: str = DEFAULT_BIND_HOST,
        port: int = DEFAULT_BIND_PORT,
    ) -> None:
        cleaned_host = host.strip()
        if not cleaned_host:
            raise RuntimeServerError("Server bind host cannot be empty.")
        if not isinstance(port, int) or isinstance(port, bool) or not (0 <= port <= 65535):
            raise RuntimeServerError("Server port must be an integer between 0 and 65535.")

        self._controller = controller
        self._requested_host = cleaned_host
        self._requested_port = port
        self._bound_host: str | None = None
        self._bound_port: int | None = None
        self._server: uvicorn.Server | None = None
        self._thread: threading.Thread | None = None
        self._socket: socket.socket | None = None
        self._startup_error: BaseException | None = None
        self._lock = threading.RLock()
        self._logger = get_logger("engine.server")

    @property
    def is_running(self) -> bool:
        """Return True when the underlying Uvicorn server is actively serving."""
        with self._lock:
            return (
                self._server is not None
                and self._server.started
                and not self._server.should_exit
                and self._thread is not None
                and self._thread.is_alive()
            )

    @property
    def host(self) -> str | None:
        """Actual bound host interface when running, else None."""
        with self._lock:
            return self._bound_host if self.is_running else None

    @property
    def port(self) -> int | None:
        """Actual bound TCP port when running, else None."""
        with self._lock:
            return self._bound_port if self.is_running else None

    @property
    def base_url(self) -> str | None:
        """Actual HTTP base URL (for example ``http://127.0.0.1:51234``) when running."""
        with self._lock:
            if not self.is_running or self._bound_host is None or self._bound_port is None:
                return None
            return f"http://{_format_url_host(self._bound_host)}:{self._bound_port}"

    def session_url(self, session_id: str) -> str:
        """Return the exact playable URL for ``session_id`` on the running server."""
        checked = validate_session_id(session_id)
        base = self.base_url
        if base is None:
            raise RuntimeServerError(
                "Browser experience server is not currently running.",
                hint="Start the server before requesting a session URL.",
            )
        return f"{base}/s/{checked}"

    def studio_url(self) -> str:
        """Return the localhost URL for the interactive Prank Studio editor."""
        base = self.base_url
        if base is None:
            raise RuntimeServerError(
                "Browser experience server is not currently running.",
                hint="Start the server before requesting the Prank Studio URL.",
            )
        return f"{base}/studio"

    def start(self, *, startup_timeout: float = 8.0) -> str:
        """Bind socket and launch the FastAPI server; returns the real ``base_url``."""
        with self._lock:
            existing = self.base_url
            if existing is not None:
                return existing

            family = socket.AF_INET6 if ":" in self._requested_host else socket.AF_INET
            sock = socket.socket(family, socket.SOCK_STREAM)
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            try:
                sock.bind((self._requested_host, self._requested_port))
                sock.listen(64)
                sock.setblocking(False)
                sockname = sock.getsockname()
                bound_host = str(sockname[0])
                bound_port = int(sockname[1])
            except OSError as exc:
                sock.close()
                raise RuntimeServerError(
                    f"Unable to bind local server on {self._requested_host}:{self._requested_port} ({exc}).",
                    hint="Ensure loopback networking is permitted or choose another free port.",
                ) from exc

            fastapi_app = create_runtime_app(self._controller)
            config = uvicorn.Config(
                app=fastapi_app,
                host=bound_host,
                port=bound_port,
                log_level="error",
                access_log=False,
                ws="auto",
                lifespan="off",
            )
            server = uvicorn.Server(config)
            # Prevent Uvicorn from hijacking terminal SIGINT/SIGTERM handlers in TUI mode.
            server.install_signal_handlers = lambda: None  # type: ignore[method-assign]

            self._socket = sock
            self._server = server
            self._bound_host = bound_host
            self._bound_port = bound_port
            self._startup_error = None

            def _serve() -> None:
                try:
                    asyncio.run(server.serve(sockets=[sock]))
                except BaseException as exc:  # noqa: BLE001
                    self._startup_error = exc

            thread = threading.Thread(
                target=_serve,
                name="poptrap-experience-server",
                daemon=True,
            )
            self._thread = thread
            thread.start()

        deadline = time.monotonic() + startup_timeout
        while time.monotonic() < deadline:
            if self._startup_error is not None:
                err = self._startup_error
                self.stop()
                raise RuntimeServerError(
                    f"Browser experience server failed during startup: {err}",
                    hint="Inspect the POPTRAP log file for diagnostic details.",
                ) from err
            if server.started and thread.is_alive():
                url = self.base_url
                assert url is not None
                self._logger.info("Experience server listening at %s", url)
                return url
            if not thread.is_alive():
                break
            time.sleep(0.02)

        self.stop()
        raise RuntimeServerError(
            "Timed out waiting for the local browser experience server to start.",
            hint="Retry starting the session or run 'python poptrap.py --check'.",
        )

    def stop(self, *, timeout: float = 5.0) -> None:
        """Stop the running Uvicorn server and release its bound socket."""
        with self._lock:
            server = self._server
            thread = self._thread
            sock = self._socket
            hub = (
                getattr(server.config.app.state, "ws_hub", None)
                if server is not None and hasattr(server.config.app, "state")
                else None
            )
            if server is not None:
                server.should_exit = True

        if hub is not None:
            try:
                hub.close()
            except Exception:  # noqa: BLE001
                pass

        if thread is not None and thread.is_alive() and thread is not threading.current_thread():
            thread.join(timeout=timeout)

        if sock is not None:
            try:
                sock.close()
            except OSError:
                pass

        with self._lock:
            was_bound = (
                f"{self._bound_host}:{self._bound_port}"
                if self._bound_host and self._bound_port
                else None
            )
            self._server = None
            self._thread = None
            self._socket = None
            self._bound_host = None
            self._bound_port = None
            self._startup_error = None

        if was_bound:
            self._logger.info("Experience server stopped (%s).", was_bound)
