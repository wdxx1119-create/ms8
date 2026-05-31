from __future__ import annotations

import asyncio
import json
from datetime import datetime, timedelta

import pytest

from ms8.engine_core import meta_cognition as mod
from ms8.engine_core.meta_cognition import ImprovementArea, MetaCognitionSystem


class _FakeLLM:
    def __init__(self, responses=None, raise_chat=False):
        self._responses = list(responses or [])
        self._raise = raise_chat

    async def chat(self, messages, temperature=0.5, max_tokens=128, task_type=""):
        if self._raise:
            raise RuntimeError("llm down")
        if self._responses:
            return self._responses.pop(0)
        return "0.75"


def _patch_config(monkeypatch, tmp_path, *, mode="suggest", llm_enabled=True):
    cfg = {
        "memory_dir": tmp_path / "memory",
        "workspace_dir": tmp_path / "workspace",
        "settings": {
            "memory": {
                "meta_cognition": {
                    "mode": mode,
                    "llm_enabled": llm_enabled,
                    "window_size": 10,
                    "time_decay": 0.9,
                    "outlier_zscore": 2.5,
                    "metrics_weights": {
                        "response_quality": 0.3,
                        "response_speed": 0.2,
                        "user_satisfaction": 0.2,
                        "task_completion": 0.2,
                        "learning_efficiency": 0.1,
                    },
                    "llm_fallback_enabled": True,
                    "backup_keep": 2,
                },
                "meta_cognition_thresholds": {
                    "strength_min_score": 0.8,
                    "weakness_max_score": 0.6,
                    "trend_change_significant": 0.03,
                    "rule_based_quality_default": 0.6,
                    "rule_based_satisfaction_default": 0.6,
                    "estimated_improvement_fallback": 0.2,
                },
            }
        },
    }
    cfg["memory_dir"].mkdir(parents=True, exist_ok=True)
    cfg["workspace_dir"].mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(mod, "get_config", lambda: cfg)
    return cfg


def _sample_conversations():
    now = datetime.now()
    return [
        {"role": "user", "content": "谢谢，挺好", "timestamp": (now - timedelta(seconds=8)).isoformat()},
        {"role": "assistant", "content": "已完成修复 done", "timestamp": (now - timedelta(seconds=5)).isoformat()},
        {"role": "user", "content": "这个不错", "timestamp": (now - timedelta(seconds=3)).isoformat()},
        {"role": "assistant", "content": "成功 fixed", "timestamp": now.isoformat()},
    ]


def test_meta_cognition_self_monitor_llm_and_reports(monkeypatch, tmp_path):
    _patch_config(monkeypatch, tmp_path, mode="suggest", llm_enabled=True)
    llm = _FakeLLM(
        responses=[
            "0.9",  # quality
            "0.8",  # satisfaction
            "优势A\n优势B",  # strengths
            "弱点A\n弱点B",  # weaknesses
            "建议1\n建议2",  # recs
        ]
    )
    system = MetaCognitionSystem(llm=llm)

    report = asyncio.run(system.self_monitor(_sample_conversations(), period="daily"))
    assert report.period == "daily"
    assert 0 <= report.overall_score <= 1
    assert system.reports
    assert system.metrics_history

    # data saved + status/trend/progress available
    assert system.meta_file.exists()
    st = system.get_status()
    assert st["report_count"] >= 1
    trend = system.get_performance_trend(days=7)
    assert trend["data_points"] >= 1
    progress = system.get_improvement_progress()
    assert progress["total_plans"] >= 0


def test_meta_cognition_fallback_rules_and_helpers(monkeypatch, tmp_path):
    _patch_config(monkeypatch, tmp_path, mode="monitor_only", llm_enabled=False)
    system = MetaCognitionSystem(llm=_FakeLLM(raise_chat=True))
    system.llm_available = False
    system.llm = None

    # helper branches
    assert system._calculate_response_speed([]) == 0.8
    assert system._calculate_task_completion([]) == 0.5
    assert system._rule_based_quality(["a" * 100]) >= 0.5
    assert 0 <= system._rule_based_satisfaction(["谢谢", "不对"]) <= 1
    assert "用户" in system._format_conversations([{"role": "user", "content": "x"}])
    assert "AI" in system._format_conversations([{"role": "assistant", "content": "y"}])

    metrics = {"response_quality": 0.9, "response_speed": 0.8}
    score = system._calculate_overall_score(metrics)
    assert 0 <= score <= 1

    smoothed = system._smooth_metrics(metrics)
    assert set(smoothed.keys()) == set(metrics.keys())

    # monitor-only should skip recommendations
    report = asyncio.run(system.self_monitor(_sample_conversations(), period="weekly"))
    assert report.recommendations == []


def test_meta_cognition_create_improvement_plan_and_estimation_fallback(monkeypatch, tmp_path):
    _patch_config(monkeypatch, tmp_path, mode="suggest", llm_enabled=True)
    # first action generation succeeds, second estimate fails => fallback score
    llm = _FakeLLM(responses=["行动1\n行动2"], raise_chat=False)
    system = MetaCognitionSystem(llm=llm)

    async def _fail_chat(*args, **kwargs):
        raise RuntimeError("estimate failed")

    # first call from _generate_improvement_actions via _chat (uses llm.chat)
    # then patch _chat for estimate fallback branch
    plan = asyncio.run(system.create_improvement_plan(ImprovementArea.KNOWLEDGE, "improve docs", priority=2))
    assert plan.priority == 2
    assert plan.actions
    assert plan.id in system.improvement_plans

    system._chat = _fail_chat  # type: ignore[method-assign]
    fallback = asyncio.run(system._estimate_improvement(ImprovementArea.KNOWLEDGE, ["a"]))
    assert fallback == pytest.approx(system.estimated_improvement_fallback)


def test_meta_cognition_load_validate_and_backup_trim(monkeypatch, tmp_path):
    _patch_config(monkeypatch, tmp_path)
    system = MetaCognitionSystem(llm=_FakeLLM())

    # validate payload branch
    assert "reports" in system._validate_payload({})
    assert system._validate_payload("bad")["reports"] == {}

    # write malformed data then reload path should not crash
    system.meta_file.write_text("{not-json", encoding="utf-8")
    system._load_meta_data()

    # create backups and trim
    for idx in range(5):
        (system.meta_file.parent / f"meta_cognition.20260101_00000{idx}.bak").write_text("x", encoding="utf-8")
    system._trim_backups()
    backups = sorted(system.meta_file.parent.glob("meta_cognition.*.bak"))
    assert len(backups) <= system.backup_keep
