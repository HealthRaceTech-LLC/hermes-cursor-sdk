"""Bridge server stub for staged re-land."""

from __future__ import annotations

import argparse


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="hermes-cursor-bridge",
        description="Hermes Cursor SDK bridge placeholder for the scaffold package.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    try:
        parser.parse_args(argv)
    except SystemExit as exc:
        if exc.code == 0:
            return 0
        raise
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
