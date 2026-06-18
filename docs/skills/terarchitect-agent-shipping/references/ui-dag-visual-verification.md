# Terarchitect UI / AgentHub DAG visual verification

Use this when changing Terarchitect's frontend shell, graph editor, AgentHub page, or browser-facing AgentHub API flow.

## Durable lessons from UI/DAG refresh

- Treat UI modernization as an end-to-end product path, not just a React refactor: inspect frontend routing/theme, graph-editor surfaces, AgentHub API shapes, and browser runtime constraints before implementation.
- Reuse one shared graph visual system for architecture graphs and AgentHub commit DAGs so style fixes cascade across both surfaces.
- AgentHub DAG viewer can be built from recent commits plus `parent_hash`; separate lineage endpoints are helpful but not required for a first viewer.
- Browser access to AgentHub protected endpoints needs three pieces verified together:
  1. UI-side API key entry/storage/clear flow.
  2. AgentHub CORS preflight support for `/api/*` with `Authorization` and `Content-Type` allowed.
  3. Normal API responses carrying CORS headers while preserving auth on non-OPTIONS requests.
- Key-clear flows must clear already-fetched protected UI state immediately. Do not merely remove localStorage and wait for a later 401; stale DAG/channel/post data can remain visible in-session.

## Recommended implementation shape

- Put shared graph styling/layout helpers in a reusable graph component directory, e.g. `frontend/src/components/graph/`.
- Use shared glass/canvas/edge/node helpers from both:
  - `GraphEditorPage` for architecture/service graphs.
  - `AgenthubPage` for commit DAG visualization.
- For AgentHub UI auth, prefer backend-owned auth:
  - The browser should call Terarchitect backend project-scoped graph APIs, not AgentHub directly.
  - The browser should not ask for or store an AgentHub API key.
  - Backend reads AgentHub with `AGENTHUB_API_KEY` from ignored `.env` when configured.
  - For local read-only dev bypass, backend should omit `Authorization` when the key is blank and AgentHub should explicitly enable unauthenticated read GETs.
  - Show backend/env configuration guidance when auth is missing or disabled incorrectly; do not ask the operator to paste a key into the browser.

## Verification checklist

Run automated checks:

```bash
cd frontend
CI=true npm test -- --runInBand --watchAll=false
npm run build

cd ../agenthub
go test ./...
```

Add focused tests for:

- AgentHub DAG renders commits, frontier leaves, channels/posts.
- Saved key is sent as a non-empty `Authorization: Bearer ...` header without exposing the key in logs/docs.
- Clearing the key removes protected data before/while the next auth failure renders.
- Offline AgentHub shows an unreachable helper instead of an auth helper.
- AgentHub server handles CORS preflight and normal authenticated API responses with CORS headers.

Run visual/operator probes against a production build when possible:

1. Serve `frontend/build` locally.
2. Inspect homepage for the global style shell.
3. Inspect Graph Editor for upgraded node/edge/canvas style and drag/edit affordances.
4. Inspect AgentHub for DAG viewer, frontier highlighting, auth helper, and no obvious layout breakage.
5. Use browser console/network output to catch CORS, auth, and runtime fetch errors.
6. If a browser-only blocker appears, fix it and rerun tests/build plus a blocker-only Codex review.

## Common pitfalls

- Running only Jest/build and skipping browser probes. CORS/preflight issues and production-layout problems are often invisible to unit tests.
- After patching AgentHub browser CORS/auth behavior, rebuild and recreate the running `agenthub` container before declaring the UI fixed. A stale AgentHub image can still return `405 Method Not Allowed` to `OPTIONS /api/*` preflights even though source tests pass, causing the browser to report a misleading generic `Failed to fetch` / offline state. Verify the live container with:
  ```bash
  docker compose build agenthub
  docker compose up -d --no-deps agenthub
  curl -i -sS -X OPTIONS http://localhost:8088/api/git/commits \
    -H 'Origin: http://localhost:3000' \
    -H 'Access-Control-Request-Method: GET' \
    -H 'Access-Control-Request-Headers: authorization'
  ```
  Expected response includes `204 No Content`, `Access-Control-Allow-Origin`, and `Access-Control-Allow-Headers: Authorization, Content-Type`.
- When reproducing an operator report that the AgentHub DAG says offline, distinguish three cases in order: AgentHub health (`/api/health`), protected endpoint auth (`401` means key helper, not offline), and browser CORS/preflight (`OPTIONS` failure means rebuild/restart or server CORS bug). Then validate authenticated `GET` requests from the browser origin return CORS headers and nonzero DAG counts.
- Treating `fetch` failures from protected AgentHub endpoints as offline by default. A 401 should produce an auth/key helper; true network failures should produce an unreachable helper.
- Adding an API key fallback for development but forgetting the operator-visible saved-key path.
- When using dev no-auth AgentHub reads, pass the bypass flag through every runtime that reasons about auth, not just the AgentHub server. `AGENTHUB_AUTH_DISABLED=1` must reach AgentHub (to allow read GETs) and backend/coordinator as needed (so doctor/status/readiness and backend proxy guidance do not still report auth missing while the live read path works).
- Leaving previously fetched protected AgentHub data visible after key removal.
- Saying AgentHub DAG viewing works before verifying CORS with an `Authorization` header in the browser path.
