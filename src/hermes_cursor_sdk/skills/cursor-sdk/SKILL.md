# Cursor SDK

Use this skill when Hermes should route work to Cursor through the `cursor` toolset.

## Decision Tree

1. Use `cursor_run` for a one-shot local task in an already available checkout.
2. Use `cursor_start` for a new cloud agent that needs repository metadata.
3. Use `cursor_session_send` when Hermes is carrying a multi-turn Cursor session.
4. Use `cursor_resume` only when the target `agent_id` is already known and the work belongs to that agent.
5. Use `cursor_status`, `cursor_cancel`, or `cursor_agent` for lifecycle and inspection tasks.

## Working Directory And Repository Rules

- `cursor_run` requires `cwd`; it operates against local files only.
- `cursor_session_send` requires `cwd` on the first turn unless an `agent_id` is already supplied.
- `cursor_session_send` must have Hermes `session_id` or `task_id`; if those are unavailable, provide a stable `session_tag`.
- `cursor_start` is cloud only and requires `repos` entries with `url` and `starting_ref`.
- Use `pr_url` only when continuing work from an existing pull request.

## Safety Rules

- Do not send PHI, secrets, credentials, access tokens, or private customer data in prompts, params, repository URLs, environment names, or metadata.
- Warn users before routing a chat-provider style request to Cursor: Cursor agents can inspect and modify code, so the request should be treated as an agent task rather than ordinary chat.
- For cron or scheduled Hermes tasks, confirm the `cursor` toolset is enabled before relying on these tools.
- Cron jobs must run in agent mode only; do not schedule plan-mode Cursor runs.
