"""Public package metadata for hermes-cursor-sdk."""

from importlib.metadata import PackageNotFoundError, version

try:
    __version__ = version("hermes-cursor-sdk")
except PackageNotFoundError:  # pragma: no cover - package is editable during local tests
    __version__ = "0.0.0"

__all__ = ["__version__"]
