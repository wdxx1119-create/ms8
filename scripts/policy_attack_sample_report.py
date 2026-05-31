#!/usr/bin/env python3
from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

from ms8.engine_core.policy_engine_loader import get_policy_engine
from ms8.paths import get_health_dir


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _to_aware(text: str) -> datetime | None:
    raw = str(text or "").strip()
    if not raw:
        return None
    try:
        dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _run_cases() -> dict[str, Any]:
    engine = get_policy_engine()
    cases: list[dict[str, Any]] = []

    def add(name: str, passed: bool, detail: str = "") -> None:
        cases.append({"name": name, "passed": bool(passed), "detail": detail})

    out1 = engine.evaluate_admission({"text": "ok"})
    route1 = str((out1.get("data") or {}).get("route", ""))
    add("noise_ack_blocked", route1 in {"rejected", "short_term_only"}, f"route={route1}")

    out2 = engine.evaluate_admission({"text": "password=SuperSecret123"})
    data2 = out2.get("data") or {}
    route2 = str(data2.get("route", ""))
    flags2 = set(data2.get("privacy_flags", []) if isinstance(data2.get("privacy_flags", []), list) else [])
    add(
        "password_pending_review",
        route2 == "pending_review" and ("password_field" in flags2 or "password_cn" in flags2),
        f"route={route2},flags={sorted(flags2)}",
    )

    out3 = engine.rank_retrieval(
        {
            "candidates": [
                {"id": "revoked-primary", "status": "revoked", "usage_permission": {"inject": "primary", "speak": "primary"}},
                {"id": "accepted-primary", "status": "accepted", "usage_permission": {"inject": "primary", "speak": "primary"}},
            ],
            "budget": {"budget_top_k": 5},
        }
    )
    data3 = out3.get("data") or {}
    items3 = data3.get("items", []) if isinstance(data3.get("items", []), list) else []
    ids3 = [str(x.get("id", "")) for x in items3 if isinstance(x, dict)]
    add("revoked_injection_blocked", ids3 == ["accepted-primary"], f"items={ids3}")

    passed = sum(1 for c in cases if c["passed"])
    failed = len(cases) - passed
    return {"cases": cases, "total_cases": len(cases), "passed_cases": passed, "failed_cases": failed, "ok": failed == 0}


def main() -> int:
    health_dir = get_health_dir()
    health_dir.mkdir(parents=True, exist_ok=True)
    latest = health_dir / "policy_attack_samples_latest.json"
    history = health_dir / "policy_attack_samples_history.jsonl"

    payload = _run_cases()
    payload["updated_at"] = _utc_now()

    if latest.exists():
        try:
            prev = json.loads(latest.read_text(encoding="utf-8"))
            if isinstance(prev, dict):
                dt = _to_aware(str(prev.get("updated_at", "")))
                if dt is not None:
                    age_h = max(0.0, (datetime.now(timezone.utc) - dt).total_seconds() / 3600.0)
                    payload["age_hours"] = round(age_h, 3)
        except (OSError, json.JSONDecodeError, TypeError, ValueError):
            pass
    payload.setdefault("age_hours", 0.0)

    latest.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    with history.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(payload, ensure_ascii=False) + "\n")

    print(json.dumps({"ok": payload["ok"], "latest": str(latest), "failed_cases": payload["failed_cases"]}, ensure_ascii=False))
    return 0 if payload["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())

