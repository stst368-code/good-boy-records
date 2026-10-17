"""Shared GBR database foundation. Importing this package never opens a database."""

from .migration import inspect_sources, migrate

__all__ = ["inspect_sources", "migrate"]
