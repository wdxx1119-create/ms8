from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from ms8.record_policy import is_valid_status_transition
from ms8.runtime import ensure_runtime_dirs, get_governance_report


def test_status_transition_rules() -> None:
    assert is_valid_status_transition("accepted", "verified") is True
    assert is_valid_status_transition("accepted", "superseded") is True
    assert is_valid_status_transition("revoked", "accepted") is False
    assert is_valid_status_transition("quarantined", "verified") is False


def test_governance_report_includes_schema_and_fallback(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("MS8_HOME", str(tmp_path / "ms8_home"))
    paths = ensure_runtime_dirs()

    memory_row = {
        "id": "m1",
        "text": "hello",
        "normalized_text": "hello",
        "category": "general",
        "status": "accepted",
        "source": "ask",
        "meta": {"admission": "ms8_write_guard_v1"},
        "scope": "personal",
        "authority": "user_explicit",
        "sensitivity": "private",
        "can_recall": True,
        "can_inject": True,
        "can_act_on": False,
    }
    paths["memories"].write_text(json.dumps(memory_row, ensure_ascii=False) + "\n", encoding="utf-8")
    now_iso = datetime.now(timezone.utc).isoformat()
    paths["quarantine"].write_text(
        json.dumps({"at": now_iso, "reason": "missing:status", "record": {"x": 1}}, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )

    fallback_file = paths["health"] / "governance_fallback_log.jsonl"
    fallback_file.write_text(
        json.dumps(
            {"timestamp": now_iso, "kind": "write", "reason": "core_unavailable"},
            ensure_ascii=False,
        )
        + "\n",
        encoding="utf-8",
    )

    out = get_governance_report()
    assert out["schema_invalid_count"] >= 1
    assert out["fallback_write_count"] >= 1
    assert out["fallback_total_count"] >= 1
    assert out.get("fallback_error_code_counts", {}).get("E_CORE_UNAVAILABLE", 0) >= 1
    latest = paths["health"] / "governance_report_latest.json"
    history = paths["health"] / "governance_report_history.jsonl"
    cfg = paths["root"] / "config.json"
    assert latest.exists()
    assert history.exists()
    assert cfg.exists()
    assert out.get("trend", {}).get("window_24h", {}).get("samples", 0) >= 1


def test_governance_trend_delta_updates(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("MS8_HOME", str(tmp_path / "ms8_home"))
    paths = ensure_runtime_dirs()
    base_row = {
        "id": "m1",
        "text": "hello",
        "normalized_text": "hello",
        "category": "general",
        "status": "accepted",
        "source": "ask",
        "meta": {"admission": "ms8_write_guard_v1"},
        "scope": "personal",
        "authority": "user_explicit",
        "sensitivity": "private",
        "can_recall": True,
        "can_inject": True,
        "can_act_on": False,
    }
    paths["memories"].write_text(json.dumps(base_row, ensure_ascii=False) + "\n", encoding="utf-8")
    first = get_governance_report()
    assert first.get("trend", {}).get("window_24h", {}).get("samples", 0) >= 1

    now_iso = datetime.now(timezone.utc).isoformat()
    paths["quarantine"].write_text(
        json.dumps({"at": now_iso, "reason": "invalid:status", "record": {"x": 2}}, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    fallback_file = paths["health"] / "governance_fallback_log.jsonl"
    fallback_file.write_text(
        json.dumps(
            {"timestamp": now_iso, "kind": "write", "reason": "core_unavailable"},
            ensure_ascii=False,
        )
        + "\n",
        encoding="utf-8",
    )

    second = get_governance_report()
    t24 = second.get("trend", {}).get("window_24h", {})
    delta = t24.get("delta", {})
    assert t24.get("samples", 0) >= 2
    assert delta.get("schema_invalid_count", 0) >= 1
    assert delta.get("fallback_write_count", 0) >= 1
    assert t24.get("risk") == "red"


def test_governance_risk_threshold_env_override(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("MS8_HOME", str(tmp_path / "ms8_home"))
    monkeypatch.setenv("MS8_GOV_RISK_RED_FALLBACK_GT", "100")
    monkeypatch.setenv("MS8_GOV_RISK_RED_FALLBACK_TOTAL_GT", "100")
    monkeypatch.setenv("MS8_GOV_RISK_YELLOW_FALLBACK_TOTAL_GT", "5")
    monkeypatch.setenv("MS8_GOV_RISK_RED_FALLBACK_CODE_SPIKE_GT", "100")
    monkeypatch.setenv("MS8_GOV_RISK_YELLOW_FALLBACK_CODE_SPIKE_GT", "5")
    paths = ensure_runtime_dirs()
    row = {
        "id": "m1",
        "text": "hello",
        "normalized_text": "hello",
        "category": "general",
        "status": "accepted",
        "source": "ask",
        "meta": {"admission": "ms8_write_guard_v1"},
        "scope": "personal",
        "authority": "user_explicit",
        "sensitivity": "private",
        "can_recall": True,
        "can_inject": True,
        "can_act_on": False,
    }
    paths["memories"].write_text(json.dumps(row, ensure_ascii=False) + "\n", encoding="utf-8")
    get_governance_report()
    fallback_file = paths["health"] / "governance_fallback_log.jsonl"
    fallback_file.write_text(
        "\n".join(
            json.dumps(
                {
                    "timestamp": f"2026-01-01T00:00:0{i}Z",
                    "kind": "write",
                    "reason": "core_unavailable",
                },
                ensure_ascii=False,
            )
            for i in range(6)
        )
        + "\n",
        encoding="utf-8",
    )
    out = get_governance_report()
    assert out.get("trend", {}).get("window_24h", {}).get("risk") == "yellow"


def test_governance_report_counts_explicit_error_code(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("MS8_HOME", str(tmp_path / "ms8_home"))
    paths = ensure_runtime_dirs()
    row = {
        "id": "m1",
        "text": "hello",
        "normalized_text": "hello",
        "category": "general",
        "status": "accepted",
        "source": "ask",
        "meta": {"admission": "ms8_write_guard_v1"},
        "scope": "personal",
        "authority": "user_explicit",
        "sensitivity": "private",
        "can_recall": True,
        "can_inject": True,
        "can_act_on": False,
    }
    paths["memories"].write_text(json.dumps(row, ensure_ascii=False) + "\n", encoding="utf-8")
    fallback_file = paths["health"] / "governance_fallback_log.jsonl"
    fallback_file.write_text(
        json.dumps(
            {
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "kind": "write",
                "reason": "custom_reason",
                "error_code": "E_CUSTOM",
            },
            ensure_ascii=False,
        )
        + "\n",
        encoding="utf-8",
    )
    out = get_governance_report()
    assert out.get("fallback_error_code_counts", {}).get("E_CUSTOM", 0) == 1


def test_governance_report_handles_corrupt_history(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("MS8_HOME", str(tmp_path / "ms8_home"))
    paths = ensure_runtime_dirs()
    row = {
        "id": "m1",
        "text": "hello",
        "normalized_text": "hello",
        "category": "general",
        "status": "accepted",
        "source": "ask",
        "meta": {"admission": "ms8_write_guard_v1"},
        "scope": "personal",
        "authority": "user_explicit",
        "sensitivity": "private",
        "can_recall": True,
        "can_inject": True,
        "can_act_on": False,
    }
    paths["memories"].write_text(json.dumps(row, ensure_ascii=False) + "\n", encoding="utf-8")
    history = paths["health"] / "governance_report_history.jsonl"
    history.write_text("{bad json}\n", encoding="utf-8")
    out = get_governance_report()
    assert out.get("total_records", 0) >= 1
    assert out.get("trend", {}).get("window_24h", {}).get("samples", 0) >= 1


def test_governance_report_fallback_missing_fields_defaults_generic(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("MS8_HOME", str(tmp_path / "ms8_home"))
    paths = ensure_runtime_dirs()
    row = {
        "id": "m1",
        "text": "hello",
        "normalized_text": "hello",
        "category": "general",
        "status": "accepted",
        "source": "ask",
        "meta": {"admission": "ms8_write_guard_v1"},
        "scope": "personal",
        "authority": "user_explicit",
        "sensitivity": "private",
        "can_recall": True,
        "can_inject": True,
        "can_act_on": False,
    }
    paths["memories"].write_text(json.dumps(row, ensure_ascii=False) + "\n", encoding="utf-8")
    fallback_file = paths["health"] / "governance_fallback_log.jsonl"
    fallback_file.write_text(
        json.dumps({"timestamp": datetime.now(timezone.utc).isoformat(), "kind": "write"}, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    out = get_governance_report()
    assert out.get("fallback_total_count", 0) >= 1
    assert out.get("fallback_error_code_counts", {}).get("E_FALLBACK_GENERIC", 0) >= 1


def test_governance_risk_threshold_env_override_fallback_total(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("MS8_HOME", str(tmp_path / "ms8_home"))
    monkeypatch.setenv("MS8_GOV_RISK_RED_FALLBACK_TOTAL_GT", "100")
    monkeypatch.setenv("MS8_GOV_RISK_YELLOW_FALLBACK_TOTAL_GT", "100")
    paths = ensure_runtime_dirs()
    row = {
        "id": "m1",
        "text": "hello",
        "normalized_text": "hello",
        "category": "general",
        "status": "accepted",
        "source": "ask",
        "meta": {"admission": "ms8_write_guard_v1"},
        "scope": "personal",
        "authority": "user_explicit",
        "sensitivity": "private",
        "can_recall": True,
        "can_inject": True,
        "can_act_on": False,
    }
    paths["memories"].write_text(json.dumps(row, ensure_ascii=False) + "\n", encoding="utf-8")
    fallback_file = paths["health"] / "governance_fallback_log.jsonl"
    fallback_file.write_text(
        "\n".join(
            json.dumps(
                {"timestamp": f"2026-01-01T00:00:0{i}Z", "kind": "retrieve", "reason": "core_unavailable"},
                ensure_ascii=False,
            )
            for i in range(8)
        )
        + "\n",
        encoding="utf-8",
    )
    out = get_governance_report()
    assert out.get("trend", {}).get("window_24h", {}).get("risk") == "green"


def test_governance_error_code_spike_threshold_env_override(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("MS8_HOME", str(tmp_path / "ms8_home"))
    monkeypatch.setenv("MS8_GOV_RISK_RED_FALLBACK_GT", "100")
    monkeypatch.setenv("MS8_GOV_RISK_YELLOW_FALLBACK_TOTAL_GT", "100")
    monkeypatch.setenv("MS8_GOV_RISK_RED_FALLBACK_TOTAL_GT", "100")
    monkeypatch.setenv("MS8_GOV_RISK_RED_FALLBACK_CODE_SPIKE_GT", "100")
    monkeypatch.setenv("MS8_GOV_RISK_YELLOW_FALLBACK_CODE_SPIKE_GT", "100")
    paths = ensure_runtime_dirs()
    row = {
        "id": "m1",
        "text": "hello",
        "normalized_text": "hello",
        "category": "general",
        "status": "accepted",
        "source": "ask",
        "meta": {"admission": "ms8_write_guard_v1"},
        "scope": "personal",
        "authority": "user_explicit",
        "sensitivity": "private",
        "can_recall": True,
        "can_inject": True,
        "can_act_on": False,
    }
    paths["memories"].write_text(json.dumps(row, ensure_ascii=False) + "\n", encoding="utf-8")
    _ = get_governance_report()
    fallback_file = paths["health"] / "governance_fallback_log.jsonl"
    fallback_file.write_text(
        "\n".join(
            json.dumps(
                {"timestamp": f"2026-01-01T00:00:0{i}Z", "kind": "retrieve", "error_code": "E_SPIKE"},
                ensure_ascii=False,
            )
            for i in range(8)
        )
        + "\n",
        encoding="utf-8",
    )
    out = get_governance_report()
    t24 = out.get("trend", {}).get("window_24h", {})
    assert t24.get("delta", {}).get("fallback_error_code_spike", 0) >= 8
    assert t24.get("risk") == "green"


def test_v2_authority_does_not_downgrade_overall_on_legacy_capture_only(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("MS8_HOME", str(tmp_path / "ms8_home"))
    paths = ensure_runtime_dirs()
    cfg_path = paths["root"] / "config.json"
    cfg = json.loads(cfg_path.read_text(encoding="utf-8"))
    cfg["governance_slo"] = {"authority": "v2_preview", "v2_min_eligible_events": 10}
    cfg_path.write_text(json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8")

    row = {
        "id": "m1",
        "text": "hello",
        "normalized_text": "hello",
        "category": "general",
        "status": "accepted",
        "source": "ask",
        "meta": {"admission": "ms8_write_guard_v1"},
        "scope": "personal",
        "authority": "user_explicit",
        "sensitivity": "private",
        "can_recall": True,
        "can_inject": True,
        "can_act_on": False,
    }
    paths["memories"].write_text(json.dumps(row, ensure_ascii=False) + "\n", encoding="utf-8")
    monkeypatch.setattr(
        "ms8.runtime.get_engine_monitoring_status",
        lambda: {
            "rates": {"capture_rate": 0.05, "auto_total_entries": 120},
            "slo": {"targets": {"capture_rate_min": 0.85, "capture_rate_min_samples": 30}},
            "rates_v2": {
                "eligible_events": 120,
                "pipeline_success_rate": 1.0,
                "valuable_capture_rate": 1.0,
                "noise_block_rate": 0.95,
            },
            "slo_v2_preview": {"all_ok": True},
            "compression_freshness": {"exists": True, "hours_since_last": 1.0, "last_report": "x"},
        },
    )

    out = get_governance_report()
    assert out.get("slo_authority") == "v2_preview"
    assert out.get("capture_rate_breach") is True
    domains = out.get("health_domains", {})
    assert domains.get("memory_quality_health") == "green"
    assert domains.get("overall") in {"green", "yellow"}
    assert "legacy_capture_caution" in domains.get("overall_reasons", [])


def test_governance_report_exposes_overall_reasons(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("MS8_HOME", str(tmp_path / "ms8_home"))
    paths = ensure_runtime_dirs()
    row = {
        "id": "m1",
        "text": "hello",
        "normalized_text": "hello",
        "category": "general",
        "status": "accepted",
        "source": "ask",
        "meta": {"admission": "ms8_write_guard_v1"},
        "scope": "personal",
        "authority": "user_explicit",
        "sensitivity": "private",
        "can_recall": True,
        "can_inject": True,
        "can_act_on": False,
    }
    paths["memories"].write_text(json.dumps(row, ensure_ascii=False) + "\n", encoding="utf-8")
    out = get_governance_report()
    domains = out.get("health_domains", {})
    assert isinstance(domains.get("overall_reasons"), list)
