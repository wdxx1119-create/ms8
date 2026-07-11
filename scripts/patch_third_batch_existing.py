from __future__ import annotations

import re
import textwrap
from pathlib import Path

ROOT = Path.cwd()


def read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def write(path: str, content: str) -> None:
    target = ROOT / path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content, encoding="utf-8")


def replace_once(path: str, old: str, new: str) -> None:
    text = read(path)
    if old not in text:
        raise RuntimeError(f"anchor not found in {path}: {old[:160]!r}")
    write(path, text.replace(old, new, 1))


def sub_once(path: str, pattern: str, replacement: str, flags: int = 0) -> None:
    text = read(path)
    output, count = re.subn(pattern, lambda _match: replacement, text, count=1, flags=flags)
    if count != 1:
        raise RuntimeError(f"pattern count={count} in {path}: {pattern[:160]!r}")
    write(path, output)


path = "src/ms8/record_policy.py"
replace_once(
    path,
    "from uuid import uuid4\n",
    "from uuid import uuid4\n\nfrom .memory_safety import (\n"
    "    build_memory_provenance,\n"
    "    normalize_memory_provenance,\n"
    "    validate_memory_provenance,\n"
    ")\n",
)
sub_once(
    path,
    r"^def build_canonical_record\(.*?\n(?=def validate_record)",
    textwrap.dedent(
        '''\
def build_canonical_record(text: str, source: str, status: str = "accepted") -> dict[str, Any]:
    payload = infer_scope_flags(text=text, source=source)
    normalized = normalize_text(text)
    low = normalize_text(text).lower()
    record_id = str(uuid4())
    created_at = _utc_now()
    normalized_status = status if status in ALLOWED_STATUS else "candidate"
    if payload["scope"] == "labs":
        category = "experimental_note"
    elif payload["scope"] == "system_debug":
        category = "system_diagnostic"
    elif payload["scope"] == "project" and any(k in low for k in PRODUCT_DECISION_KEYWORDS):
        category = "product_decision"
    elif any(k in low for k in PREFERENCE_KEYWORDS):
        category = "user_preference"
    else:
        category = "general"
    return {
        "id": record_id,
        "text": normalized,
        "normalized_text": normalized,
        "category": category,
        "status": normalized_status,
        "source": source,
        "created_at": created_at,
        "meta": {"admission": "ms8_write_guard_v1"},
        "provenance": build_memory_provenance(
            text=normalized,
            source=source,
            record_id=record_id,
            authority=payload["authority"],
            status=normalized_status,
            created_at=created_at,
        ),
        **payload,
    }


'''
    ),
    flags=re.S | re.M,
)
replace_once(
    path,
    '    if scope == "system_debug" and record.get("can_inject") is True:\n'
    '        return False, "invalid:debug_can_inject"\n'
    '    return True, "ok"\n',
    '    if scope == "system_debug" and record.get("can_inject") is True:\n'
    '        return False, "invalid:debug_can_inject"\n'
    '    if "provenance" in record:\n'
    '        provenance_ok, provenance_reason = validate_memory_provenance(record, record.get("provenance"))\n'
    '        if not provenance_ok:\n'
    '            return False, provenance_reason\n'
    '    return True, "ok"\n',
)
replace_once(
    path,
    "    updated = 0\n    system_debug = 0\n",
    "    updated = 0\n    provenance_backfilled = 0\n    system_debug = 0\n",
)
replace_once(
    path,
    '        if "migration_version" not in row:\n'
    '            row["migration_version"] = "p0_2_v1"\n'
    "            changed = True\n\n"
    "        # Fix illegal combination: system_debug should not inject/act.\n",
    '        if "migration_version" not in row:\n'
    '            row["migration_version"] = "p0_2_v1"\n'
    "            changed = True\n\n"
    "        normalized_provenance = normalize_memory_provenance(row)\n"
    '        if row.get("provenance") != normalized_provenance:\n'
    '            row["provenance"] = normalized_provenance\n'
    "            provenance_backfilled += 1\n"
    "            changed = True\n\n"
    "        # Fix illegal combination: system_debug should not inject/act.\n",
)
replace_once(
    path,
    '        "migration_version",\n    ]\n',
    '        "migration_version",\n        "provenance",\n    ]\n',
)
replace_once(
    path,
    '        "updated": updated,\n        "system_debug": system_debug,\n',
    '        "updated": updated,\n'
    '        "provenance_backfilled": provenance_backfilled,\n'
    '        "system_debug": system_debug,\n',
)

