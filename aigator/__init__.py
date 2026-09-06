"""Aigator: Local-first multi-platform AI session aggregator & context search engine."""

from aigator.db import Database
from aigator.models import CanonicalMessage, CanonicalSession, Role, SearchResult, Source

__version__ = "0.1.0"
__all__ = [
    "Database",
    "CanonicalSession",
    "CanonicalMessage",
    "SearchResult",
    "Source",
    "Role",
]

