# Chat-provider mode (Phase 2)

Run Cursor as Hermes' chat brain through a local OpenAI-compatible bridge. Hermes keeps using its normal chat-completions transport; this package adapts Cursor's agent API.

## Operator checklist

1. Start `hermes-cursor-bridge` (loopback only).
2. Run `hermes-cursor provider install`, restart Hermes/gateway, then `hermes model` → Cursor + pick a model.
3. Disable Hermes toolsets for that session (file/terminal/etc.) so only Cursor acts on the repo.
4. Pin every Hermes auxiliary slot to a normal provider — not `main/auto` when Cursor is the main model.
5. Set `HERMES_CURSOR_BRIDGE_CWD` to the target project.
6. Run `hermes-cursor doctor --provider-mode`.
7. Do **not** use for PHI workloads.
8. Confirm `hermes doctor` authenticates to the bridge `/models` endpoint.

## Important non-parity

- Requests that include Hermes `tools` / `tool_choice` are **rejected**. Cursor has its own tools; the bridge does not emulate Hermes tool calls.
- Context length advertised on `/v1/models` is a connector budget (`HERMES_CURSOR_BRIDGE_CONTEXT_LENGTH`, default 65536) labeled `context_source=connector_budget`. It is not Cursor's underlying model window. If omitted, Hermes may fall through to a 256K default — set the budget or `model.context_length` explicitly.
- Cloud runtime is **not** used as the Hermes chat brain in v1 (local only).
- Streaming returns a standards-compatible SSE response (one assistant chunk is acceptable in v1).

## Auth

| Secret | Used by |
|---|---|
| `HERMES_CURSOR_BRIDGE_TOKEN` | Hermes → bridge Bearer token |
| `CURSOR_API_KEY` | Bridge → Cursor SDK |

Never reuse the Cursor API key as the bridge bearer token.
