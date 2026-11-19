# Local runtime API

All routes are served by the local FastAPI app (`poptrap.engine.runtime.server:create_runtime_app`) bound to `127.0.0.1` by default.

### Browser & Asset Routes

These routes exist only on the numeric loopback runtime. Binding, authority and direct peer are checked separately. There is no CORS grant, proxy-header trust, or remote administration. JSON mutations require the exact request `Origin`, not a `localhost` alias.

| Method | Path | Description |
| :--- | :--- | :--- |
| `GET` | `/health` | Service liveness, version, and current active session count |
| `GET` | `/` | Browser shell landing page |
| `GET` | `/studio` | Locked Browser Studio shell; open a current one-time link to edit |
| `GET` | `/s/{session_id}` | Playable browser simulation page for a validated session (`400` invalid ID, `404` unknown/expired) |
| `GET` | `/static/{asset_path}` | Explicit runtime asset allowlist. Creator dashboard and recipient scanner assets are served only by their own listeners. |

### REST Endpoints

**Creator authorization.** From a terminal-issued Studio link, the browser reads and removes the fragment, then sends `POST /api/v1/studio/unlock` with `{"token": "…"}`. Use the returned `Authorization: Bearer …` for every other Studio route and for `/api/v1/validate`, session creation, `/start`, `/cancel`, and `/stop`. The grant is in memory, expires after 15 minutes without authenticated traffic or two hours total, and is invalidated by `POST /api/v1/studio/lock`, a replacement link, or server shutdown. Reload the editor only with a fresh link. Dashboard authorization is a separate grant. The read-only built-in template descriptions under `/api/v1/templates` contain no user data and need no bearer on the loopback runtime.

**Recipient authorization.** The `/s/…` link allows review, not creator commands or already-active playback. The player generates a random document key in memory, sends it as `X-Poptrap-Document` on consent and subsequent HTTP requests, and never saves it in a URL or browser storage. Only the document that consented can obtain the active payload and terminate active playback as the recipient. The delivery `/play` flow additionally requires its own short-lived grant; see the [recipient API](phase-6.md#local-rendezvous-protocol-deliveryv1).

| Method | Path | Status | Description |
| :--- | :--- | :--- | :--- |
| `GET` | `/api/v1/templates` | `200` | List all 5 template metadata descriptors, Phase 3 `themes`, supported `legacy_themes`, and enforced global limits |
| `GET` | `/api/v1/templates/{template_id}` | `200` / `404` | Retrieve metadata and validated default config for one template |
| `POST` | `/api/v1/validate` | `200` / `422` | Validate a `PrankConfig` object and return a structured `TemplatePreview` |
| `POST` | `/api/v1/sessions` | `201` / `422` / `429` | Create a session (`{"config": {...}, "auto_start": true}`) and return `session` + `experience_path` |
| `GET` | `/api/v1/sessions/{session_id}` | `200` / `400` / `404` | Return review state and `runtime` (`null` without consent and the matching document proof) |
| `POST` | `/api/v1/sessions/{session_id}/start` | `200` / `409` | Transition a `created` session to `ready` (awaiting recipient consent) |
| `POST` | `/api/v1/sessions/{session_id}/consent` | `200` / `400` / `409` / `422` | Record explicit recipient consent (`{"consent": true}`) and return active `runtime` payload |
| `POST` | `/api/v1/sessions/{session_id}/exit` | `200` / `409` | Immediate recipient exit (`stopped` / `cancelled`) |
| `POST` | `/api/v1/sessions/{session_id}/complete` | `200` / `409` | Mark an `active` session naturally `completed` |
| `POST` | `/api/v1/sessions/{session_id}/stop` | `200` / `409` | Creator stop action (`stopped`) |
| `POST` | `/api/v1/sessions/{session_id}/cancel` | `200` / `409` | Creator cancel action (`cancelled`) |
| `POST` | `/api/v1/studio/unlock` | `200` / `401` / `429` | Exchange the one-time ticket for an independent bearer |
| `POST` | `/api/v1/studio/lock` | `200` / `401` | Discard the editor authorization |
| `GET` | `/api/v1/studio/catalog` | `200` | Studio catalog: all 5 template summaries/defaults, themes, predefined stage kinds, message presets, and hard limits |
| `POST` | `/api/v1/studio/validate` | `200` / `422` | Validate a `StudioConfig` and return normalized config, compiled timeline, `TemplatePreview`, and effective duration |
| `POST` | `/api/v1/studio/preview` | `201` / `422` / `429` | Create and start a local-only Live Preview session (`{"config": {...}, "consent": false\|true}`) |
| `POST` | `/api/v1/studio/preview/{session_id}/restart` | `200` / `422` | Cleanly stop an active local preview and launch a fresh validated preview |
| `POST` | `/api/v1/studio/preview/{session_id}/stop` | `200` / `400` / `404` | Stop a running local Studio preview and return to editor |
| `POST` | `/api/v1/studio/sessions` | `201` / `422` / `429` | Create a full consent-gated localhost session from a validated `StudioConfig` |
| `GET` | `/api/v1/studio/presets` | `200` | List validated local presets and any skipped-file `warnings` |
| `POST` | `/api/v1/studio/presets` | `201` / `409` / `422` | Save a validated local preset atomically (`409 preset_exists` with `existing_id` unless `overwrite: true`) |
| `GET` | `/api/v1/studio/presets/{preset_id}` | `200` / `400` / `404` / `422` | Load and re-validate a single local preset (`400` on invalid/traversal ID) |
| `PATCH` | `/api/v1/studio/presets/{preset_id}` | `200` / `400` / `404` / `409` | Rename a local preset with collision checking |
| `POST` | `/api/v1/studio/presets/{preset_id}/duplicate` | `201` / `400` / `404` | Duplicate a saved preset under a new validated slug |
| `DELETE` | `/api/v1/studio/presets/{preset_id}` | `200` / `400` / `404` | Delete a local preset inside the user presets directory |

Error responses follow `{"error": {"code": "<error_code>", "message": "<summary>"}}` (with `"fields": [...]` on `422 validation_error`).

### WebSocket Endpoint

The player opens `/ws/sessions/{session_id}` on the same loopback authority and offers `poptrap.v1` followed by its document key as subprotocols. The server selects only `poptrap.v1`. Server messages include `session_update`, `pong`, and `error`; client actions are `ping`, `consent_start`, `exit`, and `complete`. A viewer without the launching document's proof never receives active playback data. The isolated recipient delivery listener is HTTP-only.
