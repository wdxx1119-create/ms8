from __future__ import annotations

import json
from pathlib import Path

from ms8.engine_core.maintenance.self_check import check_specs as cs


class _Core:
    def __init__(self, connect_root: Path) -> None:
        self.config = {"settings": {"memory": {"connect": {"root": str(connect_root)}}}}


def test_c6_client_config_presence_warn_and_ok(monkeypatch, tmp_path: Path) -> None:
    connect_root = tmp_path / "connect"
    core = _Core(connect_root)
    monkeypatch.setattr(cs.Path, "home", staticmethod(lambda: tmp_path))

    out_warn = cs._check_c6_client_config_presence(core, {})
    assert out_warn["status"] == "warn"

    claude_cfg = tmp_path / "Library" / "Application Support" / "Claude" / "claude_desktop_config.json"
    claude_cfg.parent.mkdir(parents=True, exist_ok=True)
    claude_cfg.write_text("{}", encoding="utf-8")
    out_ok = cs._check_c6_client_config_presence(core, {})
    assert out_ok["status"] == "pass"


def test_c7_source_tagging_variants(tmp_path: Path) -> None:
    connect_root = tmp_path / "connect"
    core = _Core(connect_root)
    audit = connect_root / "logs" / "audit.log"
    audit.parent.mkdir(parents=True, exist_ok=True)

    out_missing = cs._check_c7_source_tagging_e2e(core, {})
    assert out_missing["status"] == "warn"

    audit.write_text("hello world\n", encoding="utf-8")
    out_no_samples = cs._check_c7_source_tagging_e2e(core, {})
    assert out_no_samples["status"] == "warn"

    low_ratio_lines = "\n".join(
        [
            'save_memory source=none',
            'save_memory source=none2',
            'save_memory source=mcp:ok',
        ]
    )
    audit.write_text(low_ratio_lines, encoding="utf-8")
    out_low = cs._check_c7_source_tagging_e2e(core, {})
    assert out_low["status"] == "warn"

    good_ratio_lines = "\n".join(
        [
            'save_memory source=mcp:ok',
            'save_memory source=adapter:x',
            '{"source": "mcp:y", "event":"save_memory"}',
        ]
    )
    audit.write_text(good_ratio_lines, encoding="utf-8")
    out_ok = cs._check_c7_source_tagging_e2e(core, {})
    assert out_ok["status"] == "pass"


def test_c8_connect_report_health_branches(tmp_path: Path) -> None:
    connect_root = tmp_path / "connect"
    core = _Core(connect_root)
    report = connect_root / "runtime" / "connect_report.json"
    report.parent.mkdir(parents=True, exist_ok=True)

    out_missing = cs._check_c8_connect_report_health(core, {})
    assert out_missing["status"] == "warn"

    report.write_text(json.dumps({"result": {"overall_ok": True}, "steps": []}), encoding="utf-8")
    out_ok = cs._check_c8_connect_report_health(core, {})
    assert out_ok["status"] == "pass"

    report.write_text(
        json.dumps({"result": {"overall_ok": False}, "steps": [{"name": "apply", "ok": False}]}), encoding="utf-8"
    )
    out_warn = cs._check_c8_connect_report_health(core, {})
    assert out_warn["status"] == "warn"


def test_c9_auto_repair_log_health_branches(tmp_path: Path) -> None:
    connect_root = tmp_path / "connect"
    core = _Core(connect_root)
    log_file = connect_root / "runtime" / "auto_repair_log.jsonl"
    log_file.parent.mkdir(parents=True, exist_ok=True)

    out_missing = cs._check_c9_auto_repair_log_health(core, {})
    assert out_missing["status"] == "warn"

    log_file.write_text("{bad json}\n", encoding="utf-8")
    out_unreadable = cs._check_c9_auto_repair_log_health(core, {})
    assert out_unreadable["status"] == "warn"

    log_file.write_text('{"timestamp":"2026-05-10T10:00:00+00:00"}\nnot-json\n', encoding="utf-8")
    out_warn = cs._check_c9_auto_repair_log_health(core, {})
    assert out_warn["status"] == "warn"

    log_file.write_text('{"timestamp":"2026-05-10T10:00:00+00:00"}\n', encoding="utf-8")
    out_ok = cs._check_c9_auto_repair_log_health(core, {})
    assert out_ok["status"] == "pass"


