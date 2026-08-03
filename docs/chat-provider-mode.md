# Chat-provider mode (Phase 2)

Run Cursor as Hermes' chat brain through a local OpenAI-compatible bridge. Hermes keeps using its normal chat-completions transport; this package adapts Cursor's agent API.

## Operator checklist

Recommended one-shot setup (installs into the **active Hermes profile** home — e.g. `~/.hermes/profiles/co-cto` — so Desktop can see the provider):

```bash
# CURSOR_API_KEY must already be in your shell / Hermes .env
hermes-cursor setup --cwd /absolute/path/to/project --profile co-cto --load-service
# restart Hermes Desktop / gateway
hermes-cursor doctor --provider-mode
```

Then in Hermes Desktop (or `hermes model`): select **Cursor (SDK bridge)** and a model from `/v1/models`.

Manual steps (equivalent):

1. Write `~/.hermes/cursor-sdk/bridge.env` (`0600`) with `CURSOR_API_KEY`, `HERMES_CURSOR_BRIDGE_TOKEN`, `HERMES_CURSOR_BRIDGE_CWD`.
2. Put the same `HERMES_CURSOR_BRIDGE_TOKEN` / `HERMES_CURSOR_BASE_URL` into the **profile** `.env` (not only `~/.hermes/.env`).
3. Start `hermes-cursor-bridge` (loopback only) or `hermes-cursor service install --load` via setup.
4. Run `hermes-cursor provider install --profile <name>` (installs the shim **and** writes `providers.cursor` into that profile’s `config.yaml`), restart Hermes/gateway, then pick Cursor.
5. Disable Hermes toolsets for that session (file/terminal/etc.) so only Cursor acts on the repo.
6. Pin every Hermes auxiliary slot to a normal provider — not `main/auto` when Cursor is the main model.
7. Run `hermes-cursor doctor --provider-mode` (checks shim + `providers.cursor`).
8. Do **not** use for PHI workloads.
9. Confirm `hermes doctor` authenticates to the bridge `/models` endpoint.

### Why `providers.cursor` in `config.yaml`?

Hermes model-provider plugins can appear in the Models catalog, but Desktop/CLI **model switch** resolves providers via `resolve_provider_full()`, which does **not** consult the plugin registry — only models.dev / Hermes overlays / `config.yaml` `providers:`. Setup writes:

```yaml
providers:
  # hermes-cursor-sdk-managed-provider
  cursor:
    name: "Cursor (SDK bridge)"
    api: "http://127.0.0.1:8787/v1"
    key_env: "HERMES_CURSOR_BRIDGE_TOKEN"
```

Missing this entry surfaces as: `Unknown provider 'cursor'. … define it in config.yaml under 'providers:'`.

## Important non-parity

- Requests that include Hermes `tools` / `tool_choice` are **stripped** (not forwarded). Cursor has its own tools inside the SDK agent; the bridge does not emulate Hermes `tool_calls`. Still disable Hermes file/terminal toolsets so you are not paying for unused Hermes tool schemas.
- Hermes chat `session_id` is **not** mapped onto a sticky Cursor agent in v1 (each completion is a fresh local `Agent.prompt`).
- Context length advertised on `/v1/models` prefers the **Cursor model window** (`context_source=cursor_model_window`): Composer family is a fixed **200K**; models with a catalog `context` param advertise the **max** option (often **1M**) and also return `context_options` (e.g. `[272000, 1000000]`). `HERMES_CURSOR_BRIDGE_CONTEXT_LENGTH` is only the fallback (`context_source=connector_budget`, default 200000). Pin `model.context_length` in Hermes if a stale cache still shows the wrong max.
- Cloud runtime is **not** used as the Hermes chat brain in v1 (local only).
- Streaming returns a standards-compatible SSE response (one assistant chunk is acceptable in v1).

## Context meter contract (CLI + Desktop)

Hermes statusbars show **used / max** from this bridge:

| Field | Source | Notes |
|---|---|---|
| **max** (`context_length`) | `GET /v1/models` | Prefer `cursor_model_window` (Composer = 200K). Fallback: `connector_budget`. |
| **used** (`prompt_tokens`) | Chat completion `usage` | OpenAI aliases: `prompt_tokens` = Cursor `input_tokens + cache_read_tokens + cache_write_tokens`; `completion_tokens` = `output_tokens`. |

Requirements:

1. `/v1/models` must include the correct `context_length` for the active model (Composer-2.5 → **200000**). Pin `model.context_length: 200000` in the Hermes profile if a stale cache still shows 65K/256K.
2. Non-stream completions include OpenAI-shaped `usage` when the Cursor SDK returns token counts; zero/missing usage is **omitted** (not faked as zeros).
3. Streaming honors Hermes' `stream_options.include_usage` expectation by emitting a **final SSE chunk** with empty `choices` and `usage` before `[DONE]`.

Bridge logs (`run_local_timing`) also record `prompt_tokens` / `completion_tokens` / `total_tokens` for ops correlation.

## Auth

| Secret | Used by |
|---|---|
| `HERMES_CURSOR_BRIDGE_TOKEN` | Hermes → bridge Bearer token |
| `CURSOR_API_KEY` | Bridge → Cursor SDK |

Never reuse the Cursor API key as the bridge bearer token.