path = "src/ms8/engine.py"
replace_once(
    path,
    "from .record_policy import append_canonical_record\n",
    "from .memory_safety import evaluate_memory_policy, pre_action_check as evaluate_pre_action_check\n"
    "from .record_policy import append_canonical_record\n",
)
sub_once(
    path,
    r"^    def _filter_rows_by_policy\(.*?^    def _exact_fallback_matches\(",
    textwrap.indent(
        textwrap.dedent(
            '''\
def _filter_rows_by_policy(
    self,
    rows: list[dict[str, Any]],
    *,
    query: str,
    purpose: str,
    limit: int,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    allowed: list[dict[str, Any]] = []
    blocked = 0
    reason_counts: dict[str, int] = {}
    decisions: list[dict[str, Any]] = []
    lane = "inject" if str(purpose or "").strip().lower() == "inject" else "recall"
    for row in rows:
        decision = evaluate_memory_policy(row, query=query, purpose=lane)
        decisions.append(decision)
        if bool(decision.get("allowed", False)):
            allowed.append(row)
        else:
            blocked += 1
            reason_codes = decision.get("reason_codes", [])
            if not isinstance(reason_codes, list) or not reason_codes:
                reason_codes = ["policy_blocked"]
            for reason in reason_codes:
                key = str(reason)
                reason_counts[key] = reason_counts.get(key, 0) + 1
    return allowed[:limit], {
        "purpose": lane,
        "candidate_total": len(rows),
        "allowed_total": len(allowed),
        "blocked_total": blocked,
        "reason_counts": dict(sorted(reason_counts.items())),
        "low_confidence_refusal": reason_counts.get("low_confidence", 0),
        "record_decisions": decisions[: min(len(decisions), 50)],
    }


def pre_action_check(
    self,
    action: str,
    *,
    memory_ids: list[str] | None = None,
    explicit_user_confirmation: bool = False,
) -> dict[str, Any]:
    return evaluate_pre_action_check(
        action=action,
        records=self.read_memories(),
        memory_ids=memory_ids,
        explicit_user_confirmation=explicit_user_confirmation,
    )


def _exact_fallback_matches(
'''
        ),
        "    ",
    ),
    flags=re.S | re.M,
)
replace_once(
    path,
    '            "can_recall": row.get("can_recall", True),\n'
    '            "can_inject": row.get("can_inject", True),\n'
    '            "superseded_by": str(row.get("superseded_by") or ""),\n',
    '            "can_recall": row.get("can_recall", True),\n'
    '            "can_inject": row.get("can_inject", True),\n'
    '            "can_act_on": row.get("can_act_on", False),\n'
    '            "provenance": dict(row.get("provenance", {})) '
    'if isinstance(row.get("provenance", {}), dict) else {},\n'
    '            "superseded_by": str(row.get("superseded_by") or ""),\n',
)

path = "src/ms8/connect/mcp_server/memory_access_policy.py"
replace_once(
    path,
    "from typing import Any\n",
    "from typing import Any\n\nfrom ...memory_safety import evaluate_memory_policy\n",
)
sub_once(
    path,
    r"^def memory_row_browsable\(.*?\n(?=def redact_memory_row)",
    'def memory_row_browsable(row: dict[str, Any]) -> bool:\n'
    '    """Return whether a record is safe for default explicit MCP browsing."""\n\n'
    '    return bool(evaluate_memory_policy(row, query="", purpose="recall").get("allowed", False))\n\n\n',
    flags=re.S | re.M,
)

