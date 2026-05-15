from __future__ import annotations

from typing import Any, Dict

from ..self_check.check_specs import build_check_specs


def run_check_once(core: Any, check_id: str) -> Dict[str, Any]:
    specs = build_check_specs(level="FULL_PLUS")
    spec = next((s for s in specs if s.check_id == check_id), None)
    if spec is None:
        return {"status": "skipped", "message": "check_not_found", "check_id": check_id}
    try:
        out = spec.fn(core, {})
        if not isinstance(out, dict):
            return {"status": "error", "message": "invalid_check_result", "check_id": check_id}
        out.setdefault("status", "error")
        out["check_id"] = check_id
        out["level"] = spec.level
        out["domain"] = spec.domain
        return out
    except Exception as exc:
        return {"status": "error", "message": f"exception:{exc}", "check_id": check_id}


def verify_repair(core: Any, check_id: str) -> Dict[str, Any]:
    out = run_check_once(core, check_id)
    st = str(out.get("status", "error"))
    if st == "pass":
        return {"ok": True, "status": "pass", "verify": out}
    if st == "warn":
        return {"ok": True, "status": "warn", "verify": out, "partial": True}
    return {"ok": False, "status": st, "verify": out}
