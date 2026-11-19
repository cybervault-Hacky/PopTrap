"""Recipient-only HTTP gateway. Never mount the localhost administration app here."""

from __future__ import annotations

import asyncio
import socket
import threading
import time
from contextlib import asynccontextmanager, suppress
from typing import Any

import uvicorn
from fastapi import FastAPI, Request, Response
from fastapi.responses import FileResponse, JSONResponse
from pydantic import ValidationError

from poptrap.engine.activity import ActivityLog
from poptrap.engine.delivery.manager import DeliveryManager
from poptrap.engine.delivery.security import LOOPBACK, DeliveryEndpoint, DeliveryError, LanInterface, list_lan_interfaces
from poptrap.engine.http_safety import MAX_BODY_BYTES, MAX_HEADER_BYTES, RequestBudget, headers_within_limits, read_json, request_document
from poptrap.engine.models import ConsentRequest
from poptrap.engine.registry import TemplateRegistry
from poptrap.engine.runtime.assets import SECURITY_HEADERS, get_web_root, resolve_web_asset

_HEADERS = {
    **SECURITY_HEADERS,
    "Content-Security-Policy": (
        "default-src 'none'; script-src 'self'; style-src 'self'; img-src 'self' data:; "
        "connect-src 'self'; base-uri 'none'; form-action 'none'; frame-ancestors 'none'"
    ),
    "X-Frame-Options": "DENY",
}

# Only the scanner document needs a local decoder worker, selected-file blob
# URLs or camera access. LAN HTTP must not request the camera even if a browser
# is configured to treat a private address as trustworthy.
_SCAN_HEADERS = {
    "Content-Security-Policy": (
        "default-src 'none'; script-src 'self'; style-src 'self'; img-src blob:; "
        "media-src blob:; worker-src 'self'; connect-src 'none'; base-uri 'none'; form-action 'none'; "
        "frame-ancestors 'none'"
    ),
}

_ASSETS = frozenset({
    "styles/themes.css", "styles/base.css", "styles/animations.css", "styles/effects.css",
    "styles/responsive.css", "styles/delivery.css", "scripts/accessibility.js", "scripts/effects.js",
    "scripts/controller.js", "scripts/runtime.js", "scripts/delivery.js", "scripts/document-key.js",
    "styles/scan.css", "scripts/scan.js", "scripts/qr-worker.js", "scripts/vendor/jsQR.js",
    "scripts/effects/effects_manager.js", "scripts/effects/scanlines.js", "scripts/effects/transitions.js",
    "scripts/effects/particles.js", "scripts/effects/glitch.js", "scripts/effects/sequence.js",
})

_COMMANDS: dict[str, set[str]] = {
    "invite": {"token"},
    "respond": {"token", "decision"},
    "announce": {"token", "name", "available"},
    "inbox": set(),
    "withdraw": set(),
    "session": set(),
    "consent": {"consent"},
    "exit": set(),
    "complete": set(),
}


def _bearer(request: Request, *, optional: bool = False) -> str | None:
    headers = request.headers.getlist("authorization")
    if not headers and optional:
        return None
    if len(headers) != 1 or not headers[0].startswith("Bearer "):
        raise DeliveryError(403, "A scoped capability is required.")
    return headers[0][7:]


def _error(status: int, message: str) -> JSONResponse:
    return JSONResponse({"error": {"message": message}}, status_code=status, headers=_HEADERS)


