Code is being re-landed via stacked PRs; full tree also at branch archive/v0.1-monolith / tag archive/v0.1-monolith.

# hermes-cursor-sdk

Unofficial, community-maintained [Hermes](https://github.com/NousResearch/hermes-agent) plugin that wraps the Python [Cursor SDK](https://cursor.com/docs/sdk/python).

**Not affiliated with, endorsed by, or maintained by Cursor or Nous Research.** Do not use Cursor or Hermes logos in a way that implies vendor endorsement.

## What it provides

| Surface | Purpose |
|---|---|
| **Phase 1 tools** (`cursor_*`) | Hermes keeps your normal chat model; call Cursor local/cloud agents as tools |
| **Phase 2 bridge** | Optional OpenAI-compatible loopback server so Cursor can act as a Hermes model provider |
| **CLI** (`hermes-cursor`) | `status`, `doctor`, `bridge`, `provider install` |

## Compatibility

| Component | Tested |
|---|---|
| Python | 3.11, 3.12, 3.13 (`>=3.11,<3.14`) |
| `cursor-sdk` | `>=1.0.26,<2` |
| Hermes | pip entry-point plugins + model-provider directory scan (see `docs/hermes-setup.md`) |
| OS smoke | macOS (developer workstation) |

## Install (keeps your Hermes tracking repo clean)

Install into Hermes' venv only — never copy plugin source into a Hermes tracking/git repo.

```bash
# 1. clone this repo somewhere outside your Hermes tracking checkout
git clone https://github.com/HealthRaceTech-LLC/hermes-cursor-sdk.git ~/Development/hermes-cursor-sdk

# 2. install into Hermes' venv
~/.hermes/hermes-agent/venv/bin/pip install -e ~/Development/hermes-cursor-sdk

# 3. enable the plugin, then enable the toolset
~/.hermes/hermes-agent/venv/bin/hermes plugins enable cursor
# hermes tools  →  enable toolset "cursor" for CLI / gateway / cron as needed

# 4. credentials (Hermes .env only — never commit)
# ~/.hermes/.env  →  CURSOR_API_KEY=...

# 5. restart Hermes / gateway
~/.hermes/hermes-agent/venv/bin/hermes-cursor doctor
```

Verify discovery:

```bash
HERMES_PLUGINS_DEBUG=1 ~/.hermes/hermes-agent/venv/bin/hermes plugins list
```

Full steps: [docs/hermes-setup.md](docs/hermes-setup.md).

## Tools (Phase 1)

| Tool | Use when |
|---|---|
| `cursor_models` | List Cursor models / parameters |
| `cursor_repositories` | List SCM repos before cloud start |
| `cursor_run` | Quick **local** one-shot (always waits) |
| `cursor_start` | **Cloud** background / cron / PR work |
| `cursor_status` / `cursor_cancel` / `cursor_resume` | Inspect or continue agents |
| `cursor_session_send` | Multi-turn local collaboration |
| `cursor_agent` | List / archive / delete lifecycle |

Local background execution is intentionally unsupported. Detachable work must use `cursor_start` (cloud).

## Chat-provider mode (Phase 2)

```bash
export HERMES_CURSOR_BRIDGE_TOKEN="$(openssl rand -hex 32)"
export HERMES_CURSOR_BRIDGE_CWD="/path/to/project"
hermes-cursor-bridge &
hermes-cursor provider install
# hermes model → provider "cursor"
# Disable Hermes file/terminal toolsets; pin aux models away from Cursor
hermes-cursor doctor --provider-mode
```

Details and warnings: [docs/chat-provider-mode.md](docs/chat-provider-mode.md).

## Cron / subagents

- Use **agent-mode** cron that calls `cursor_start` / `cursor_status`.
- Enable toolset `cursor` for the **cron** platform in `hermes tools`.
- Do **not** use script-only cron for SDK calls — Hermes sanitizes provider credentials from those subprocesses.
- Prefer cloud IDs for work that must survive process exit.

## PHI / secrets

- **Unsupported for PHI / BAA workloads.** Treat Cursor cloud/local agents as non-PHI.
- Keep `CURSOR_API_KEY` in `~/.hermes/.env` (mode `0600`). Never pass secret values through model-authored tool args.
- Bridge bearer token (`HERMES_CURSOR_BRIDGE_TOKEN`) is separate from `CURSOR_API_KEY`.

## Development

```bash
uv venv && source .venv/bin/activate
uv pip install -e ".[dev]"
pytest -m "not slow"
ruff check src tests
ruff format --check src tests
```

Testing layers: [docs/testing.md](docs/testing.md). Manual release smoke: [tests/manual/SMOKE_CHECKLIST.md](tests/manual/SMOKE_CHECKLIST.md).

## License

MIT — see [LICENSE](LICENSE).