path = "src/ms8/connect/mcp_server/memory_service_interface.py"
replace_once(
    path,
    "    def query(self, text: str, top_k: int = 5) -> dict[str, Any]:\n",
    "    def pre_action_check(\n"
    "        self,\n"
    "        action: str,\n"
    "        *,\n"
    "        memory_ids: list[str] | None = None,\n"
    "        explicit_user_confirmation: bool = False,\n"
    "    ) -> dict[str, Any]:\n"
    "        return self._engine_adapter().pre_action_check(\n"
    "            action,\n"
    "            memory_ids=memory_ids,\n"
    "            explicit_user_confirmation=explicit_user_confirmation,\n"
    "        )\n\n"
    "    def query(self, text: str, top_k: int = 5) -> dict[str, Any]:\n",
)

path = "src/ms8/connect/mcp_server/mcp_server.py"
replace_once(
    path,
    '    "prepare_reply",\n    "submit",\n',
    '    "prepare_reply",\n    "pre_action_check",\n    "submit",\n',
)
replace_once(
    path,
    '        payload.setdefault("source", _source_tag("submit"))\n',
    '        payload.setdefault("source", _source_tag(_get_client_name(p)))\n',
)
replace_once(
    path,
    '            payload.setdefault("source", _source_tag("batch_submit"))\n',
    '            payload.setdefault("source", _source_tag(_get_client_name(p)))\n',
)
replace_once(
    path,
    '    if tool == "query":\n'
    '        out = svc.query(str(p.get("text") or p.get("query") or ""), int(p.get("top_k", 5) or 5))\n',
    '''    if tool == "pre_action_check":
        raw_ids = p.get("memory_ids", [])
        if raw_ids is None:
            raw_ids = []
        if not isinstance(raw_ids, list):
            out = {
                "ok": False,
                "status": "invalid_request",
                "error": "memory_ids_must_be_array",
                "error_code": "E_MCP_INVALID_ACTION_CHECK",
            }
            _audit("pre_action_check", False, {"client": _get_client_name(p)})
            return out
        out = svc.pre_action_check(
            str(p.get("action") or p.get("intent") or ""),
            memory_ids=[str(item) for item in raw_ids],
            explicit_user_confirmation=_as_bool(p.get("explicit_user_confirmation", False)),
        )
        _audit(
            "pre_action_check",
            bool(out.get("ok", False)),
            {
                "client": _get_client_name(p),
                "decision": out.get("decision"),
                "allowed": out.get("allowed"),
            },
        )
        return out
    if tool == "query":
        out = svc.query(str(p.get("text") or p.get("query") or ""), int(p.get("top_k", 5) or 5))
''',
)

path = "src/ms8/connect/mcp_server/stdio_server.py"
replace_once(
    path,
    '    "query": {\n        "description": "Search memories by query text.",\n',
    '''    "pre_action_check": {
        "description": (
            "Evaluate whether selected memory records may support a proposed external action. "
            "This tool never executes the action and requires explicit human confirmation before allowing it."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "action": {"type": "string", "description": "Proposed external action or intent."},
                "memory_ids": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Optional memory record IDs used as supporting evidence.",
                },
                "explicit_user_confirmation": {
                    "type": "boolean",
                    "default": False,
                    "description": "True only when the user explicitly confirmed this exact action.",
                },
            },
            "required": ["action"],
            "additionalProperties": True,
        },
    },
    "query": {
        "description": "Search memories by query text.",
''',
)

