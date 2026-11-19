"""FastAPI browser experience server, WebSocket sync hub, and localhost runner."""

from __future__ import annotations

import asyncio
import ipaddress
import socket
import threading
import time
from contextlib import asynccontextmanager, suppress
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import uvicorn
from fastapi import APIRouter, Depends, FastAPI, Request, Response, WebSocket, WebSocketDisconnect
from fastapi.concurrency import run_in_threadpool
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
from poptrap.engine.delivery.security import DeliveryError
from poptrap.engine.http_safety import (
    DOCUMENT_PROTOCOL, MAX_HEADER_BYTES, RequestBudget, document_digest, headers_within_limits,
    read_json, request_document, strict_json,
)
from poptrap.engine.lifecycle import is_terminal_state
from poptrap.engine.models import (
    ClientEventType,
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
from poptrap.engine.runtime.security import CreatorAccess
from poptrap.engine.studio.presets import validate_preset_id
from poptrap.engine.validation import validate_client_event, validate_session_id

DEFAULT_BIND_HOST: str = "127.0.0.1"
DEFAULT_BIND_PORT: int = 0  # Ephemeral OS-assigned free localhost port
MAX_WS_CLIENTS_PER_SESSION: int = 8
MAX_WS_MESSAGE_BYTES: int = 2048
MAX_WS_CLIENTS = 24
MAX_CREATOR_BODY_BYTES = 32 * 1024
_ASSETS = frozenset({
    "styles/themes.css", "styles/base.css", "styles/animations.css", "styles/effects.css",
    "styles/responsive.css", "styles/prank.css", "styles/studio.css", "styles/bluetooth.css",
    "scripts/accessibility.js", "scripts/document-key.js", "scripts/effects.js", "scripts/controller.js",
    "scripts/delivery.js", "scripts/runtime.js", "scripts/studio.js", "scripts/bluetooth.js", "bluetooth.html",
    "scripts/effects/effects_manager.js", "scripts/effects/scanlines.js", "scripts/effects/transitions.js",
    "scripts/effects/particles.js", "scripts/effects/glitch.js", "scripts/effects/sequence.js",
})


@dataclass(eq=False, slots=True)
class _Watcher:
    loop: asyncio.AbstractEventLoop
    changed: asyncio.Event = field(default_factory=asyncio.Event)
    pending: bool = False


class _WebSocketHub:
    """Coalesce updates from the scheduler/TUI; each socket has only one writer."""

    def __init__(self, controller: PrankController) -> None:
        self._lock = threading.Lock()
        self._clients: dict[str, set[_Watcher]] = {}
        self._count = 0
        self.closed = False
        self._unsub_update = controller.subscribe(self._on_update, transitions_only=True)
        self._unsub_purge = controller.subscribe_purge(self._notify)

    def close(self) -> None:
        self._unsub_update()
        self._unsub_purge()
        with self._lock:
            self.closed = True
            for watchers in self._clients.values():
                for watcher in watchers:
                    self._wake(watcher)

    def register(self, sid: str) -> _Watcher | None:
        with self._lock:
            if self.closed or self._count >= MAX_WS_CLIENTS:
                return None
            watchers = self._clients.setdefault(sid, set())
            if len(watchers) >= MAX_WS_CLIENTS_PER_SESSION:
                return None
            watcher = _Watcher(asyncio.get_running_loop())
            watchers.add(watcher)
            self._count += 1
            return watcher

    def unregister(self, sid: str, watcher: _Watcher) -> None:
        with self._lock:
            watchers = self._clients[sid]
            watchers.remove(watcher)
            self._count -= 1
            if not watchers:
                self._clients.pop(sid)

    def acknowledge(self, watcher: _Watcher) -> None:
        with self._lock:
            watcher.changed.clear()
            watcher.pending = False

    @staticmethod
    def _wake(watcher: _Watcher) -> None:
        if not watcher.pending:
            watcher.pending = True
            with suppress(RuntimeError):  # Event loop can close during shutdown.
                watcher.loop.call_soon_threadsafe(watcher.changed.set)

    def _notify(self, sid: str) -> None:
        with self._lock:
            for watcher in self._clients.get(sid, ()):
                self._wake(watcher)

    def _on_update(self, snapshot: SessionSnapshot) -> None:
        self._notify(snapshot.session_id)


def _error_response(status_code: int, code: str, message: str) -> JSONResponse:
    return JSONResponse(
        status_code=status_code,
        content={"error": {"code": code, "message": message}},
        headers=SECURITY_HEADERS,
    )


def create_runtime_app(
    controller: PrankController | None = None,
    *,
    web_root: Path | None = None,
    host: str = DEFAULT_BIND_HOST,
    port: int = 80,
    access: CreatorAccess | None = None,
) -> FastAPI:
    """Create the loopback runtime pinned to the listener's numeric authority."""
    host = _loopback_host(host)
    if type(port) is not int or not 1 <= port <= 65535:
        raise ValueError("Runtime needs an assigned TCP port.")
    authority = _authority(host, port)
    origin = f"http://{authority}"
    engine = PrankController() if controller is None else controller
    assets_dir = get_web_root() if web_root is None else web_root
    creator_access = CreatorAccess() if access is None else access
    ingress = RequestBudget(per_peer=256, total=256)
    requests = RequestBudget(per_peer=128, total=128)
    upgrades = RequestBudget(per_peer=40, total=40)
    unlocks = RequestBudget(per_peer=10, total=10)

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        try:
            yield
        finally:
            hub.close()
            creator_access.clear()
            if controller is None:
                engine.shutdown()

    hub = _WebSocketHub(engine)
    app = FastAPI(
        title=f"{APP_NAME} Browser Experience Runtime", version=__version__,
        docs_url=None, redoc_url=None, openapi_url=None, redirect_slashes=False, lifespan=lifespan,
    )
    app.state.controller = engine
    app.state.ws_hub = hub

    def local_peer(conn: Request | WebSocket) -> bool:
        if conn.client is None or conn.headers.getlist("host") != [authority]:
            return False
        try:
            return ipaddress.ip_address(conn.client.host).is_loopback
        except ValueError:
            return False

    @app.middleware("http")
    async def boundary(request: Request, call_next) -> Response:
        if not headers_within_limits(request.scope["headers"]):
            return _error_response(431, "header_limit", "Request headers are too large.")
        if not local_peer(request):
            return _error_response(403, "local_only", "Use the numeric loopback address issued by POPTRAP.")
        read_only = request.method in ("GET", "HEAD")
        origins = request.headers.getlist("origin")
        if (origins or not read_only) and origins != [origin]:
            return _error_response(403, "origin_denied", "Same-origin requests only.")
        api = request.scope["path"].startswith("/api/v1/")
        if request.scope["query_string"]:
            return _error_response(400, "invalid_query", "Query parameters are not supported.")
        if api and request.headers.getlist("sec-fetch-site") not in ([], ["same-origin"], ["none"]):
            return _error_response(403, "origin_denied", "Same-origin requests only.")
        if read_only and (request.headers.getlist("content-length") not in ([], ["0"])
                          or "transfer-encoding" in request.headers):
            return _error_response(400, "invalid_body", "This method does not accept a body.")
        if not ingress.take(host) or (api and not requests.take(host)):
            response = _error_response(429, "rate_limit", "Too many requests. Try again shortly.")
            response.headers["Retry-After"] = "10"
            return response
        response = await call_next(request)
        for header, value in SECURITY_HEADERS.items():
            response.headers.setdefault(header, value)
        return response

    def creator_token(request: Request) -> str:
        values = request.headers.getlist("authorization")
        if len(values) != 1 or not values[0].startswith("Bearer "):
            raise DeliveryError(401, "Open a fresh Browser Studio link from POPTRAP.")
        return values[0][7:]

    async def authorize_creator(request: Request) -> None:
        creator_access.authorize(creator_token(request))

    creator = APIRouter(dependencies=[Depends(authorize_creator)])

    async def creator_json(request: Request, *, optional: bool = False) -> dict[str, Any]:
        body = await read_json(request, max_bytes=MAX_CREATOR_BODY_BYTES, optional=optional)
        await authorize_creator(request)  # The grant may have rotated during body receipt.
        return body

    async def creator_empty(request: Request) -> None:
        await read_json(request, set(), optional=True)
        await authorize_creator(request)

    @app.exception_handler(DeliveryError)
    async def expected_error(_request: Request, exc: DeliveryError) -> JSONResponse:
        return _error_response(exc.status, "request_denied", exc.message)

    @app.post("/api/v1/studio/unlock")
    async def studio_unlock(request: Request) -> dict[str, str]:
        if not unlocks.take(host):
            raise DeliveryError(429, "Too many unlock attempts. Try again shortly.")
        body = await read_json(request, {"token"})
        return {"bearer": creator_access.exchange(body["token"])}

    @creator.post("/api/v1/studio/lock")
    async def studio_lock(request: Request) -> dict[str, bool]:
        await read_json(request, set(), optional=True)
        creator_access.authorize(creator_token(request), lock=True)
        return {"locked": True}

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
        return _error_response(500, "preset_storage_error", "Local preset storage is unavailable. Check diagnostics.")

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

    def player_page() -> FileResponse:
        index_file, media_type = resolve_web_asset("index.html", web_root=assets_dir)
        # Some engines do not map CSP 'self' to a WebSocket origin.
        csp = SECURITY_HEADERS["Content-Security-Policy"].replace(
            "connect-src 'self';", f"connect-src 'self' ws://{authority}/ws/sessions/;",
        )
        return FileResponse(index_file, media_type=media_type, headers={"Content-Security-Policy": csp})

    @app.get("/")
    async def root_page() -> FileResponse:
        return player_page()

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
        return player_page()

    @app.get("/static/{asset_path:path}")
    async def static_asset(asset_path: str) -> Response:
        if asset_path not in _ASSETS:
            return _error_response(404, "asset_not_found", "Requested asset was not found.")
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

    @creator.post("/api/v1/validate")
    async def validate_configuration(
        request: Request,
    ) -> dict[str, Any]:
        payload = await creator_json(request)
        preview = engine.preview_config(payload)
        return {
            "valid": True,
            "config": preview.config.model_dump(mode="json"),
            "preview": preview.model_dump(mode="json"),
        }

    # ------------------------------------------------------------------
    # Session lifecycle API
    # ------------------------------------------------------------------

    @creator.post("/api/v1/sessions", status_code=201)
    async def create_session(
        request: Request,
    ) -> dict[str, Any]:
        payload = await creator_json(request)
        auto_start = payload.get("auto_start", False)
        raw_config = payload.get("config", payload)
        if not isinstance(raw_config, dict):
            raise PrankConfigError(
                "Request body must contain a valid configuration object.",
                field_errors=("config: Expected JSON object.",),
            )
        if type(auto_start) is not bool:
            raise DeliveryError(400, "auto_start must be a boolean.")
        snapshot = engine.create_session(raw_config)
        if auto_start:
            snapshot = engine.start_session(snapshot.session_id)
        return {
            "session": snapshot.model_dump(mode="json"),
            "experience_path": f"/s/{snapshot.session_id}",
        }

    def recipient_envelope(session_id: str, proof: bytes | None) -> dict[str, Any]:
        return engine.build_browser_payload(session_id, recipient=True, playback_owner=proof)

    @app.get("/api/v1/sessions/{session_id}")
    async def get_session(session_id: str, request: Request) -> dict[str, Any]:
        return recipient_envelope(session_id, request_document(request.headers))

    @creator.post("/api/v1/sessions/{session_id}/start")
    async def start_session(session_id: str, request: Request) -> dict[str, Any]:
        await creator_empty(request)
        snapshot = engine.start_session(session_id)
        return {
            "session": snapshot.model_dump(mode="json"),
            "experience_path": f"/s/{snapshot.session_id}",
        }

    @app.post("/api/v1/sessions/{session_id}/consent")
    async def grant_consent(session_id: str, request: Request) -> dict[str, Any]:
        body = await read_json(request, {"consent"})
        if body["consent"] is not True:
            raise PrankConfigError(
                "Explicit consent (consent=true) is required to start the simulation.",
                field_errors=("consent: Must be true.",),
            )
        proof = request_document(request.headers, required=True)
        engine.activate_session(session_id, playback_owner=proof)
        return recipient_envelope(session_id, proof)

    @app.post("/api/v1/sessions/{session_id}/exit")
    async def recipient_exit(session_id: str, request: Request) -> dict[str, Any]:
        await read_json(request, set(), optional=True)
        proof = request_document(request.headers)
        engine.end_recipient_session(session_id, proof)
        return recipient_envelope(session_id, proof)

    @app.post("/api/v1/sessions/{session_id}/complete")
    async def complete_session(session_id: str, request: Request) -> dict[str, Any]:
        await read_json(request, set(), optional=True)
        proof = request_document(request.headers)
        engine.end_recipient_session(session_id, proof, complete=True)
        return recipient_envelope(session_id, proof)

    @creator.post("/api/v1/sessions/{session_id}/stop")
    async def stop_session(session_id: str, request: Request) -> dict[str, Any]:
        await creator_empty(request)
        engine.end_session(session_id, reason="Stopped by session creator.")
        return engine.build_browser_payload(session_id)

    @creator.post("/api/v1/sessions/{session_id}/cancel")
    async def cancel_session(session_id: str, request: Request) -> dict[str, Any]:
        await creator_empty(request)
        engine.end_session(session_id, reason="Cancelled by session creator.", cancel=True)
        return engine.build_browser_payload(session_id)

    # ------------------------------------------------------------------
    # Phase 4: Local Prank Studio, Live Preview, & Preset Management API
    # ------------------------------------------------------------------

    @creator.get("/api/v1/studio/catalog")
    async def studio_catalog() -> dict[str, Any]:
        return engine.studio_catalog()

    @creator.post("/api/v1/studio/validate")
    async def studio_validate(
        request: Request,
    ) -> dict[str, Any]:
        payload = await creator_json(request)
        raw_config = payload.get("config", payload) if "config" in payload else payload
        if not isinstance(raw_config, dict):
            raise PrankConfigError(
                "Request body must contain a valid Studio configuration object.",
                field_errors=("config: Expected JSON object.",),
            )
        summary = engine.preview_studio(raw_config)
        return {"valid": True, **summary}

    @creator.post("/api/v1/studio/preview", status_code=201)
    async def studio_start_preview(
        request: Request,
    ) -> dict[str, Any]:
        payload = await creator_json(request)
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

    @creator.post("/api/v1/studio/preview/{session_id}/restart")
    async def studio_restart_preview(
        session_id: str,
        request: Request,
    ) -> dict[str, Any]:
        payload = await creator_json(request, optional=True)
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

    @creator.post("/api/v1/studio/preview/{session_id}/stop")
    async def studio_stop_preview(session_id: str, request: Request) -> dict[str, Any]:
        await creator_empty(request)
        engine.stop_local_preview(session_id)
        return engine.build_browser_payload(session_id)

    @creator.get("/api/v1/studio/preview/{session_id}")
    async def studio_preview_state(session_id: str) -> dict[str, Any]:
        snap = engine.get_preview(session_id)
        return {"state": snap.state, "remaining_seconds": snap.remaining_seconds}

    @creator.post("/api/v1/studio/preview/{session_id}/complete")
    async def studio_complete_preview(session_id: str, request: Request) -> dict[str, Any]:
        await creator_empty(request)
        return {"state": engine.complete_preview(session_id).state}

    @creator.post("/api/v1/studio/sessions", status_code=201)
    async def studio_create_session(
        request: Request,
    ) -> dict[str, Any]:
        payload = await creator_json(request)
        auto_start = payload.get("auto_start", False) if "config" in payload else False
        raw_config = payload.get("config", payload) if "config" in payload else payload
        if not isinstance(raw_config, dict):
            raise PrankConfigError(
                "Request body must contain a valid Studio configuration object.",
                field_errors=("config: Expected JSON object.",),
            )
        if type(auto_start) is not bool:
            raise DeliveryError(400, "auto_start must be a boolean.")
        snapshot = engine.create_studio_session(raw_config, is_preview=False)
        if auto_start:
            snapshot = engine.start_session(snapshot.session_id)
        return {
            "session": snapshot.model_dump(mode="json"),
            "experience_path": f"/s/{snapshot.session_id}",
        }

    async def preset_operation(request: Request, method, *args, **kwargs):
        def execute():
            creator_access.authorize(creator_token(request))
            return method(*args, **kwargs)
        return await run_in_threadpool(execute)

    @creator.get("/api/v1/studio/presets")
    async def studio_list_presets(request: Request) -> dict[str, Any]:
        summaries, warnings = await preset_operation(request, engine.presets.list_presets)
        return {
            "presets": [item.model_dump(mode="json") for item in summaries],
            "warnings": list(warnings),
        }

    @creator.post("/api/v1/studio/presets", status_code=201)
    async def studio_save_preset(
        request: Request,
    ) -> dict[str, Any]:
        payload = await creator_json(request)
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
        record = await preset_operation(
            request, engine.presets.save_preset, raw_config,
            preset_id=preset_id, overwrite=overwrite,
        )
        return {"preset": record.model_dump(mode="json")}

    @creator.get("/api/v1/studio/presets/{preset_id:path}")
    async def studio_get_preset(preset_id: str, request: Request) -> dict[str, Any]:
        record = await preset_operation(request, engine.presets.load_preset, preset_id)
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

    @creator.post("/api/v1/studio/presets/{preset_id}/duplicate", status_code=201)
    async def studio_duplicate_preset(
        preset_id: str,
        request: Request,
    ) -> dict[str, Any]:
        payload = await creator_json(request, optional=True)
        body = payload or {}
        new_name = body.get("name")
        if new_name is not None and not isinstance(new_name, str):
            raise PrankConfigError(
                "Duplicate preset name must be a string.",
                field_errors=("name: Expected string.",),
            )
        record = await preset_operation(request, engine.presets.duplicate_preset, preset_id, new_name=new_name)
        return {"preset": record.model_dump(mode="json")}

    @creator.patch("/api/v1/studio/presets/{preset_id:path}")
    async def studio_update_preset(
        preset_id: str,
        request: Request,
    ) -> dict[str, Any]:
        payload = await creator_json(request)
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
            record = await preset_operation(
                request, engine.presets.save_preset, raw_config, preset_id=checked_id, overwrite=True
            )
            return {"preset": record.model_dump(mode="json")}

        new_name = payload.get("name")
        if not isinstance(new_name, str):
            raise PrankConfigError(
                "Preset rename requires a non-empty 'name' string.",
                field_errors=("name: Expected string.",),
            )
        record = await preset_operation(request, engine.presets.rename_preset, checked_id, new_name)
        return {"preset": record.model_dump(mode="json")}

    @creator.delete("/api/v1/studio/presets/{preset_id:path}")
    async def studio_delete_preset(preset_id: str, request: Request) -> dict[str, Any]:
        await creator_empty(request)
        checked_id = validate_preset_id(preset_id)
        await preset_operation(request, engine.presets.delete_preset, checked_id)
        return {"deleted": True, "preset_id": checked_id}

    # ------------------------------------------------------------------
    # Real-time WebSocket session synchronization
    # ------------------------------------------------------------------

    @app.websocket("/ws/sessions/{session_id}")
    async def session_websocket(websocket: WebSocket, session_id: str) -> None:
        if (not headers_within_limits(websocket.scope["headers"]) or not local_peer(websocket)
                or websocket.headers.getlist("origin") != [origin]
                or websocket.headers.getlist("sec-fetch-site") not in ([], ["same-origin"], ["none"])
                or websocket.scope["query_string"] or not upgrades.take(host)):
            await websocket.close(code=1008)
            return
        protocols = websocket.scope["subprotocols"]
        proof = document_digest(protocols[1]) if len(protocols) == 2 and protocols[0] == DOCUMENT_PROTOCOL else None
        if proof is None:
            await websocket.close(code=1008)
            return
        try:
            checked = validate_session_id(session_id)
            initial = engine.get_session(checked)
            if initial.consent_granted and not engine.owns_playback(checked, proof):
                await websocket.close(code=1008)
                return
        except (SessionIdentifierError, SessionNotFoundError):
            await websocket.close(code=1008)
            return

        watcher = hub.register(checked)
        if watcher is None:
            await websocket.close(code=1013)
            return

        registered = False
        tasks: set[asyncio.Task] = set()
        frames = RequestBudget(per_peer=24, total=24)

        async def send(message: dict[str, Any]) -> None:
            async with asyncio.timeout(3):
                await websocket.send_json(message)

        async def update() -> bool:
            envelope = engine.build_browser_payload(checked, recipient=True, playback_owner=proof)
            await send({"type": "session_update", **envelope})
            session = envelope["session"]
            return not session["is_terminal"] and (session["state"] != "active" or envelope["runtime"] is not None)

        try:
            await websocket.accept(subprotocol=DOCUMENT_PROTOCOL)
            if not is_terminal_state(initial.state):
                engine.register_client_connection(checked)
                registered = True
            if not await update():
                await websocket.close()
                return
            incoming = asyncio.create_task(websocket.receive())
            changed = asyncio.create_task(watcher.changed.wait())
            tasks.update((incoming, changed))
            while not hub.closed:
                done, _ = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
                if hub.closed:
                    break
                if changed in done:
                    tasks.remove(changed)
                    hub.acknowledge(watcher)
                    if not await update():
                        break
                    changed = asyncio.create_task(watcher.changed.wait())
                    tasks.add(changed)
                if incoming not in done:
                    continue
                tasks.remove(incoming)
                message = incoming.result()
                if message["type"] == "websocket.disconnect":
                    return
                text = message.get("text")
                if text is None or len(text.encode("utf-8")) > MAX_WS_MESSAGE_BYTES or not frames.take(host):
                    await websocket.close(code=1008)
                    return
                try:
                    event = validate_client_event(strict_json(text))
                except (ValueError, RecursionError, PrankConfigError):
                    await send({"type": "error", "message": "Invalid client event."})
                else:
                    try:
                        if event.type is ClientEventType.PING:
                            snap = engine.get_session(checked)
                            await send({"type": "pong", "state": snap.state.value, "remaining_seconds": snap.remaining_seconds})
                        elif event.type is ClientEventType.CONSENT_START:
                            engine.activate_session(checked, playback_owner=proof)
                        elif event.type in (ClientEventType.EXIT, ClientEventType.COMPLETE):
                            engine.end_recipient_session(checked, proof, complete=event.type is ClientEventType.COMPLETE)
                            await update()
                            break
                    except SessionStateError as exc:
                        await send({"type": "error", "message": exc.message})
                incoming = asyncio.create_task(websocket.receive())
                tasks.add(incoming)
            await websocket.close()
        except (SessionNotFoundError, TimeoutError):
            with suppress(RuntimeError, WebSocketDisconnect, OSError):
                await websocket.close(code=1001)
        except (WebSocketDisconnect, OSError):
            pass
        finally:
            for task in tasks:
                task.cancel()
            if tasks:
                await asyncio.gather(*tasks, return_exceptions=True)
            hub.unregister(checked, watcher)
            if registered:
                engine.unregister_client_connection(checked)

    app.include_router(creator)
    return app


def _loopback_host(host: str) -> str:
    try:
        address = ipaddress.ip_address(host)
        if address.is_loopback and "%" not in host:
            return str(address)
    except ValueError:
        pass
    raise RuntimeServerError("The experience runtime can only bind to a numeric loopback address.")


def _authority(host: str, port: int) -> str:
    address = f"[{host}]" if ":" in host else host
    return address if port == 80 else f"{address}:{port}"


class ExperienceServer:
    """Managed loopback-only Uvicorn server; never a delivery or LAN listener."""

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
        self._access = CreatorAccess()
        self._requested_host = cleaned_host
        self._requested_port = port
        self._bound_host: str | None = None
        self._bound_port: int | None = None
        self._server: uvicorn.Server | None = None
        self._thread: threading.Thread | None = None
        self._socket: socket.socket | None = None
        self._startup_error: BaseException | None = None
        self._lock = threading.RLock()
        self._lifecycle_lock = threading.RLock()
        self._logger = get_logger("engine.server")

    @property
    def loopback_only(self) -> bool:
        """Whether the requested bind host is a numeric loopback address."""
        try:
            _loopback_host(self._requested_host)
            return True
        except RuntimeServerError:
            # Creator operations require a numeric destination, not name resolution.
            return False

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
            return f"http://{_authority(self._bound_host, self._bound_port)}"

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

    def studio_url(self, *, selection: tuple[str, str] | None = None) -> str:
        """Issue a one-time Browser Studio ticket; a new ticket locks the old editor."""
        with self._lifecycle_lock:
            base = self.base_url
            if base is None:
                raise RuntimeServerError("Start the local experience server before opening Browser Studio.")
            suffix = ""
            if selection is not None:
                kind, key = selection
                if kind == "preset":
                    key = validate_preset_id(key)
                elif kind == "template":
                    key = self._controller.get_template(key).template_id.value
                else:
                    raise ValueError("Unknown Studio selection.")
                suffix = f"&{kind}={key}"
            return f"{base}/studio#ticket={self._access.issue()}{suffix}"

    def start(self, *, startup_timeout: float = 8.0) -> str:
        """Bind socket and launch the FastAPI server; returns the real ``base_url``."""
        with self._lifecycle_lock:
            return self._start(startup_timeout=startup_timeout)

    def _start(self, *, startup_timeout: float) -> str:
        with self._lock:
            existing = self.base_url
            if existing is not None:
                return existing
            if self._thread is not None and self._thread.is_alive():
                raise RuntimeServerError("The experience listener is still stopping. Try again shortly.")

            bind_host = _loopback_host(self._requested_host)
            family = socket.AF_INET6 if ":" in bind_host else socket.AF_INET
            sock = socket.socket(family, socket.SOCK_STREAM)
            try:
                option = getattr(socket, "SO_EXCLUSIVEADDRUSE", socket.SO_REUSEADDR)
                sock.setsockopt(socket.SOL_SOCKET, option, 1)
                sock.bind((bind_host, self._requested_port))
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

            try:
                fastapi_app = create_runtime_app(self._controller, host=bound_host, port=bound_port, access=self._access)
                config = uvicorn.Config(
                    app=fastapi_app, host=bound_host, port=bound_port, http="h11",
                    log_level="error", access_log=False, proxy_headers=False, server_header=False,
                    ws="websockets", ws_max_size=MAX_WS_MESSAGE_BYTES, ws_max_queue=4,
                    ws_per_message_deflate=False, ws_ping_interval=12, ws_ping_timeout=6,
                    limit_concurrency=64, timeout_keep_alive=3, timeout_graceful_shutdown=2,
                    h11_max_incomplete_event_size=MAX_HEADER_BYTES, lifespan="on",
                )
                server = uvicorn.Server(config)
            except BaseException:
                sock.close()
                raise

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
                finally:
                    self._access.clear()
                    fastapi_app.state.ws_hub.close()
                    sock.close()
                    if server.started:
                        self._controller.cancel_all_sessions(reason="Local experience listener stopped.")

            thread = threading.Thread(
                target=_serve,
                name="poptrap-experience-server",
                daemon=True,
            )
            self._thread = thread
            try:
                thread.start()
            except BaseException:
                self._stop(timeout=0)
                raise

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
        with self._lifecycle_lock:
            self._stop(timeout=timeout)

    def _stop(self, *, timeout: float) -> None:
        self._access.clear()
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
            if thread.is_alive() and server is not None:
                server.force_exit = True
                if sock is not None:
                    sock.close()
                raise RuntimeServerError("The experience listener is still stopping. Try again shortly.")

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
