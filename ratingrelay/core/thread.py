"""Backwards-compatible import: the helper now lives in the engine."""

from ratingrelay.engine.thread import run_sync

__all__ = ["run_sync"]
