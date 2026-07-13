"""Explicit CLI for an already-authorized ledger-v1 runtime."""

from __future__ import annotations

import json
from argparse import Namespace
from pathlib import Path
from typing import Any

from ..main_cli_bridge import run_memory_ledger_operations
from .memory_service import LedgerCompatibilityError, build_ledger_memory_compatibility_adapter


def _optional_text(args: Namespace, name: str) -> str | None:
    value = str(getattr(args, name, "") or "").strip()
    return value or None


def _read_options(args: Namespace) -> dict[str, Any]:
    return {
        "recorded_as_of": _optional_text(args, "recorded_as_of"),
        "observed_as_of": _optional_text(args, "observed_as_of"),
        "valid_at": _optional_text(args, "valid_at"),
        "realm_id": _optional_text(args, "realm_id"),
        "scope": _optional_text(args, "scope"),
    }


def run_memory_ledger_cli(args: Namespace) -> int:
    """Run explicit ledger-v1 read or guarded operational commands."""

    command = str(getattr(args, "memory_ledger_cmd", "") or "").strip()
    if command in {"doctor", "rebuild", "mutate", "migrate"}:
        return run_memory_ledger_operations(args)

    workspace = Path(str(getattr(args, "workspace", "") or "")).expanduser()
    if not str(workspace).strip():
        print(json.dumps({"ok": False, "error": "workspace_required"}, ensure_ascii=False, indent=2))
        return 2

    retrieval_profile = str(
        getattr(args, "retrieval_profile", "legacy") or "legacy"
    ).strip()
    config: dict[str, Any] = {
        "memory_ledger_v1": {
            "enabled": True,
            "retrieval_profile": retrieval_profile,
        }
    }
    try:
        adapter = build_ledger_memory_compatibility_adapter(config, workspace)
        if adapter is None:
            raise LedgerCompatibilityError("ledger-v1 compatibility adapter was not enabled")

        if command == "status":
            out: dict[str, Any] = {"ok": True, **adapter.status()}
        elif command == "query":
            options = _read_options(args)
            purpose = str(getattr(args, "purpose", "recall") or "recall").strip()
            if purpose != "recall":
                options["purpose"] = purpose
            if bool(getattr(args, "explain", False)):
                options["explain"] = True
            out = adapter.query(
                str(getattr(args, "text", "") or ""),
                int(getattr(args, "limit", 5) or 5),
                **options,
            )
        elif command == "context":
            options = _read_options(args)
            if bool(getattr(args, "explain", False)):
                options["explain"] = True
            out = adapter.context(
                str(getattr(args, "text", "") or ""),
                int(getattr(args, "limit", 5) or 5),
                **options,
            )
        elif command == "explain":
            out = adapter.explain(str(getattr(args, "claim_id", "") or ""))
        else:
            out = {
                "ok": False,
                "error": "memory_ledger_command_required",
                "allowed": ["doctor", "status", "query", "context", "explain", "rebuild", "mutate", "migrate"],
            }
    except (LedgerCompatibilityError, OSError, RuntimeError, TypeError, ValueError) as exc:
        out = {
            "ok": False,
            "error": str(exc),
            "error_code": "E_LEDGER_V1_CLI_FAILED",
        }

    print(json.dumps(out, ensure_ascii=False, indent=2))
    return 0 if bool(out.get("ok", False)) else 1


__all__ = ["run_memory_ledger_cli"]
