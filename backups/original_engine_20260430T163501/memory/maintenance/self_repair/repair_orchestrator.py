from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List

from .repair_policies import get_policy
from .repair_schema import RepairPlanItem, utc_now_iso


def _load_latest_check(memory_dir: Path) -> Dict[str, Any]:
    p = memory_dir / "reports" / "self_check_latest.json"
    if not p.exists():
        return {"status": "missing", "path": str(p), "results": []}
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception as exc:
        return {"status": "error", "error": str(exc), "path": str(p), "results": []}


def _op_id(check_id: str, action: str) -> str:
    base = f"{check_id}|{action}|{utc_now_iso()}"
    sig = hashlib.sha1(base.encode("utf-8", errors="ignore")).hexdigest()[:10]
    return f"repair-{datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S')}-{sig}"


def _topo_sort(items: List[RepairPlanItem]) -> List[RepairPlanItem]:
    if not items:
        return []
    idx_by_action: Dict[str, List[int]] = {}
    for i, it in enumerate(items):
        idx_by_action.setdefault(str(it.action or ""), []).append(i)

    edges: Dict[int, set[int]] = {i: set() for i in range(len(items))}
    indeg: Dict[int, int] = {i: 0 for i in range(len(items))}
    for i, it in enumerate(items):
        for dep in list(it.depends_on or []):
            dep_idx = idx_by_action.get(str(dep or ""), [])
            for d in dep_idx:
                if d == i:
                    continue
                if i not in edges[d]:
                    edges[d].add(i)
                    indeg[i] += 1

    out: List[RepairPlanItem] = []
    domain_order = {"security": 10, "memory": 20, "connect": 30}
    ready = [i for i, v in indeg.items() if v == 0]
    ready.sort(key=lambda i: (domain_order.get(items[i].domain, 99), items[i].risk, items[i].action))

    while ready:
        cur = ready.pop(0)
        out.append(items[cur])
        for nxt in sorted(edges[cur]):
            indeg[nxt] -= 1
            if indeg[nxt] == 0:
                ready.append(nxt)
        ready.sort(key=lambda i: (domain_order.get(items[i].domain, 99), items[i].risk, items[i].action))

    if len(out) != len(items):
        # Cycle or malformed dependencies; fallback to deterministic sort.
        tmp = list(items)
        tmp.sort(key=lambda x: (domain_order.get(x.domain, 99), x.risk, x.action))
        return tmp
    return out


def build_repair_plan(core: Any, *, mode: str = "dry-run", only_risk: str = "", domain: str = "", check_id: str = "") -> Dict[str, Any]:
    memory_dir = Path(core.config["memory_dir"])
    report = _load_latest_check(memory_dir)
    rows = report.get("results", []) if isinstance(report.get("results", []), list) else []
    target_domain = str(domain or "").strip().lower()
    target_check = str(check_id or "").strip()
    target_risk = str(only_risk or "").strip().upper()

    plan: List[RepairPlanItem] = []
    seen = set()
    for row in rows:
        if not isinstance(row, dict):
            continue
        st = str(row.get("status", ""))
        if st not in {"warn", "fail", "error"}:
            continue
        cid = str(row.get("check_id", "") or "")
        if target_check and cid != target_check:
            continue
        pol = get_policy(cid)
        if pol is None:
            continue
        if target_domain and pol.domain.lower() != target_domain:
            continue
        if target_risk and pol.risk.upper() != target_risk:
            continue
        key = (pol.action, pol.target)
        if key in seen:
            continue
        seen.add(key)
        params: Dict[str, Any] = {}
        details = row.get("details", {}) if isinstance(row.get("details", {}), dict) else {}
        if cid == "l2_jsonl_parse":
            bad_files = details.get("bad_files", {}) if isinstance(details.get("bad_files", {}), dict) else {}
            if bad_files:
                # Repair the most recent/first reported corrupted file first.
                params["target_file"] = str(next(iter(bad_files.keys())))
        if cid == "l1_core_files":
            missing = details.get("missing", []) if isinstance(details.get("missing", []), list) else []
            if missing:
                params["missing_files"] = [str(x) for x in missing]

        op = RepairPlanItem(
            operation_id=_op_id(cid, pol.action),
            check_id=cid,
            action=pol.action,
            domain=pol.domain,
            risk=pol.risk,
            reason=str(row.get("message", "") or "self_check_issue"),
            target=pol.target,
            depends_on=list(pol.depends_on),
            params=params,
            action_guide=str(row.get("action_guide", "") or ""),
        )
        plan.append(op)

    plan = _topo_sort(plan)
    return {
        "status": "ok",
        "mode": mode,
        "source_report_status": report.get("status", "ok"),
        "source_report_level": report.get("requested_level", ""),
        "total_candidates": len(rows),
        "plan_count": len(plan),
        "plan": [p.to_dict() for p in plan],
    }
