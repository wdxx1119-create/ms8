"""Access to the published ledger-v1 JSON Schema."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

SCHEMA_FILENAME = "ms8-ledger-v1.schema.json"


def ledger_schema_path() -> Path:
    """Return the repository/package path for the ledger-v1 JSON Schema."""

    return Path(__file__).with_name("schemas") / SCHEMA_FILENAME


def load_ledger_schema() -> dict[str, Any]:
    """Load and minimally validate the published ledger-v1 JSON Schema."""

    path = ledger_schema_path()
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise TypeError("ledger schema must be a JSON object")
    if payload.get("$schema") != "https://json-schema.org/draft/2020-12/schema":
        raise ValueError("unsupported ledger schema dialect")
    definitions = payload.get("$defs")
    if not isinstance(definitions, dict) or "ledgerTransaction" not in definitions:
        raise ValueError("ledger schema is missing ledgerTransaction")
    return payload


__all__ = ["SCHEMA_FILENAME", "ledger_schema_path", "load_ledger_schema"]
