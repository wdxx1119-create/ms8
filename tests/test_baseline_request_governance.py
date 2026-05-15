from __future__ import annotations

import json
from pathlib import Path

from ms8.engine_core.maintenance.self_check.check_specs import build_check_specs
from ms8.engine_core.maintenance.self_repair.repair_policies import get_hooks, get_policy


class _Core:
    def __init__(self, memory_dir: Path) -> None:
        self.config = {"memory_dir": str(memory_dir)}


def _find_check(check_id: str):
    for spec in build_check_specs(level="FULL_PLUS"):
        if spec.check_id == check_id:
            return spec
    raise AssertionError(f"check not found: {check_id}")


def test_self_check_covers_pending_baseline_request(tmp_path: Path) -> None:
    reports = tmp_path / "reports"
    reports.mkdir(parents=True, exist_ok=True)
    req = reports / "baseline_update_request.json"
    req.write_text(
        json.dumps(
            {
                "status": "needs_authorization",
                "authorizer": "pending",
                "changes": [{"file": "x.py"}],
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    core = _Core(tmp_path)
    spec = _find_check("l1_baseline_update_request")
    out = spec.fn(core, {})
    assert out["status"] == "warn"
    assert "pending authorization" in out["message"]


def test_self_repair_resolves_aligned_baseline_request(tmp_path: Path) -> None:
    reports = tmp_path / "reports"
    reports.mkdir(parents=True, exist_ok=True)

    from ms8.engine_core.maintenance.self_repair import repair_policies as rp

    hashes = rp._current_self_check_hashes()
    (reports / "self_check_integrity_baseline.json").write_text(
        json.dumps({"hashes": hashes}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    req = reports / "baseline_update_request.json"
    req.write_text(
        json.dumps({"status": "needs_authorization", "changes": []}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    core = _Core(tmp_path)
    pol = get_policy("l1_baseline_update_request")
    assert pol is not None
    hooks = get_hooks(pol.action)
    assert hooks is not None

    dry = hooks.dry_run(core, {})
    assert dry["status"] == "ok"
    assert dry["can_auto_resolve"] is True

    apply = hooks.apply(core, {})
    assert apply["status"] == "ok"
    assert apply["resolved"] is True
    assert req.exists() is False
    archive = reports / "baseline_update_request.archive.jsonl"
    assert archive.exists() is True
