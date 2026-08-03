# SDK → Hermes inventory

| Cursor SDK | Hermes surface | Notes |
|---|---|---|
| `CURSOR_API_KEY` | env + tool `check_fn` | Manifest `requires_env` is install UX only |
| `Cursor.models.list()` | `cursor_models`, bridge `/v1/models` | Parameters + variants; context unknown |
| `Cursor.repositories.list()` | `cursor_repositories` | Validate before `cursor_start` |
| Local `Agent.prompt` / create+send | `cursor_run`, `cursor_session_send` | Always wait locally |
| Cloud `Agent.create` | `cursor_start` | Detachable background |
| `Agent.resume` / get / get_run | `cursor_resume`, `cursor_status` | Local needs stored `cwd` |
| `run.cancel` | `cursor_cancel` | No-op with reason if unsupported |
| Agent archive/delete | `cursor_agent` | Delete requires confirm |
| Token usage / cloud cost | result fields | Cost may be pending |
| MCP JSON | `~/.hermes/cursor-sdk/mcp.json` | Re-pass on resume |
| Chat as Hermes brain | Phase 2 bridge | Lean Hermes toolsets required |

Out of scope for v1: ACP, Hermes core patches, cloud-as-chat-brain, webhooks, PHI/BAA.