def test_c10_bootstrap_consistency_branches(tmp_path: Path) -> None:
    connect_root = tmp_path / "connect"
    core = _Core(connect_root)
    report = connect_root / "runtime" / "bootstrap_report.json"
    report.parent.mkdir(parents=True, exist_ok=True)

    out_missing = cs._check_c10_connect_reconcile_consistency(core, {})
    assert out_missing["status"] == "warn"

    report.write_text(
        json.dumps({"ok": True, "connect_flow_overall_ok": False, "self_heal": {"verify_ok": True, "smoke_ok": True}}),
        encoding="utf-8",
    )
    out_fail = cs._check_c10_connect_reconcile_consistency(core, {})
    assert out_fail["status"] == "fail"

    report.write_text(
        json.dumps({"ok": True, "connect_flow_overall_ok": True, "self_heal": {"verify_ok": False, "smoke_ok": False}}),
        encoding="utf-8",
    )
    out_warn = cs._check_c10_connect_reconcile_consistency(core, {})
    assert out_warn["status"] == "warn"

    report.write_text(
        json.dumps({"ok": True, "connect_flow_overall_ok": True, "self_heal": {"verify_ok": True, "smoke_ok": True}}),
        encoding="utf-8",
    )
    out_ok = cs._check_c10_connect_reconcile_consistency(core, {})
    assert out_ok["status"] == "pass"


def test_c12_c13_c14_template_hint_chain_branches(tmp_path: Path) -> None:
    connect_root = tmp_path / "connect"
    core = _Core(connect_root)
    snippet = connect_root / "runtime" / "client_snippets" / "generic_mcp.json"
    first_report = connect_root / "runtime" / "first_install_connect_report.json"
    snippet.parent.mkdir(parents=True, exist_ok=True)

    out_c12_missing = cs._check_c12_template_export_health(core, {})
    assert out_c12_missing["status"] == "warn"

    snippet.write_text("not-json", encoding="utf-8")
    out_c12_fail = cs._check_c12_template_export_health(core, {})
    assert out_c12_fail["status"] == "fail"

    snippet.write_text(json.dumps({"mcpServers": {"ms8-memory": {"command": "", "args": []}}}), encoding="utf-8")
    out_c12_warn = cs._check_c12_template_export_health(core, {})
    assert out_c12_warn["status"] == "warn"

    snippet.write_text(
        json.dumps({"mcpServers": {"ms8-memory": {"command": "python", "args": ["-m", "ms8.connect"]}}}),
        encoding="utf-8",
    )
    out_c12_ok = cs._check_c12_template_export_health(core, {})
    assert out_c12_ok["status"] == "pass"

    out_c13_missing = cs._check_c13_actionable_hints_quality(core, {})
    out_c14_missing = cs._check_c14_shortest_repair_chain_health(core, {})
    assert out_c13_missing["status"] == "warn"
    assert out_c14_missing["status"] == "warn"

    first_report.write_text(json.dumps({"counts": {"manual": 1, "degraded": 0}, "actionable_hints": []}), encoding="utf-8")
    out_c13_warn = cs._check_c13_actionable_hints_quality(core, {})
    assert out_c13_warn["status"] == "warn"

    first_report.write_text(
        json.dumps({"counts": {"manual": 1, "degraded": 0}, "actionable_hints": ["Check manually"]}),
        encoding="utf-8",
    )
    out_c13_warn2 = cs._check_c13_actionable_hints_quality(core, {})
    assert out_c13_warn2["status"] == "warn"

    first_report.write_text(
        json.dumps({"counts": {"manual": 1, "degraded": 0}, "actionable_hints": ["ms8 connect verify --target all"]}),
        encoding="utf-8",
    )
    out_c13_ok = cs._check_c13_actionable_hints_quality(core, {})
    assert out_c13_ok["status"] == "pass"

    first_report.write_text(json.dumps({"counts": {"manual": 0, "degraded": 0}, "shortest_repair_chain": ""}), encoding="utf-8")
    out_c14_ok_no_required = cs._check_c14_shortest_repair_chain_health(core, {})
    assert out_c14_ok_no_required["status"] == "pass"

    first_report.write_text(json.dumps({"counts": {"manual": 1, "degraded": 0}, "shortest_repair_chain": ""}), encoding="utf-8")
    out_c14_warn_missing = cs._check_c14_shortest_repair_chain_health(core, {})
    assert out_c14_warn_missing["status"] == "warn"

    first_report.write_text(
        json.dumps({"counts": {"manual": 1, "degraded": 0}, "shortest_repair_chain": "echo hi && ms8 connect verify --target all"}),
        encoding="utf-8",
    )
    out_c14_warn_bad = cs._check_c14_shortest_repair_chain_health(core, {})
    assert out_c14_warn_bad["status"] == "warn"

    first_report.write_text(
        json.dumps(
            {
                "counts": {"manual": 1, "degraded": 0},
                "shortest_repair_chain": "ms8 connect apply --target all && ms8 connect verify --target all",
            }
        ),
        encoding="utf-8",
    )
    out_c14_ok = cs._check_c14_shortest_repair_chain_health(core, {})
    assert out_c14_ok["status"] == "pass"

