# Changelog

## 0.1.0 — 2026-08-03

### Added

- Phase 1 Hermes tools: `cursor_models`, `cursor_repositories`, `cursor_run`, `cursor_start`, `cursor_status`, `cursor_resume`, `cursor_cancel`, `cursor_session_send`, `cursor_agent`
- Shared `CursorSDKClient` with SQLite state store, error mapping, and fake-SDK unit tests
- Phase 2 OpenAI-compatible loopback bridge (`hermes-cursor-bridge`) and model-provider shim (`hermes-cursor provider install`)
- CLI: `status`, `doctor`, `bridge`, `provider`, `service`
- CI: lint, Python 3.11–3.13 tests (85% coverage), packaging, entry-point compat, pip-audit
