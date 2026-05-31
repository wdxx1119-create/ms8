from __future__ import annotations

import json
from pathlib import Path

from ms8.connect.scripts import common, connect, install_env, status


def test_common_connect_root_fallback_and_helpers(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("OPENCLAW_MEMORY_AUTO_ROOT", str(tmp_path / "custom_connect"))
    root = common.connect_root()
    assert (root / "runtime").exists()
    assert (root / "logs").exists()

    p = tmp_path / "x.json"
    common.write_json(p, {"ok": True})
    assert common.read_json(p)["ok"] is True
    assert common.read_json(tmp_path / "missing.json") == {}

    y = tmp_path / "x.yaml"
    y.write_text("a: 1\n", encoding="utf-8")
    assert common.load_yaml(y)["a"] == 1
    y.write_text(":\n", encoding="utf-8")
    assert common.load_yaml(y) == {}


def test_common_run_and_choose_python(monkeypatch) -> None:
    info = common.run(["python3", "-c", "print('ok')"])
    assert info["ok"] is True
    assert info["code"] == 0
    assert "ok" in info["stdout"]

    monkeypatch.setattr(common.shutil, "which", lambda _: None)
    assert common.choose_python() == "python3"


def test_status_main_and_target_connectivity(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("OPENCLAW_MEMORY_AUTO_ROOT", str(tmp_path / "connect_root"))
    cfg_dir = common.connect_package_root() / "config"
    cfg_dir.mkdir(parents=True, exist_ok=True)
    (cfg_dir / "mcp_config.yaml").write_text("mcp:\n  enabled: true\n", encoding="utf-8")

    class _Svc:
        @classmethod
        def from_config(cls, _cfg):
            return cls()

        def status(self):
            return {"ok": True, "service": "ready"}

    monkeypatch.setattr(status, "MemoryServiceInterface", _Svc)
    monkeypatch.setattr(status, "selected_targets", lambda _t: ["claude_desktop", "generic_json"])
    monkeypatch.setattr(
        status,
        "target_paths",
        lambda _name: {
            "claude_desktop": tmp_path / "claude.json",
            "generic_json": tmp_path / "export.json",
        },
    )
    monkeypatch.setattr(
        status,
        "target_discovery",
        lambda _t: {
            "claude_desktop": {"resolved": str(tmp_path / "claude.json"), "resolved_exists": False},
            "generic_json": {"resolved": str(tmp_path / "export.json"), "resolved_exists": False},
        },
    )

    out = status.main("all")
    assert out["ok"] is True
    assert out["target"] == "all"
    assert "target_profiles" in out
    assert "claude_desktop" in out["target_profiles"]


def test_connect_flow_happy_path_and_target_readiness(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("OPENCLAW_MEMORY_AUTO_ROOT", str(tmp_path / "connect_root"))
    package_cfg = common.connect_package_root() / "config" / "mcp_config.yaml"
    package_cfg.parent.mkdir(parents=True, exist_ok=True)
    package_cfg.write_text("mcp:\n  enabled: true\n", encoding="utf-8")

    monkeypatch.setattr(connect, "run_smoke_test", lambda _cfg: {"ok": True, "steps": []})
    monkeypatch.setattr(connect, "list_tools", lambda: ["t1", "t2", "t3", "t4", "t5"])
    monkeypatch.setattr(connect, "list_resources", lambda: ["r1", "r2", "r3"])
    monkeypatch.setattr(connect, "load_registry", lambda _p: {"a": {}})
    monkeypatch.setattr(connect, "generate_client_configs", lambda target="all": {"ok": True, "target": target})
    monkeypatch.setattr(connect, "apply_client_configs", lambda target="all": {"ok": True, "target": target})
    monkeypatch.setattr(
        connect,
        "verify_client_configs",
        lambda target="all": {
            "ok": True,
            "details": {
                "generic_json": {
                    "exists": True,
                    "has_ms8_server": True,
                    "command_ok": True,
                    "args_ok": True,
                    "legacy_path_found": False,
                }
            },
        },
    )
    monkeypatch.setattr(connect, "snippet_paths", lambda _target: {"generic_json": "generic.json"})
    monkeypatch.setattr(
        connect,
        "target_discovery",
        lambda _target: {"generic_json": {"strategy": "fixed_path", "resolved_exists": True, "candidates": []}},
    )
    monkeypatch.setattr(
        connect,
        "payload_for_target",
        lambda _name: {"mcpServers": {"ms8-memory": {"command": "python3", "args": ["-m", "ms8.connect"]}}},
    )

    out = connect.run_connect_flow({"mcp": {"enabled": True}}, target="generic_json")
    assert out["result"]["overall_ok"] is True
    assert any(step["name"] == "report" for step in out["steps"])
    assert out["target_readiness"]["counts"]["ready"] >= 1

    report_path = common.connect_root() / "runtime" / "connect_report.json"
    assert report_path.exists()
    saved = json.loads(report_path.read_text(encoding="utf-8"))
    assert "steps" in saved


def test_install_env_run(monkeypatch) -> None:
    monkeypatch.setattr(install_env.shutil, "which", lambda name: "/usr/bin/python3" if name == "python3" else None)
    out = install_env.run()
    assert out["ok"] is True
    assert out["deps"]["python3"]
