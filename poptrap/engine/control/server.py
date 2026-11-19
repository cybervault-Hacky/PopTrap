"""Dedicated IPv4 loopback-only creator server. Never mounted on recipient listeners."""

from __future__ import annotations

import asyncio
import socket
import threading
import time
from collections.abc import Callable
from functools import partial
from typing import Any

import uvicorn
from fastapi import FastAPI, Request, Response
from fastapi.responses import FileResponse, JSONResponse
from pydantic import ValidationError
from starlette.concurrency import run_in_threadpool

from poptrap.core.exceptions import (
    ConfigurationError, PresetExistsError, PresetFormatError, PresetIdentifierError,
    PresetNotFoundError, PresetStorageError, PrankConfigError, RuntimeServerError,
    SessionIdentifierError, SessionLimitError, SessionNotFoundError, SessionStateError, TemplateNotFoundError,
)
from poptrap.engine.bluetooth.errors import BluetoothError
from poptrap.engine.control.diagnostics import run_diagnostics
from poptrap.engine.control.service import (
    BindLan, Command, ControlCenter, DeletePreset, Issue, Listener, ListenerTarget,
    OpenStudio, Preferences, RecordId, Selection, Source,
)
from poptrap.engine.delivery.security import LOOPBACK, DeliveryEndpoint, DeliveryError
from poptrap.engine.http_safety import MAX_HEADER_BYTES, RequestBudget, headers_within_limits, read_json
from poptrap.engine.runtime.assets import SECURITY_HEADERS, resolve_web_asset
from poptrap.engine.runtime.security import ACCESS_SECONDS, IDLE_SECONDS, UNLOCK_SECONDS, CreatorAccess

_HEADERS = {
    **SECURITY_HEADERS,
    "Content-Security-Policy": (
        "default-src 'none'; script-src 'self'; style-src 'self'; img-src 'self' data:; connect-src 'self'; "
        "base-uri 'none'; form-action 'none'; frame-ancestors 'none'"
    ),
    "X-Frame-Options": "DENY",
    "Cross-Origin-Resource-Policy": "same-origin",
}
_ASSETS = {"styles/control.css", "scripts/control.js"}
_API = "/control/v1/"


def _error(status: int, message: str) -> JSONResponse:
    return JSONResponse({"error": {"message": message}}, status_code=status, headers=_HEADERS)


def _bearer(request: Request) -> str:
    values = request.headers.getlist("authorization")
    if len(values) != 1 or not values[0].startswith("Bearer "):
        raise DeliveryError(401, "Dashboard authorization required. Open the link from the POPTRAP terminal.")
    return values[0][7:]


