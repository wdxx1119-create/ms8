from __future__ import annotations

import json
from pathlib import Path

from ms8 import runtime


def test_get_llm_status_runtime_handles_non_dict_config(monkeypatch, tmp_path: Path) -> None:
    cfg = tmp_path / "config.yaml"
    monkeypatch.setattr(runtime, "_workspace_config_path", lambda: cfg)
    monkeypatch.setattr(runtime, "_read_workspace_config_yaml", lambda: {"memory": {"llm": "bad"}})
    monkeypatch.setattr(runtime, "get_engine_llm_status", lambda: {"available": False})
    monkeypatch.setattr(
        runtime,
        "detect_llm_providers",
        lambda: {
            "ollama": {"ready": True},
            "openai": {"ready": False},
            "openrouter": {"ready": False},
        },
    )
    out = runtime.get_llm_status_runtime()
    assert out["ok"] is True
    assert out["configured"]["enabled"] is False
    assert out["recommended_mode"] == "local"


def test_get_expression_router_status_handles_bad_rows(monkeypatch, tmp_path: Path) -> None:
    root = tmp_path
    reports = root / "memory" / "reports"
    reports.mkdir(parents=True, exist_ok=True)
    decisions = reports / "expression_router_decisions.jsonl"
    decisions.write_text(
        "\n".join(
            [
                "{bad-json",
                json.dumps(["not-dict"], ensure_ascii=False),
                json.dumps({"mode": "strong", "cooldown_applied": True, "profile_used": True, "reason": "r1"}, ensure_ascii=False),
                json.dumps({"mode": "light", "reason": "r2"}, ensure_ascii=False),
                json.dumps({"mode": "normal", "reason": "r2"}, ensure_ascii=False),
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    (root / "memory" / "expression_router_state.json").write_text("{bad-json", encoding="utf-8")
    (root / "memory" / "expression_preference_profile.json").write_text("{bad-json", encoding="utf-8")
    monkeypatch.setattr(runtime, "ensure_runtime_dirs", lambda: {"root": root})
    out = runtime.get_expression_router_status(sample_size=10)
    assert out["total_samples"] == 3
    assert out["mode_counts"]["strong"] == 1
    assert out["mode_counts"]["light"] == 1
    assert out["mode_counts"]["normal"] == 1
    assert out["cooldown_applied_count"] == 1
    assert out["profile_used_count"] == 1
    assert out["top_reasons"][0] == "r2"


def test_get_expression_router_status_reads_state_and_profile(monkeypatch, tmp_path: Path) -> None:
    root = tmp_path
    mem = root / "memory"
    (mem / "reports").mkdir(parents=True, exist_ok=True)
    (mem / "reports" / "expression_router_decisions.jsonl").write_text("", encoding="utf-8")
    (mem / "expression_router_state.json").write_text(
        json.dumps({"current_round": 7, "last_mode": "invalid"}, ensure_ascii=False),
        encoding="utf-8",
    )
    (mem / "expression_preference_profile.json").write_text(
        json.dumps({"evidence_count": 11}, ensure_ascii=False),
        encoding="utf-8",
    )
    monkeypatch.setattr(runtime, "ensure_runtime_dirs", lambda: {"root": root})
    out = runtime.get_expression_router_status(sample_size=5)
    assert out["current_round"] == 7
    assert out["last_mode"] is None
    assert out["profile_evidence_count"] == 11


def test_configure_llm_mode_invalid_mode() -> None:
    out = runtime.configure_llm_mode_runtime("invalid-mode")
    assert out["ok"] is False
    assert out["error"] == "invalid_mode"


def test_configure_llm_mode_cloud_and_offline(monkeypatch, tmp_path: Path) -> None:
    cfg = tmp_path / "config.yaml"
    monkeypatch.setattr(runtime, "_workspace_config_path", lambda: cfg)
    monkeypatch.setattr(runtime, "_read_workspace_config_yaml", lambda: {})

    written = {}

    def _write(payload: dict) -> None:
        written["payload"] = payload
        cfg.write_text("written", encoding="utf-8")

    monkeypatch.setattr(runtime, "_write_workspace_config_yaml", _write)

    # offline path
    monkeypatch.setattr(
        runtime,
        "detect_llm_providers",
        lambda: {
            "ollama": {"ready": False},
            "openai": {"ready": False},
            "openrouter": {"ready": False},
        },
    )
    out_offline = runtime.configure_llm_mode_runtime("auto")
    assert out_offline["ok"] is False
    assert out_offline["error"] == "offline_unavailable"

    # cloud path
    monkeypatch.setattr(
        runtime,
        "detect_llm_providers",
        lambda: {
            "ollama": {"ready": False},
            "openai": {"ready": True},
            "openrouter": {"ready": False},
        },
    )
    out_cloud = runtime.configure_llm_mode_runtime("cloud")
    assert out_cloud["ok"] is True
    assert out_cloud["applied_mode"] == "cloud"
    llm_cfg = written["payload"]["memory"]["llm"]
    assert llm_cfg["enabled"] is True
    assert llm_cfg["provider_order_chat"][0] == "openai"


def test_consume_llm_degraded_notice_state_transitions(monkeypatch, tmp_path: Path) -> None:
    health = tmp_path / "health"
    health.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(runtime, "ensure_runtime_dirs", lambda: {"health": health})

    # first enters rule_only -> emit
    monkeypatch.setattr(
        runtime,
        "get_llm_status_runtime",
        lambda: {"effective_mode_ladder": {"mode": "rule_only"}},
    )
    first = runtime.consume_llm_degraded_notice_runtime()
    assert first["emit"] is True

    # second still rule_only -> no emit
    second = runtime.consume_llm_degraded_notice_runtime()
    assert second["emit"] is False

    # back to full -> update state, no emit
    monkeypatch.setattr(
        runtime,
        "get_llm_status_runtime",
        lambda: {"effective_mode_ladder": {"mode": "full"}},
    )
    third = runtime.consume_llm_degraded_notice_runtime()
    assert third["emit"] is False


def test_update_governance_risk_config_persists(monkeypatch, tmp_path: Path) -> None:
    cfg_file = tmp_path / "config.json"
    cfg_file.write_text("{}", encoding="utf-8")
    monkeypatch.setattr(runtime, "ensure_runtime_dirs", lambda: {"config_file": cfg_file})
    monkeypatch.setattr(
        runtime,
        "_load_governance_risk_config",
        lambda: {
            "red": {"schema_invalid_count_gt": 0, "fallback_write_count_gt": 5},
            "yellow": {"pending_review_gt": 5},
        },
    )
    out = runtime.update_governance_risk_config(
        red_fallback_write_gt=9,
        yellow_pending_review_gt=12,
    )
    assert out["governance_risk"]["red"]["fallback_write_count_gt"] == 9
    assert out["governance_risk"]["yellow"]["pending_review_gt"] == 12
    saved = json.loads(cfg_file.read_text(encoding="utf-8"))
    assert saved["governance_risk"]["red"]["fallback_write_count_gt"] == 9


def test_load_governance_risk_config_fallback_defaults(monkeypatch, tmp_path: Path) -> None:
    cfg_file = tmp_path / "config.json"
    cfg_file.write_text("{bad-json", encoding="utf-8")
    monkeypatch.setattr(runtime, "ensure_runtime_dirs", lambda: {"config_file": cfg_file})
    out = runtime._load_governance_risk_config()
    assert "red" in out and "yellow" in out
    assert out["red"]["fallback_total_count_gt"] >= 1
