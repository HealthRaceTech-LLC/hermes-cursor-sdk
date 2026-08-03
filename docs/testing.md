# Testing

## Layers

| Layer | Path | CI |
|---|---|---|
| Unit | `tests/unit/` | every PR |
| Integration | `tests/integration/` | every PR |
| Contract | `tests/contract/` | every PR |
| Slow / concurrency | `@pytest.mark.slow` | `main` / release |
| Manual smoke | `tests/manual/SMOKE_CHECKLIST.md` | release |

Required CI never uses a live `CURSOR_API_KEY`. Use `tests/helpers/fake_cursor_sdk.py`.

## Local commands

```bash
source .venv/bin/activate
uv pip install -e ".[dev]"
pytest -m "not slow"          # PR-equivalent
pytest                        # include slow
ruff check src tests
ruff format --check src tests
ty check src                  # if installed
```

Coverage gate: **85%** overall (`--cov-fail-under=85`).

## Updating the SDK contract pin

1. Bump `cursor-sdk` in `pyproject.toml` within the compatible major.
2. Refresh `uv.lock` if present.
3. Run `tests/contract/test_sdk_surface.py`.
4. Update the compatibility table in `README.md` and `CHANGELOG.md`.
