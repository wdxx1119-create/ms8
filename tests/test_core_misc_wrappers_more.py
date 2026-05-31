from __future__ import annotations

import asyncio
import collections
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

from ms8.engine_core.core import MemoryCore


def _core(tmp_path: Path) -> MemoryCore:
    c = MemoryCore.__new__(MemoryCore)
    c._recent_query_tokens = collections.deque(maxlen=24)
    c._utc_now = lambda: datetime(2026, 5, 25, tzinfo=timezone.utc)  # type: ignore[method-assign]
    c.config = {"workspace_dir": tmp_path, "memory_dir": tmp_path / "memory", "settings": {"memory": {}}}
    c._run_async = lambda x: x  # type: ignore[method-assign]
    return c


def test_rollback_improvement_found_and_missing(tmp_path: Path, monkeypatch) -> None:
    c = _core(tmp_path)
    captured: dict[str, object] = {}

    class _DummyImprovementRecord:
        def __init__(self, **kwargs):
            self.id = "rb-1"
            self._payload = kwargs

        def to_dict(self):
            return {"id": self.id, "kind": "rollback"}

    class _DummyValidationStatus:
        VALIDATED = "validated"

    monkeypatch.setattr(
        "ms8.engine_core.self_improvement.ImprovementRecord",
        _DummyImprovementRecord,
        raising=True,
    )
    monkeypatch.setattr(
        "ms8.engine_core.self_improvement.ValidationStatus",
        _DummyValidationStatus,
        raising=True,
    )

    c.self_improvement = SimpleNamespace(
        history=[
            {
                "id": "imp-1",
                "improvement_type": "memory_edit",
                "before_state": {"a": 1},
                "after_state": {"a": 2},
            }
        ],
        _generate_improvement_id=lambda: "gen-1",
        _rollback_improvement=lambda rb: captured.update({"rolled_back": rb.id}),  # noqa: ANN001
        _save_history=lambda: captured.update({"saved": True}),
    )
    ok = c.rollback_improvement("imp-1")
    assert ok["status"] == "success"
    assert ok["rollback_id"] == "rb-1"
    assert captured["rolled_back"] == "rb-1"
    assert captured["saved"] is True

    miss = c.rollback_improvement("not-exist")
    assert miss["status"] == "error"


def test_detect_patterns_paths(tmp_path: Path) -> None:
    c = _core(tmp_path)

    async def _detect_ok(conversations, use_llm=True):  # noqa: ANN001
        return {"status": "success", "n": len(conversations), "use_llm": use_llm}

    c.self_improvement = SimpleNamespace(
        detect_user_patterns=_detect_ok
    )
    out = asyncio.run(c.detect_patterns([{"role": "user", "content": "x"}], use_llm=False))
    assert out["status"] == "success"
    assert out["n"] == 1

    c.self_improvement = SimpleNamespace()
    out2 = asyncio.run(c.detect_patterns([]))
    assert out2["status"] == "error"


def test_monitoring_and_advanced_insight_and_maintenance_wrappers(tmp_path: Path) -> None:
    c = _core(tmp_path)
    c.monitoring = SimpleNamespace(status=lambda persist_reports=True: {"persist_reports": persist_reports})  # noqa: ANN001,E501
    assert c.get_monitoring_status(lightweight=False)["persist_reports"] is True
    assert c.get_monitoring_status(lightweight=True)["persist_reports"] is False

    c.context_understanding = SimpleNamespace(understandings={"a": 1})
    c.pattern_recognition = SimpleNamespace(patterns={"p": 2})
    c._advanced_insight_count = 7
    out = c.get_advanced_insight_status()
    assert out["enabled"] is True
    assert out["context_records"] == 1
    assert out["pattern_records"] == 1
    assert out["interaction_counter"] == 7

    class _Bad:
        @property
        def understandings(self):  # type: ignore[override]
            raise RuntimeError("bad_ctx")

    class _Bad2:
        @property
        def patterns(self):  # type: ignore[override]
            raise RuntimeError("bad_pat")

    c.context_understanding = _Bad()
    c.pattern_recognition = _Bad2()
    out2 = c.get_advanced_insight_status()
    assert out2["context_records"] == 0
    assert out2["pattern_records"] == 0

    c.maintenance = SimpleNamespace(
        run_maintenance=lambda force=True: {"status": "success", "force": force},  # noqa: ANN001
        run_restore_drill=lambda: {"status": "success"},
    )
    c._run_maintenance_policy = lambda force=True: {"status": "policy", "force": force}  # type: ignore[method-assign]
    m = c.run_maintenance_now(force=False)
    assert m["maintenance"]["force"] is False
    assert m["policy"]["status"] == "policy"
    assert c.run_restore_drill()["status"] == "success"


def test_security_and_shadow_simple_forwarders(tmp_path: Path) -> None:
    c = _core(tmp_path)
    c.crypto = SimpleNamespace(
        status=lambda: {"enabled": False},
        enable_encryption=lambda pw: {"status": "success", "pw": pw},  # noqa: ANN001
        disable_encryption=lambda pw: {"status": "success", "pw": pw},  # noqa: ANN001
        unlock=lambda pw: pw == "ok",  # noqa: ANN001
        lock=lambda: None,
    )
    c.shadow = SimpleNamespace(
        status=lambda: {"enabled": True},
        health_check=lambda: {"status": "ok"},
        archive_replayed_spool=lambda: {"status": "success"},
        startup_self_heal=lambda: {"status": "success"},
        rotate_events_monthly=lambda: {"status": "success"},
        sync_verified_backup=lambda **kw: {"status": "success", **kw},  # noqa: ANN001
        verify_checkpoints=lambda: {"status": "success"},
        restore_shadow_snapshot=lambda *a, **k: {"status": "success"},  # noqa: ANN001
        list_manifest_snapshots=lambda limit=20: [{"limit": limit}],  # noqa: ANN001
        restore_manifest_snapshot=lambda *a, **k: {"status": "success"},  # noqa: ANN001
        restore_backup_snapshot=lambda *a, **k: {"status": "success"},  # noqa: ANN001
        run_recovery_drill=lambda **kw: {"status": "success", **kw},  # noqa: ANN001
    )

    assert c.security_status()["enabled"] is False
    assert c.security_enable("p")["status"] == "success"
    assert c.security_disable("p")["status"] == "success"
    assert c.security_unlock("bad")["status"] == "error"
    assert c.security_unlock("ok")["status"] == "success"
    assert c.security_lock()["status"] == "success"

    assert c.shadow_status()["enabled"] is True
    assert c.shadow_health()["status"] == "ok"
    assert c.shadow_archive_spool()["status"] == "success"
    assert c.shadow_startup_self_heal()["status"] == "success"
    assert c.shadow_rotate_events_monthly()["status"] == "success"
    assert c.shadow_sync_verified_backup(caller_id="x")["caller_id"] == "x"
    assert c.shadow_verify()["status"] == "success"
    assert c.shadow_restore_snapshot("s")["status"] == "success"
    assert c.shadow_list_manifest_snapshots(limit=3)["items"][0]["limit"] == 3
    assert c.shadow_restore_manifest_snapshot("m")["status"] == "success"
    assert c.shadow_restore_backup_snapshot("b")["status"] == "success"
    assert c.shadow_recovery_drill(sample_text="z")["sample_text"] == "z"
