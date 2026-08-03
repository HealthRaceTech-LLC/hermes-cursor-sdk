# Hermes setup

Install this connector into Hermes without modifying any Hermes git/tracking repository.

## Prerequisites

- Hermes agent with a virtualenv (typically `~/.hermes/hermes-agent/venv`)
- Python matching Hermes (`>=3.11,<3.14`)
- Cursor user or service-account API key

## Phase 1 — tools plugin

```bash
git clone https://github.com/HealthRaceTech-LLC/hermes-cursor-sdk.git ~/Development/hermes-cursor-sdk
~/.hermes/hermes-agent/venv/bin/pip install -e ~/Development/hermes-cursor-sdk

# quit Hermes GUI / gateway first if it is running
~/.hermes/hermes-agent/venv/bin/hermes plugins enable cursor
```

Add to `~/.hermes/.env` (never commit this file):

```bash
CURSOR_API_KEY=crsr_...
# optional
HERMES_CURSOR_DEFAULT_MODEL=composer-2.5
```

Enable the toolset (required — plugin enable alone is not enough):

1. Run `hermes tools`
2. Enable toolset `cursor` for each platform you need: CLI, gateway, and/or **cron**
3. Restart Hermes / gateway

Verify:

```bash
HERMES_PLUGINS_DEBUG=1 ~/.hermes/hermes-agent/venv/bin/hermes plugins list
~/.hermes/hermes-agent/venv/bin/hermes-cursor doctor
```

Smoke from chat (with the toolset enabled):

1. Call `cursor_models`
2. Call `cursor_run` with an absolute `cwd` pointing at a scratch git checkout

## Phase 2 — chat provider (optional)

Required for Cursor to appear in Hermes Desktop’s model/provider picker.

```bash
# Installs into the active profile home (or pass --profile)
hermes-cursor setup --cwd /absolute/path/to/project --load-service
```

Then:

1. Restart Hermes Desktop / gateway
2. `hermes model` (or Desktop Settings → Providers) → **Cursor (SDK bridge)** + a model from `/v1/models`
3. Disable Hermes toolsets that would nest file/terminal actions under Cursor
4. Pin every Hermes auxiliary slot (compression, vision, memory, etc.) to a normal provider — not Cursor
5. `hermes-cursor doctor --provider-mode`
6. Confirm `hermes doctor` can probe the bridge `/models` endpoint

`setup` / `provider install` write **two** things into the profile home:

1. Plugin shim: `$HERMES_HOME/plugins/model-providers/cursor/` (for profile `co-cto`, `~/.hermes/profiles/co-cto/plugins/model-providers/cursor/`). Discovered by Hermes' model-provider scanner — **do not** add `cursor-provider` to `plugins.enabled`.
2. Config entry: `$HERMES_HOME/config.yaml` → `providers.cursor` with `api` + `key_env`. **Required for Desktop/CLI model switch** — Hermes' picker can list plugin providers, but `resolve_provider_full` (model switch) only accepts models.dev / overlays / `config.yaml` `providers:`. Without this entry you get `Unknown provider 'cursor'`.

## Config files (optional)

| Path | Purpose |
|---|---|
| `~/.hermes/cursor-sdk/config.toml` | Defaults (model, allowlists, `auto_create_pr`, …) |
| `~/.hermes/cursor-sdk/mcp.json` | Inline MCP servers passed to Cursor agents |
| `~/.hermes/cursor-sdk/bridge.env` | Strict `KEY=VALUE` env for the bridge service (`0600`) |
| `~/.hermes/cache/cursor-sdk/state.sqlite3` | Agent / run / session state |

## Uninstall

```bash
hermes-cursor provider uninstall   # Phase 2 shim only
~/.hermes/hermes-agent/venv/bin/hermes plugins disable cursor
~/.hermes/hermes-agent/venv/bin/pip uninstall hermes-cursor-sdk
```
