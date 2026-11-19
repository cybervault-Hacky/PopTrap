"""Test clients use the same loopback and one-time authorization contract as browsers."""
from __future__ import annotations

from fastapi.testclient import TestClient

from poptrap.engine.runtime.security import CreatorAccess
from poptrap.engine.runtime.server import create_runtime_app

ORIGIN = "http://127.0.0.1"
DOCUMENT_KEY = "a" * 64
OTHER_DOCUMENT = "b" * 64
DOCUMENT = {"X-Poptrap-Document": DOCUMENT_KEY}


def runtime_client(controller, *, access=None, **kwargs):
    return TestClient(
        create_runtime_app(controller, access=access), base_url=ORIGIN,
        client=("127.0.0.1", 48001), **kwargs,
    )


def unlock(client: TestClient, access: CreatorAccess) -> str:
    response = client.post("/api/v1/studio/unlock", headers={"Origin": ORIGIN}, json={"token": access.issue()})
    assert response.status_code == 200, response.text
    return response.json()["bearer"]


def authorize(client: TestClient, access: CreatorAccess) -> None:
    client.headers.update({"Origin": ORIGIN, "Authorization": "Bearer " + unlock(client, access)})


def runtime_ws(client: TestClient, sid: str, *, document: str = DOCUMENT_KEY):
    return client.websocket_connect(
        f"ws://127.0.0.1/ws/sessions/{sid}", headers={"Origin": ORIGIN}, subprotocols=["poptrap.v1", document],
    )
