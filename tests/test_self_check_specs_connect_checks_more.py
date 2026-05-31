from __future__ import annotations

import json
from pathlib import Path

from ms8.engine_core.maintenance.self_check import check_specs as cs


class _Core:
    def __init__(self, root: Path) -> None:
        self.config = {"settings": {"memory": {"connect": {"root": str(root)}}}}


def _write(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def test_c6_c8_c10_basic_paths(tmp_path: Path) -> None:
    root = tmp_path / "connect"
    core = _Core(root)

    c6 = cs._check_c6_client_config_presence(core, {})
    assert c6["status"] in {"warn", "pass"}

    c8_missing = cs._check_c8_connect_report_health(core, {})
    assert c8_missing["status"] == "warn"

    _write(root / "runtime" / "connect_report.json", {"result": {"overall_ok": True}, "steps": [{"ok": True}]})
    c8_ok = cs._check_c8_connect_report_health(core, {})
    assert c8_ok["status"] == "pass"

    c10_missing = cs._check_c10_connect_reconcile_consistency(core, {})
    assert c10_missing["status"] == "warn"
    _write(root / "runtime" / "bootstrap_report.json", {"ok": True, "connect_flow_overall_ok": False})
    c10_bad = cs._check_c10_connect_reconcile_consistency(core, {})
    assert c10_bad["status"] == "fail"


def test_c7_c9_log_checks(tmp_path: Path) -> None:
    root = tmp_path / "connect"
    core = _Core(root)

    # c7 missing
    out_missing = cs._check_c7_source_tagging_e2e(core, {})
    assert out_missing["status"] == "warn"

    audit = root / "logs" / "audit.log"
    audit.parent.mkdir(parents=True, exist_ok=True)
    audit.write_text(
        "\n".join(
            [
                'save_memory source=mcp:claude ok',
                'save_memory source=adapter:tool ok',
                '{"source": "mcp:foo"}',
            ]
        ),
        encoding="utf-8",
    )
    out_ok = cs._check_c7_source_tagging_e2e(core, {})
    assert out_ok["status"] in {"pass", "warn"}

    c9_missing = cs._check_c9_auto_repair_log_health(core, {})
    assert c9_missing["status"] == "warn"
    logf = root / "runtime" / "auto_repair_log.jsonl"
    logf.parent.mkdir(parents=True, exist_ok=True)
    logf.write_text('{"timestamp":"2026-05-23T00:00:00Z","x":1}\n', encoding="utf-8")
    c9_ok = cs._check_c9_auto_repair_log_health(core, {})
    assert c9_ok["status"] in {"pass", "warn"}


def test_c11_c12_c13_c14_checks(tmp_path: Path) -> None:
    root = tmp_path / "connect"
    core = _Core(root)

    # c11 profile schema
    profiles = root / "profiles"
    profiles.mkdir(parents=True, exist_ok=True)
    (profiles / "ok.yaml").write_text("name: test\npath: /tmp/a\nsnippet_file: x.json\nverify_keys: []\n", encoding="utf-8")
    c11 = cs._check_c11_external_profile_schema(core, {})
    assert c11["status"] == "pass"

    # c12 template export
    c12_missing = cs._check_c12_template_export_health(core, {})
    assert c12_missing["status"] == "warn"
    _write(
        root / "runtime" / "client_snippets" / "generic_mcp.json",
        {"mcpServers": {"ms8-memory": {"command": "python", "args": ["-m", "x"]}}},
    )
    c12_ok = cs._check_c12_template_export_health(core, {})
    assert c12_ok["status"] == "pass"

    # c13/c14 first install report
    c13_missing = cs._check_c13_actionable_hints_quality(core, {})
    assert c13_missing["status"] == "warn"
    _write(
        root / "runtime" / "first_install_connect_report.json",
        {
            "counts": {"manual": 1, "degraded": 0},
            "actionable_hints": ["ms8 connect apply --target claude_desktop"],
            "shortest_repair_chain": "ms8 connect apply --target claude_desktop",
        },
    )
    c13 = cs._check_c13_actionable_hints_quality(core, {})
    assert c13["status"] == "pass"
    c14 = cs._check_c14_shortest_repair_chain_health(core, {})
    assert c14["status"] == "pass"
