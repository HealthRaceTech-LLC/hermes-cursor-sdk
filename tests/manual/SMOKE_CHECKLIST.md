# Manual Smoke Checklist

## Phase 1: Offline Package Checks

- Create and activate a Python 3.11+ virtual environment.
- Install the package with `python -m pip install -e '.[dev]'`.
- Run `pytest -m "not slow" -q --cov-fail-under=0` and confirm the mocked suite is green.
- Confirm `hermes-cursor --help` exits successfully.
- Confirm `hermes-cursor status` prints bridge/provider/service status with no traceback.
- Confirm `hermes-cursor doctor --provider-mode` reports provider metadata.
- Confirm `hermes-cursor-bridge --help` exits successfully.
- Confirm Hermes can discover the `hermes_agent.plugins:cursor` entry point.

## Phase 2: Credentialed Local Smoke

- Export a non-production `CURSOR_API_KEY`.
- Export `HERMES_CURSOR_BRIDGE_TOKEN` to a local-only test value.
- Start the bridge with `hermes-cursor bridge -- --port 8787 --token "$HERMES_CURSOR_BRIDGE_TOKEN"`.
- Call `GET /healthz` and confirm a 200 response.
- Call `GET /v1/models` with `Authorization: Bearer $HERMES_CURSOR_BRIDGE_TOKEN`.
- Send a small `/v1/chat/completions` request with `cursor.cwd` set to a disposable repository.
- Repeat the chat request with a stable `cursor.session_id` and confirm the session continues.
- Install the provider shim with `hermes-cursor provider install`, verify Hermes sees the `cursor` provider, then uninstall or leave it managed.

