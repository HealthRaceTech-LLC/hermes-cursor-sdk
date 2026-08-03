# Contributing

Thanks for considering a contribution to `hermes-cursor-sdk`.

## Development

```bash
python -m venv .venv
source .venv/bin/activate
python -m pip install -e '.[dev]'
pre-commit install
pytest
```

Please keep changes small, tested, and documented when they alter public behavior.
