"""HTTP API adapters for OmniBot v3."""

from __future__ import annotations

from typing import Any

__all__ = ["create_app"]


def __getattr__(name: str) -> Any:
	if name == "create_app":
		from omnibot_v3.api.app import create_app

		return create_app
	raise AttributeError(f"module {__name__!r} has no attribute {name!r}")