def create_control_app(center: ControlCenter, endpoint: DeliveryEndpoint, access: CreatorAccess) -> FastAPI:
    if endpoint.interface is not None:
        raise ValueError("The creator app accepts only a loopback endpoint.")
    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None, redirect_slashes=False)
    requests = RequestBudget(per_peer=120, total=120)
    unlocks = RequestBudget(per_peer=10, total=10)
    mutations = asyncio.Lock()
    checking = asyncio.Lock()
    handlers: dict[str, tuple[type[Command], Callable[..., dict[str, Any]]]] = {
        "open-studio": (OpenStudio, center.studio), "prepare": (Source, center.prepare),
        "preview": (RecordId, center.preview), "duplicate-preset": (RecordId, center.duplicate),
        "delete-preset": (DeletePreset, center.delete), "save-preferences": (Preferences, center.update_preferences),
        "stop-local-session": (RecordId, partial(center.end_local, revoke=False)),
        "revoke-local-session": (RecordId, partial(center.end_local, revoke=True)),
        "cleanup": (Command, center.cleanup), "cancel-bluetooth": (RecordId, center.cancel_bluetooth),
        "start-loopback": (Command, center.start_local), "start-lan": (BindLan, center.start_lan),
        "stop-listener": (Listener, center.stop_listener),
        "open-joining": (Listener, partial(center.joining, action="open")),
        "close-joining": (Listener, partial(center.joining, action="close")),
        "join-link": (Listener, partial(center.joining, action="view")),
        "select-device": (Selection, partial(center.selection, action="single")),
        "select-all": (Listener, partial(center.selection, action="all")),
        "select-none": (Listener, partial(center.selection, action="none")),
        "queue": (Issue, center.queue), "create-share": (Issue, center.create_share),
        "share-link": (ListenerTarget, center.show_share),
        "revoke-delivery": (ListenerTarget, partial(center.revoke_delivery, stop=False)),
        "stop-delivery": (ListenerTarget, partial(center.revoke_delivery, stop=True)),
    }

    @app.middleware("http")
    async def boundary(request: Request, call_next) -> Response:
        if not headers_within_limits(request.scope["headers"]):
            return _error(431, "Request headers are too large.")
        path = request.scope["path"]
        if request.client is None or request.client.host != LOOPBACK or request.headers.getlist("host") != [endpoint.authority]:
            return _error(403, "Control Center is limited to its local loopback address.")
        origins = request.headers.getlist("origin")
        if (origins or request.method not in ("GET", "HEAD")) and origins != [endpoint.origin]:
            return _error(403, "Same-origin requests only.")
        if path.startswith(_API) and request.headers.getlist("sec-fetch-site") not in ([], ["same-origin"], ["none"]):
            return _error(403, "Same-origin requests only.")
        if request.scope["query_string"]:
            return _error(400, "Query parameters are not supported.")
        if request.method in ("GET", "HEAD") and (
            request.headers.getlist("content-length") not in ([], ["0"]) or "transfer-encoding" in request.headers
        ):
            return _error(400, "This method does not accept a request body.")
        if not requests.take(LOOPBACK) or (path == _API + "unlock" and not unlocks.take(LOOPBACK)):
            response = _error(429, "Too many requests. Wait a few seconds before trying again.")
            response.headers["Retry-After"] = "10"
            return response
        if path.startswith(_API) and path != _API + "unlock":
            try:
                access.authorize(_bearer(request))
            except DeliveryError as exc:
                return _error(exc.status, exc.message)
        response = await call_next(request)
        for key, value in _HEADERS.items():
            response.headers.setdefault(key, value)
        return response

    @app.exception_handler(DeliveryError)
    async def expected_error(_request: Request, exc: DeliveryError) -> JSONResponse:
        return _error(exc.status, exc.message)

    @app.exception_handler(PresetStorageError)
    @app.exception_handler(PresetFormatError)
    @app.exception_handler(ConfigurationError)
    async def storage_error(_request: Request, _exc: Exception) -> JSONResponse:
        return _error(409, "Local configuration or preset storage could not be used. Run Diagnostics.")

    @app.exception_handler(SessionNotFoundError)
    @app.exception_handler(PresetNotFoundError)
    @app.exception_handler(TemplateNotFoundError)
    async def missing_error(_request: Request, _exc: Exception) -> JSONResponse:
        return _error(404, "This record is no longer available. Refresh the workspace.")

    @app.exception_handler(SessionIdentifierError)
    @app.exception_handler(PresetIdentifierError)
    @app.exception_handler(PrankConfigError)
    async def invalid_error(_request: Request, _exc: Exception) -> JSONResponse:
        return _error(400, "Invalid record or experience configuration. Verify it in Studio.")

    @app.exception_handler(SessionStateError)
    @app.exception_handler(PresetExistsError)
    @app.exception_handler(BluetoothError)
    async def conflict_error(_request: Request, _exc: Exception) -> JSONResponse:
        return _error(409, "The record changed or cannot be modified. Refresh and review its current state.")

    @app.exception_handler(SessionLimitError)
    async def capacity_error(_request: Request, _exc: Exception) -> JSONResponse:
        return _error(409, "Local session capacity reached. End a session or wait for it to expire.")

    @app.exception_handler(RuntimeServerError)
    async def runtime_error(_request: Request, _exc: Exception) -> JSONResponse:
        return _error(503, "The loopback experience server did not start. Check terminal status and loopback access.")

    def asset(path: str) -> FileResponse:
        try:
            file_path, mime = resolve_web_asset(path)
        except FileNotFoundError as exc:
            raise DeliveryError(404, "Resource not available.") from exc
        return FileResponse(file_path, media_type=mime)

    @app.get("/control")
    async def page() -> FileResponse:
        return asset("control.html")

    @app.get("/static/{path:path}")
    async def static(path: str) -> FileResponse:
        if path not in _ASSETS:
            raise DeliveryError(404, "Resource not available.")
        return asset(path)

    @app.get(_API + "state")
    async def state() -> dict[str, Any]:
        return await run_in_threadpool(center.state)

    @app.get(_API + "library")
    async def library() -> dict[str, Any]:
        return await run_in_threadpool(center.library)

    @app.get(_API + "diagnostics")
    async def diagnostics() -> dict[str, Any]:
        if checking.locked():
            raise DeliveryError(409, "A diagnostic check is already running.")
        async with checking:
            return await run_in_threadpool(run_diagnostics, center)

    @app.post(_API + "unlock")
    async def unlock(request: Request) -> dict[str, str]:
        body = await read_json(request, {"token"})
        return {"bearer": access.exchange(body["token"])}

    @app.post(_API + "lock")
    async def lock(request: Request) -> dict[str, bool]:
        await read_json(request, set())
        access.authorize(_bearer(request), lock=True)
        return {"locked": True}

    @app.post(_API + "{name}")
    async def command(name: str, request: Request) -> dict[str, Any]:
        if name not in handlers:
            raise DeliveryError(404, "Unknown creator command.")
        model, handler = handlers[name]
        raw = await read_json(request, set(model.model_fields))
        try:
            parsed = model.model_validate(raw)
        except ValidationError as exc:
            raise DeliveryError(400, "Invalid command fields. Refresh the workspace and check the values.") from exc
        async with mutations:
            # Recheck after reading the request and waiting behind another writer.
            access.authorize(_bearer(request))
            return await run_in_threadpool(handler, parsed)

    return app