def create_delivery_app(manager: DeliveryManager, *, budget: RequestBudget | None = None) -> FastAPI:
    endpoint = manager.endpoint
    root = get_web_root()
    requests = RequestBudget() if budget is None else budget
    ingress = RequestBudget(per_peer=192, total=800)
    joins = RequestBudget(per_peer=8, total=40, window=10.0)

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        async def reap() -> None:
            while True:
                await asyncio.sleep(1.0)
                manager.maintain()
                for limiter in (ingress, requests, joins):
                    limiter.clear_expired()

        task = asyncio.create_task(reap())
        try:
            yield
        finally:
            task.cancel()
            with suppress(asyncio.CancelledError):
                await task
            manager.close()

    app = FastAPI(
        docs_url=None, redoc_url=None, openapi_url=None, redirect_slashes=False, lifespan=lifespan,
    )

    @app.middleware("http")
    async def enforce_boundary(request: Request, call_next) -> Response:
        if not headers_within_limits(request.scope["headers"]):
            return _error(431, "Request headers are too large.")
        peer = request.client.host if request.client else ""
        if request.headers.getlist("host") != [endpoint.authority] or not endpoint.allows_peer(peer):
            return _error(403, "This listener is limited to its selected local address and subnet.")
        origins = request.headers.getlist("origin")
        if (origins or request.method not in ("GET", "HEAD")) and origins != [endpoint.origin]:
            return _error(403, "Same-origin requests only.")
        recipient_api = request.scope["path"].startswith("/delivery/v1/")
        if recipient_api and request.headers.getlist("sec-fetch-site") not in ([], ["same-origin"], ["none"]):
            return _error(403, "Same-origin requests only.")
        if not ingress.take(peer) or (recipient_api and not requests.take(peer)):
            response = _error(429, "Too many requests. Try again shortly.")
            response.headers["Retry-After"] = "10"
            return response
        if recipient_api and request.scope["query_string"]:
            return _error(400, "Query parameters are not supported.")
        if request.method in ("GET", "HEAD") and (
            request.headers.getlist("content-length") not in ([], ["0"])
            or "transfer-encoding" in request.headers
        ):
            return _error(400, "This method does not accept a request body.")
        response = await call_next(request)
        for key, value in _HEADERS.items():
            response.headers.setdefault(key, value)
        return response

    @app.exception_handler(DeliveryError)
    async def expected_error(_request: Request, exc: DeliveryError) -> JSONResponse:
        return _error(exc.status, exc.message)

    def asset(path: str) -> FileResponse:
        try:
            file_path, media_type = resolve_web_asset(path, web_root=root)
        except FileNotFoundError as exc:
            raise DeliveryError(404, "Resource not available.") from exc
        return FileResponse(file_path, media_type=media_type)

    @app.get("/scan")
    async def scan_page() -> FileResponse:
        response = asset("scan.html")
        response.headers.update(_SCAN_HEADERS)
        if endpoint.interface is None:
            response.headers["Permissions-Policy"] = SECURITY_HEADERS["Permissions-Policy"].replace(
                "camera=()", "camera=(self)", 1,
            )
        return response

    @app.get("/invite")
    async def invitation_page() -> FileResponse:
        return asset("delivery.html")

    @app.get("/availability")
    async def availability_page() -> FileResponse:
        if endpoint.interface is None:
            raise DeliveryError(404, "Availability is offered only by the LAN listener.")
        return asset("delivery.html")

    @app.get("/play")
    async def play_page() -> FileResponse:
        # This shell is inert without a scoped grant obtained by accepting.
        return asset("index.html")

    @app.get("/static/{path:path}")
    async def fixed_assets(path: str) -> FileResponse:
        if path not in _ASSETS:
            raise DeliveryError(404, "Resource not available.")
        return asset(path)

    @app.post("/delivery/v1/{command}")
    async def recipient_command(command: str, request: Request) -> dict[str, Any]:
        if command not in _COMMANDS:
            raise DeliveryError(404, "Unknown recipient command.")
        if command == "announce" and not joins.take(request.client.host):
            raise DeliveryError(429, "Too many availability requests. Try again shortly.")
        body = await read_json(request, _COMMANDS[command])
        if command == "invite":
            return manager.preview(body["token"])
        if command == "respond":
            return manager.decide(body["token"], body["decision"], lease=_bearer(request, optional=True))
        if command == "announce":
            return {"lease": manager.announce(body["token"], body["name"], available=body["available"])}
        grant = _bearer(request)
        assert grant is not None
        if command == "inbox":
            return manager.inbox(grant)
        if command == "withdraw":
            manager.withdraw(grant)
            return {"available": False}
        if command == "consent":
            try:
                ConsentRequest.model_validate(body)
            except ValidationError as exc:
                raise DeliveryError(400, "Explicit consent (true) is required to launch.") from exc
        proof = request_document(request.headers, required=command == "consent")
        return manager.play(grant, command, proof=proof)

    return app


