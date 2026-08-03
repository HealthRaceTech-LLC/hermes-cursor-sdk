# Changelog

## Unreleased

### Fixed

- Local chat via the bridge no longer crashes with `'NoneType' object is not iterable` when building `ModelSelection` with empty params
- Bridge ignores OpenAI sampling knobs (`temperature`, `top_p`, …) so Hermes Desktop/CLI defaults do not fail Cursor catalog validation
- Extract assistant text from `cursor_sdk.RunResult.result` so chat completions return the model reply
- Strip Hermes `tools` / `tool_choice` on chat completions instead of HTTP 400 so Desktop/CLI can use Cursor as the chat brain
- Stop stuffing Hermes transport fields (`base_url`, `model`, …) into `cursor.params` via `build_extra_body`
- Ignore Hermes OpenAI knobs that are not in the Cursor model catalog (e.g. `max_tokens` on `composer-2.5`)
- Buffer SSE chat streams with `Content-Length` so Hermes' OpenAI client no longer sees empty 200 responses
- Do not map Hermes `session_id` onto Cursor agent sessions in v1 (stateless completions); clamp SDK `status_code=200` failures to HTTP 502
- Emit OpenAI-shaped `usage` (including SSE final usage chunk) so Hermes CLI/Desktop context meters get `prompt_tokens`
- Advertise per-model Cursor context windows on `/v1/models` (Composer 200K; switchable models use max, e.g. 1M) with `context_options`

## 0.1.0 — 2026-08-03

### Added

- Phase 1 Hermes tools: `cursor_models`, `cursor_repositories`, `cursor_run`, `cursor_start`, `cursor_status`, `cursor_resume`, `cursor_cancel`, `cursor_session_send`, `cursor_agent`
- Shared `CursorSDKClient` with SQLite state store, error mapping, and fake-SDK unit tests
- Phase 2 OpenAI-compatible loopback bridge (`hermes-cursor-bridge`) and model-provider shim (`hermes-cursor provider install`)
- CLI: `status`, `doctor`, `bridge`, `provider`, `service`
- CI: lint, Python 3.11–3.13 tests (85% coverage), packaging, entry-point compat, pip-audit
