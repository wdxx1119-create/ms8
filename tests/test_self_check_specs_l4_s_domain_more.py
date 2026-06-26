from __future__ import annotations

import json
import sqlite3
from contextlib import closing
from datetime import datetime, timedelta, timezone
from pathlib import Path

from ms8.engine_core.maintenance.self_check import check_specs as cs


class _Core:
    def __init__(self, memory_dir: Path, workspace_dir: Path) -> None:
        self.config = {
            "memory_dir": str(memory_dir),
            "workspace_dir": str(workspace_dir),
            "settings": {
                "memory": {
                    "security": {
                        "use_keychain": True,
                        "shadow": {"immutable_enabled": False, "backup_dir": str(memory_dir / "backup")},
                    }
                }
            },
        }

    def retrieve_memories(self, query: str, top_k: int = 5) -> list[dict]:
        if query == "记忆":
            return [{"id": "1"}]
        return []

    def shadow_status(self) -> dict:
        return {"manifest_signature_valid": True, "sealed": False}

    def shadow_verify(self) -> dict:
        return {"ok": True}

    @property
    def shadow(self):  # noqa: D401
        class _Locking:
            def current_lease(self):
                return None

        class _Cap:
            def evaluate(self):
                return {"stage": "ok", "ratio": 0.2}

        class _Shadow:
            locking = _Locking()
            capacity_guard = _Cap()

            def status(self):
                return {"mode": "normal"}

        return _Shadow()


def _write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def test_l4_capture_and_injection_paths(tmp_path: Path) -> None:
    core = _Core(tmp_path, tmp_path)
    now = datetime.now(timezone.utc)

    _write_json(
        tmp_path / "auto_memory_log.json",
        {
            "entries": [
                {"timestamp": (now - timedelta(days=1)).isoformat(), "status": "dropped"},
                {"timestamp": (now - timedelta(days=1)).isoformat(), "status": "dropped"},
            ]
        },
    )
    out_capture = cs._check_l4_capture_trend(core, {})
    assert out_capture["status"] == "pass"

    usage = tmp_path / "memory_usage_log.jsonl"
    usage.write_text(
        "\n".join(
            [
                json.dumps({"query": "health probe", "used_in_answer": False, "timestamp": now.isoformat()}),
                json.dumps({"query": "业务问题", "injected_count": 1, "timestamp": now.isoformat()}),
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    out_inj = cs._check_l4_injection_effectiveness(core, {})
    assert out_inj["status"] == "pass"


def test_l4_threshold_and_l5_notice(tmp_path: Path) -> None:
    core = _Core(tmp_path, tmp_path)
    now = datetime.now(timezone.utc)
    pending = {
        "items": [
            {"status": "pending", "created_at": (now - timedelta(days=8)).isoformat()},
            {"status": "pending", "created_at": (now - timedelta(days=1)).isoformat()},
        ]
    }
    _write_json(tmp_path / "threshold_suggestions_pending.json", pending)
    out_t = cs._check_l4_threshold_suggestions(core, {})
    assert out_t["status"] == "warn"

    _write_json(tmp_path / "health" / "llm_notice_state.json", {"last_mode": "unknown_mode"})
    out_l5 = cs._check_l5_llm_notice_state_health(core, {})
    assert out_l5["status"] == "warn"


def test_l4_absorb_health_branches(tmp_path: Path, monkeypatch) -> None:
    core = _Core(tmp_path, tmp_path)

    monkeypatch.setattr(
        "ms8.absorb.health.absorb_health_summary",
        lambda: {
            "risk": "green",
            "authorized_roots": 1,
            "pending_review": 0,
            "quarantine": 0,
            "auto_write_tier": "OFF",
            "kg_extract": {"pending_candidates": 0, "applied_total": 0},
        },
    )
    assert cs._check_l4_absorb_health(core, {})["status"] == "pass"

    monkeypatch.setattr(
        "ms8.absorb.health.absorb_health_summary",
        lambda: {
            "risk": "yellow",
            "authorized_roots": 1,
            "pending_review": 1,
            "quarantine": 0,
            "auto_write_tier": "OFF",
            "kg_extract": {"pending_candidates": 1, "applied_total": 0},
        },
    )
    out_warn = cs._check_l4_absorb_health(core, {})
    assert out_warn["status"] == "warn"
    assert out_warn["details"]["pending_review"] == 1

    monkeypatch.setattr(
        "ms8.absorb.health.absorb_health_summary",
        lambda: {
            "risk": "red",
            "authorized_roots": 1,
            "pending_review": 25,
            "quarantine": 12,
            "auto_write_tier": "OFF",
            "kg_extract": {"pending_candidates": 2, "applied_total": 0},
        },
    )
    assert cs._check_l4_absorb_health(core, {})["status"] == "fail"


def test_m_domain_and_s_domain_branches(tmp_path: Path) -> None:
    ws = tmp_path / "ws"
    ws.mkdir(parents=True, exist_ok=True)
    core = _Core(tmp_path, ws)

    # m5 semantic cache
    _write_json(tmp_path / "semantic_cache.json", {"items": [{"dense": None}, {"dense": [0.1]}]})
    assert cs._check_m5_semantic_cache_health(core, {})["status"] in {"warn", "pass"}

    # m6 cjk recall
    out_m6 = cs._check_m6_cjk_recall_probe(core, {})
    assert out_m6["status"] in {"warn", "pass"}

    # m7 kg access feedback
    kg = tmp_path / "knowledge_graph.db"
    with closing(sqlite3.connect(kg)) as conn:
        conn.execute("CREATE TABLE entities(id INTEGER PRIMARY KEY, access_count INTEGER)")
        conn.execute("INSERT INTO entities(id, access_count) VALUES (1, 0), (2, 0), (3, 1)")
        conn.commit()
    assert cs._check_m7_kg_access_feedback(core, {})["status"] in {"warn", "pass"}

    # m8 latency budget with small sample should pass
    pipe_log = tmp_path / "auto_memory_pipeline.log"
    pipe_log.write_text(json.dumps({"duration_ms": 120.0}) + "\n", encoding="utf-8")
    assert cs._check_m8_pipeline_latency_budget(core, {})["status"] == "pass"

    # s1 keychain effective -> with no file should pass
    assert cs._check_s1_keychain_effective(core, {})["status"] == "pass"
    # s2 writable
    assert cs._check_s2_shadow_ops_audit_writable(core, {})["status"] == "pass"
    # s3 immutable disabled -> warn
    assert cs._check_s3_shadow_immutable_flags(core, {})["status"] == "warn"
    # s4 backup dual-site missing backup -> warn/fail depends primary
    primary = tmp_path / "security" / "shadow_data" / "shadow_events.jsonl"
    primary.parent.mkdir(parents=True, exist_ok=True)
    primary.write_text("{}", encoding="utf-8")
    assert cs._check_s4_shadow_backup_dual_site(core, {})["status"] in {"warn", "pass"}

    # s5 replay cadence with no log -> warn
    assert cs._check_s5_replay_dryrun_weekly(core, {})["status"] == "warn"
    # s6 manifest/checkpoint pass
    assert cs._check_s6_manifest_checkpoint_pair(core, {})["status"] == "pass"
    # s7 locking no lease
    assert cs._check_s7_locking_lease_health(core, {})["status"] == "pass"
    # s8 minimal survival healthy
    assert cs._check_s8_minimal_survival_trigger(core, {})["status"] == "pass"
