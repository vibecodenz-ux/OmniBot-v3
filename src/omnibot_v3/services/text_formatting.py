"""Shared lightweight text-formatting helpers for operator-facing copy."""

from __future__ import annotations


def sentence_case(value: str | None) -> str | None:
    if value is None:
        return None
    return value[:1].upper() + value[1:]


def title_label(value: str) -> str:
    return value.replace("_", " ").replace("-", " ").title()