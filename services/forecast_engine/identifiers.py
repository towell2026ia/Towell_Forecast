"""Stable identifiers for generic runs, vintages and research evidence."""

from __future__ import annotations

import re


def scope_token(scope: str | None) -> str:
    token = re.sub(r"[^A-Z0-9]+", "-", (scope or "ALL").upper()).strip("-")
    return token or "ALL"


def run_id(scope: str | None, period: str, sequence: int) -> str:
    return f"RUN-{scope_token(scope)}-{period}-{sequence:03d}"


def vintage_id(run: str) -> str:
    return f"V-{run}"


def research_id(scope: str | None, period: str, digest: str = "") -> str:
    suffix = f"-{digest[:12]}" if digest else ""
    return f"RS-{scope_token(scope)}-{period}{suffix}"


def model_version(kind: str, scope: str, period: str, sequence: int) -> str:
    if kind not in {"ST", "ML", "FT"}:
        raise ValueError("invalid_model_kind")
    return f"{kind}-{scope_token(scope)}-{period.replace('-', '')}-V{sequence:02d}"
