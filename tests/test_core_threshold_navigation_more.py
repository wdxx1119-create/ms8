from __future__ import annotations

import collections
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ms8.engine_core.core import MemoryCore


def _core_stub(tmp_path: Path) -> MemoryCore:
    c = MemoryCore.__new__(MemoryCore)
    c._recent_query_tokens = collections.deque(maxlen=24)
    c.config = {
        "workspace_dir": tmp_path,
        "memory_dir": tmp_path / "memory",
        "settings": {
            "memory": {
                "maintenance_policy": {
                    "threshold_auto_navigate_enabled": True,
                    "threshold_auto_navigate_batch_limit": 2,
                    "threshold_auto_navigate_min_recent_count": 80,
                    "threshold_auto_navigate_max_suggestions_per_item": 2,
                    "threshold_auto_navigate_max_abs_delta": 0.15,
                    "threshold_auto_navigate_max_simple_top_k_delta": 1,
                    "threshold_auto_navigate_auto_reject_failed_guardrail": False,
                }
            }
        },
    }
    c._utc_now = lambda: datetime(2026, 5, 20, tzinfo=timezone.utc)  # type: ignore[method-assign]
    return c


def test_threshold_guardrail_check_reason_branches(tmp_path: Path) -> None:
    c = _core_stub(tmp_path)
    item = {
        "stats": {"recent_count": 10},
        "suggestions": [
            {"key": "a", "delta": "x"},
            {"key": "b", "delta": 0.3},
            {"key": "simple_top_k", "delta": 3},
            "bad",
        ],
    }
    out = c._threshold_guardrail_check(
        item,
        min_recent_count=80,
        max_suggestions_per_item=2,
        max_abs_delta=0.15,
        max_simple_top_k_delta=1,
    )
    assert out["pass"] is False
    reasons = " ".join(out["reasons"])
    assert "recent_count_low" in reasons
    assert "too_many_suggestions" in reasons
    assert "non_numeric_delta" in reasons
    assert "delta_too_large" in reasons
    assert "simple_top_k_delta_too_large" in reasons
    assert "invalid_suggestion_type" in reasons


def test_threshold_config_backup_and_restore(tmp_path: Path) -> None:
    c = _core_stub(tmp_path)
    cfg = tmp_path / "config.yaml"
    cfg.write_text("a: 1\n", encoding="utf-8")
    backup = c._backup_workspace_config_for_threshold_nav()
    assert backup is not None and backup.exists()
    cfg.write_text("a: 2\n", encoding="utf-8")
    restored = c._restore_workspace_config_backup(backup)
    assert restored["status"] == "success"
    assert cfg.read_text(encoding="utf-8") == "a: 1\n"
    skipped = c._restore_workspace_config_backup(None)
    assert skipped["status"] == "skipped"


def test_auto_navigate_threshold_disabled_and_guardrail_block(monkeypatch, tmp_path: Path) -> None:
    c = _core_stub(tmp_path)
    c.config["settings"]["memory"]["maintenance_policy"]["threshold_auto_navigate_enabled"] = False
    disabled = c.auto_navigate_threshold_suggestions()
    assert disabled["status"] == "disabled"

    c.config["settings"]["memory"]["maintenance_policy"]["threshold_auto_navigate_enabled"] = True
    c.config["settings"]["memory"]["maintenance_policy"]["threshold_auto_navigate_auto_reject_failed_guardrail"] = True
    monkeypatch.setattr(
        c,
        "list_pending_threshold_suggestions",
        lambda include_processed=False: {
            "items": [{"approval_id": "a1", "stats": {"recent_count": 1}, "suggestions": [{"key": "k", "delta": 1.0}]}],
            "pending_count": 1,
        },
    )
    logs: list[dict[str, Any]] = []
    monkeypatch.setattr(c, "_append_threshold_approval_log", lambda payload: logs.append(payload))
    monkeypatch.setattr(c, "reject_threshold_suggestion", lambda approval_id, approver, reason="": {"status": "success", "id": approval_id})
    out = c.auto_navigate_threshold_suggestions(limit=1)
    assert out["status"] == "success"
    assert out["approved_count"] == 0
    assert out["processed_count"] == 1
    assert out["handled"][0]["status"] == "guardrail_blocked"
    assert "auto_reject" in out["handled"][0]
    assert any(x.get("event") == "auto_navigate_guardrail_blocked" for x in logs)


def test_auto_navigate_threshold_approve_success_and_failure(monkeypatch, tmp_path: Path) -> None:
    c = _core_stub(tmp_path)
    items = [
        {"approval_id": "ok1", "stats": {"recent_count": 100}, "suggestions": [{"key": "k", "delta": 0.01}]},
        {"approval_id": "bad1", "stats": {"recent_count": 100}, "suggestions": [{"key": "k", "delta": 0.01}]},
    ]
    monkeypatch.setattr(c, "list_pending_threshold_suggestions", lambda include_processed=False: {"items": items, "pending_count": 2})
    monkeypatch.setattr(c, "_threshold_guardrail_check", lambda *a, **k: {"pass": True, "reasons": []})
    monkeypatch.setattr(c, "_backup_workspace_config_for_threshold_nav", lambda: tmp_path / "backup.yaml")

    def _approve(approval_id: str, approver: str, confirm: bool = False) -> dict[str, Any]:
        if approval_id == "ok1":
            return {"status": "success", "apply_result": {"applied": [{"k": 1}]}}
        return {"status": "error", "reason": "x"}

    monkeypatch.setattr(c, "approve_threshold_suggestion", _approve)
    monkeypatch.setattr(c, "_restore_workspace_config_backup", lambda backup_file: {"status": "success", "restored_from": str(backup_file)})
    logs: list[dict[str, Any]] = []
    monkeypatch.setattr(c, "_append_threshold_approval_log", lambda payload: logs.append(payload))

    out = c.auto_navigate_threshold_suggestions(limit=2)
    assert out["status"] == "success"
    assert out["approved_count"] == 1
    assert out["processed_count"] == 2
    statuses = {x["approval_id"]: x["status"] for x in out["handled"]}
    assert statuses["ok1"] == "approved"
    assert statuses["bad1"] == "approve_failed"
    assert any(x.get("event") == "auto_navigate_approved" for x in logs)