path = "src/ms8/recovery.py"
sub_once(
    path,
    r"^def _atomic_copy\(.*?\n(?=def restore_runtime_backup)",
    '''def _atomic_copy(source: Path, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_name(f".{destination.name}.{os.getpid()}.restore-tmp")
    try:
        shutil.copy2(source, temporary)
        os.replace(temporary, destination)
    finally:
        temporary.unlink(missing_ok=True)


def _append_restore_audit(target: Path, event: dict[str, Any]) -> None:
    audit_path = target / "memory" / "logs" / "restore_audit.jsonl"
    audit_path.parent.mkdir(parents=True, exist_ok=True)
    with audit_path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(event, ensure_ascii=False, sort_keys=True) + "\n")
        handle.flush()
        os.fsync(handle.fileno())


''',
    flags=re.S | re.M,
)
sub_once(
    path,
    r'    with tempfile\.TemporaryDirectory\(prefix="ms8-restore-"\).*?'
    r'    return \{\*\*plan, "ok": True, "applied": True, "dry_run": False, '
    r'"pre_restore_backup": pre_restore_backup\}\n',
    '''    try:
        with tempfile.TemporaryDirectory(prefix="ms8-restore-") as temp_dir:
            stage = Path(temp_dir)
            with zipfile.ZipFile(Path(archive).expanduser().resolve(), "r") as bundle:
                for row in rows:
                    relative = _relative_path(str(row["path"]))
                    member = f"runtime/{relative.as_posix()}"
                    staged_file = stage / relative
                    staged_file.parent.mkdir(parents=True, exist_ok=True)
                    staged_file.write_bytes(bundle.read(member))
                    if _sha256(staged_file) != str(row["sha256"]):
                        return {
                            **plan,
                            "ok": False,
                            "applied": False,
                            "error": f"staged_checksum_mismatch:{relative}",
                            "pre_restore_backup": pre_restore_backup,
                        }
            for row in rows:
                relative = _relative_path(str(row["path"]))
                destination = _safe_destination(target, relative)
                _atomic_copy(stage / relative, destination)
    except (OSError, ValueError, zipfile.BadZipFile) as exc:
        failure_event = {
            "event": "runtime_restore_failed",
            "at": _utc_now(),
            "archive": str(Path(archive).expanduser().resolve()),
            "archive_sha256": verification.get("archive_sha256", ""),
            "pre_restore_backup": pre_restore_backup,
            "error_type": type(exc).__name__,
            "error": str(exc),
        }
        _append_restore_audit(target, failure_event)
        return {
            **plan,
            "ok": False,
            "applied": False,
            "dry_run": False,
            "error": f"restore_apply_failed:{type(exc).__name__}",
            "pre_restore_backup": pre_restore_backup,
        }

    event = {
        "event": "runtime_restore",
        "at": _utc_now(),
        "archive": str(Path(archive).expanduser().resolve()),
        "archive_sha256": verification.get("archive_sha256", ""),
        "pre_restore_backup": pre_restore_backup,
        "created": len(plan["create"]),
        "overwritten": len(plan["overwrite"]),
        "unchanged": len(plan["unchanged"]),
    }
    _append_restore_audit(target, event)
    return {**plan, "ok": True, "applied": True, "dry_run": False, "pre_restore_backup": pre_restore_backup}
''',
    flags=re.S,
)

path = "docs/DATA_MODEL.md"
marker = (
    "Additional engine or migration fields may be present. Consumers must tolerate additive fields "
    "and should not discard unknown metadata during repair or migration.\n"
)
section = '''Additional engine or migration fields may be present. Consumers must tolerate additive fields and should not discard unknown metadata during repair or migration.

### Provenance object

New canonical records include a `provenance` object with a content digest, source kind/reference, creator/recorder classes, observation and recording timestamps, validity interval, parent record IDs, transformation chain, verification state, confidence, and provenance schema version.

Provenance is additive for backward compatibility: older records remain readable, while repair/backfill can add the object idempotently. A provenance object whose content digest does not match the canonical text is invalid and must not authorize recall, injection, or action.

`confidence` is evidence quality, not action permission. `can_act_on` remains independently false by default, and only a verified, explicit-user-authorized record with explicit confirmation may pass the pre-action gate.
'''
replace_once(path, marker, section)