class DeliveryServer:
    """Own a standalone listener and its volatile state, not the ExperienceServer."""

    def __init__(
        self, *, registry: TemplateRegistry | None = None, activity: ActivityLog | None = None,
    ) -> None:
        self._registry = registry
        self._activity = activity
        self._lock = threading.RLock()
        self._server: uvicorn.Server | None = None
        self._thread: threading.Thread | None = None
        self._socket: socket.socket | None = None
        self._manager: DeliveryManager | None = None

    @property
    def manager(self) -> DeliveryManager | None:
        with self._lock:
            if self._server and self._server.started and not self._server.should_exit and self._thread and self._thread.is_alive():
                return self._manager
            return None

    def start(self, interface: LanInterface | None = None, *, lan_confirmed: bool = False) -> DeliveryEndpoint:
        with self._lock:
            if interface is not None and (
                lan_confirmed is not True or interface not in list_lan_interfaces()
            ):
                raise DeliveryError(400, "Explicitly select and enable an assigned LAN address.")
            running = self.manager
            if running is not None:
                if running.endpoint.interface != interface:
                    raise DeliveryError(409, "Stop the current delivery listener before switching addresses.")
                return running.endpoint
            self.stop()
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            try:
                sock.bind((str(interface.address.ip) if interface is not None else LOOPBACK, 0))
                sock.listen(64)
                sock.setblocking(False)
            except OSError as exc:
                sock.close()
                raise DeliveryError(503, "Could not bind the selected address. Check that it is still assigned.") from exc
            endpoint = DeliveryEndpoint(port=sock.getsockname()[1], interface=interface)
            manager = DeliveryManager(endpoint, registry=self._registry, activity=self._activity)
            try:
                app = create_delivery_app(manager)
                server = uvicorn.Server(uvicorn.Config(
                    app, host=endpoint.host, port=endpoint.port, http="h11", ws="none", lifespan="on",
                    proxy_headers=False, access_log=False, server_header=False, log_level="warning",
                    limit_concurrency=64, timeout_keep_alive=3, timeout_graceful_shutdown=3,
                    h11_max_incomplete_event_size=MAX_HEADER_BYTES,
                ))
            except BaseException:
                manager.close()
                sock.close()
                raise

            def serve() -> None:
                try:
                    server.run(sockets=[sock])
                finally:
                    manager.close()
                    sock.close()

            thread = threading.Thread(target=serve, name="poptrap-delivery-server", daemon=True)
            self._socket, self._manager, self._server, self._thread = sock, manager, server, thread
            try:
                thread.start()
                deadline = time.monotonic() + 5.0
                while thread.is_alive() and time.monotonic() < deadline:
                    if server.started:
                        return endpoint
                    time.sleep(0.02)
            except BaseException:
                self.stop()
                raise
            self.stop()
            raise DeliveryError(503, "Delivery listener did not start.")

    def stop(self, *, expected: DeliveryManager | None = None) -> None:
        with self._lock:
            if expected is not None and expected is not self.manager:
                raise DeliveryError(409, "The delivery listener changed. Refresh before trying again.")
            if self._manager:
                self._manager.close()
            if self._server:
                self._server.should_exit = True
            if self._thread and self._thread.is_alive():
                self._thread.join(timeout=5.0)
            if self._socket:
                self._socket.close()
            self._thread = self._server = self._socket = self._manager = None