class ControlServer:
    """Started only by a local terminal action. Owns no session store."""

    def __init__(self, center: ControlCenter) -> None:
        self._center = center
        self._lock = threading.RLock()
        self._access = CreatorAccess()
        self._server: uvicorn.Server | None = None
        self._thread: threading.Thread | None = None
        self._endpoint: DeliveryEndpoint | None = None
        self._socket: socket.socket | None = None

    def issue_url(self) -> str:
        with self._lock:
            if not (self._server and self._server.started and self._thread and self._thread.is_alive()):
                self.stop()
                self._start()
            assert self._endpoint is not None
            return f"{self._endpoint.origin}/control#{self._access.issue()}"

    def _start(self) -> None:
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        started = False
        try:
            sock.bind((LOOPBACK, 0))
            sock.listen(32)
            sock.setblocking(False)
            endpoint = DeliveryEndpoint(sock.getsockname()[1])
            server = uvicorn.Server(uvicorn.Config(
                create_control_app(self._center, endpoint, self._access), host=LOOPBACK, port=endpoint.port,
                http="h11", ws="none", lifespan="off", proxy_headers=False, access_log=False, server_header=False,
                log_level="error", limit_concurrency=32, timeout_keep_alive=3, timeout_graceful_shutdown=2,
                h11_max_incomplete_event_size=MAX_HEADER_BYTES,
            ))
            thread = threading.Thread(target=server.run, kwargs={"sockets": [sock]}, name="poptrap-creator-server", daemon=True)
            self._endpoint, self._server, self._thread, self._socket = endpoint, server, thread, sock
            thread.start()
            deadline = time.monotonic() + 4
            while thread.is_alive() and time.monotonic() < deadline:
                if server.started:
                    started = True
                    return
                time.sleep(0.02)
        except OSError as exc:
            raise RuntimeServerError("Could not bind the creator workspace to loopback.") from exc
        finally:
            if not started:
                self.stop()
                sock.close()
        raise RuntimeServerError("The creator workspace did not start.")

    def stop(self) -> None:
        with self._lock:
            self._access.clear()
            if self._server is not None:
                self._server.should_exit = True
            if self._thread is not None and self._thread.is_alive():
                self._thread.join(timeout=3)
            if self._socket is not None:
                self._socket.close()
            self._server = self._thread = self._endpoint = self._socket = None
