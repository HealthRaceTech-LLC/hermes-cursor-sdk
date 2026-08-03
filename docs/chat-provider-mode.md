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
