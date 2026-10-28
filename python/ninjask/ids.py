"""Showdown's ids."""

from __future__ import annotations


def to_id(name: str) -> str:
    """Showdown's toID: lowercase, ASCII letters and digits only."""
    return "".join(ch for ch in name.lower() if ch.isascii() and ch.isalnum())
