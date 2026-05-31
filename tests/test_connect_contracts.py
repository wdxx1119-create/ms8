from __future__ import annotations

import json
from pathlib import Path

from ms8.connect.mcp_server import mcp_server
from ms8.connect.mcp_server.memory_service_interface import (
    ERR_CORE_UNAVAILABLE,
    ERR_PROFILE_NOT_FOUND,
    ERR_PROFILE_PARSE,
    ERR_PROFILE_UNKNOWN,
    MemoryServiceInterface,
)
from ms8.connect.scripts.bootstrap import run_bootstrap
from ms8.connect.scripts.connect import run_connect_flow
from ms8.connect.scripts.rollback_client_configs import run as rollback_client_configs
from ms8.connect.scripts.smoke_test import run_smoke_test


class _FakeCore:
    def __init__(self) -> None:
        self.last_write = {}

    def write_gateway(self, content: str, source: str, category: str, write_daily_log: bool = True):
        self.last_write = {
            "content": content,
            "source": source,
            "category": category,
            "write_daily_log": bool(write_daily_log),
        }
        return {"status": "saved", "id": "r1"}

    def retrieve_memories(self, query: str, top_k: int = 5):
        return [
            {
                "id": "m1",
                "text": f"hit:{query}",
                "source": "user",
                "category": "note",
                "status": "accepted",
                "created_at": "2026-01-01T00:00:00Z",
            }
        ][:top_k]

    def get_response_memory_context(self, query: str):
        return {"memories": [{"text": f"ctx:{query}"}, {"text": "ctx:2"}]}

    def get_monitoring_status(self):
        return {"ok": True, "uptime": 1}


def _cfg(workspace: Path) -> dict:
    return {
        "memory_core": {"workspace": str(workspace)},
        "mcp": {"enabled": True},
    }


def test_service_returns_core_unavailable_error_code() -> None:
    svc = MemoryServiceInterface(config={}, core=None, core_error="boot failed")
    out = svc.status()
    assert out["ok"] is False
    assert out["error_code"] == ERR_CORE_UNAVAILABLE
    assert "boot failed" in out["error"]


def test_service_submit_query_context_status_success() -> None:
    core = _FakeCore()
    svc = MemoryServiceInterface(config={}, core=core)
    submit = svc.submit({"content": "hello", "source": "u", "category": "preference"})
    assert submit["ok"] is True
    assert core.last_write["content"] == "hello"
    assert core.last_write["category"] == "preference"

    query = svc.query("hello", top_k=1)
    assert query["ok"] is True
    assert query["count"] == 1
    assert query["results"][0]["text"] == "hit:hello"

    ctx = svc.context("hello", limit=1)
    assert ctx["ok"] is True
    assert len(ctx["context"]["memories"]) == 1

    status = svc.status()
    assert status["ok"] is True
    assert status["service"] == "ms8-connect"


def test_expression_state_round_increments_once_per_context_call(tmp_path: Path) -> None:
    core = _FakeCore()
    svc = MemoryServiceInterface(config=_cfg(tmp_path), core=core)
    out1 = svc.context("first call", limit=1)
    out2 = svc.context("second call", limit=1)
    assert out1["ok"] is True and out2["ok"] is True
    state_file = tmp_path / "memory" / "expression_router_state.json"
    assert state_file.exists()
    state = json.loads(state_file.read_text(encoding="utf-8"))
    assert int(state.get("current_round", 0) or 0) == 2


def test_context_prefers_core_expression_mode_payload() -> None:
    class _CoreWithExpression(_FakeCore):
        def get_response_memory_context(self, query: str):
            return {
                "memories": [{"text": f"ctx:{query}"}],
                "expression_mode": {
                    "mode": "light",
                    "confidence": 0.66,
                    "prompt_extra": "from-core",
                    "decision": {"mode": "light", "reason": "core_provided"},
                },
            }

    svc = MemoryServiceInterface(config={}, core=_CoreWithExpression())
    out = svc.context("hello", limit=1)
    assert out["ok"] is True
    expr = out["expression_mode"]
    assert expr["mode"] == "light"
    assert expr["prompt_extra"] == "from-core"
    assert expr["decision"]["reason"] == "core_provided"


def test_profile_error_codes(tmp_path: Path) -> None:
    svc = MemoryServiceInterface(config=_cfg(tmp_path), core=_FakeCore())
    out_missing = svc.profile("long-term")
    assert out_missing["ok"] is False
    assert out_missing["error_code"] == ERR_PROFILE_NOT_FOUND

    out_unknown = svc.profile("nope")
    assert out_unknown["ok"] is False
    assert out_unknown["error_code"] == ERR_PROFILE_UNKNOWN

    (tmp_path / "memory").mkdir(parents=True, exist_ok=True)
    broken = tmp_path / "memory" / "memory_blocks.json"
    broken.write_text("{", encoding="utf-8")
    out_parse = svc.profile("profile")
    assert out_parse["ok"] is False
    assert out_parse["error_code"] == ERR_PROFILE_PARSE


def test_mcp_server_call_tool_unknown(monkeypatch) -> None:
    class _Svc:
        pass

    monkeypatch.setattr(
        "ms8.connect.mcp_server.mcp_server.MemoryServiceInterface.from_config",
        lambda _cfg: _Svc(),
    )
    out = mcp_server.call_tool("does-not-exist", {}, config={"mcp": {"enabled": True}})
    assert out["ok"] is False
    assert "unknown_tool" in out["error"]


def test_mcp_server_readonly_submit_denied(monkeypatch) -> None:
    class _Svc:
        pass

    monkeypatch.setattr(
        "ms8.connect.mcp_server.mcp_server.MemoryServiceInterface.from_config",
        lambda _cfg: _Svc(),
    )
    monkeypatch.setenv("MS8_CONNECT_READONLY", "1")
    out = mcp_server.call_tool("submit", {"content": "x"}, config={"mcp": {"enabled": True}})
    assert out["ok"] is False
    assert out["error"] == "readonly_mode"
    monkeypatch.delenv("MS8_CONNECT_READONLY", raising=False)


def test_mcp_server_token_enforced(monkeypatch) -> None:
    class _Svc:
        pass

    monkeypatch.setattr(
        "ms8.connect.mcp_server.mcp_server.MemoryServiceInterface.from_config",
        lambda _cfg: _Svc(),
    )
    monkeypatch.setenv("MS8_CONNECT_CLIENT_TOKEN", "abc")
    out = mcp_server.call_tool("status", {"client": "t1", "token": "wrong"}, config={"mcp": {"enabled": True}})
    assert out["ok"] is False
    assert out["error"] == "invalid_client_token"
    monkeypatch.delenv("MS8_CONNECT_CLIENT_TOKEN", raising=False)


def test_mcp_server_submit_source_tagging_default(monkeypatch) -> None:
    import tempfile
    from pathlib import Path

    monkeypatch.setenv("OPENCLAW_MEMORY_AUTO_ROOT", str(Path(tempfile.mkdtemp(prefix="ms8_connect_test_"))))
    core = _FakeCore()
    svc = MemoryServiceInterface(config={}, core=core)
    monkeypatch.setattr(
        "ms8.connect.mcp_server.mcp_server.MemoryServiceInterface.from_config",
        lambda _cfg: svc,
    )
    out = mcp_server.call_tool("submit", {"content": "hello"}, config={"mcp": {"enabled": True}})
    assert out["ok"] is True
    assert core.last_write["source"] == "mcp:submit"


def test_connect_flow_report_shape(tmp_path: Path, monkeypatch) -> None:
    root = tmp_path / "connect_root"
    monkeypatch.setenv("OPENCLAW_MEMORY_AUTO_ROOT", str(root))
    monkeypatch.setenv("MS8_HOME", str(tmp_path / "workspace"))
    monkeypatch.setenv("OPENCLAW_MEMORY_FAST_START", "1")
    class _Svc:
        def submit(self, _p):
            return {"ok": True}

        def query(self, _q, top_k=5):
            return {"ok": True, "top_k": top_k}

        def context(self, _q, limit=5):
            return {"ok": True, "limit": limit}

        def status(self):
            return {"ok": True}

    monkeypatch.setattr(
        "ms8.connect.scripts.smoke_test.MemoryServiceInterface.from_config",
        lambda _cfg: _Svc(),
    )
    monkeypatch.setattr(
        "ms8.connect.scripts.connect.verify_client_configs",
        lambda target="all": {
            "ok": True,
            "details": {
                "openclaw": {
                    "path": str(tmp_path / "openclaw_mcp.json"),
                    "exists": True,
                    "has_mcpServers": True,
                    "has_ms8_server": True,
                    "command_ok": True,
                    "args_ok": True,
                    "legacy_path_found": False,
                }
            },
        },
    )
    report = run_connect_flow(
        config={
            "mcp": {"enabled": True},
            "memory_core": {"workspace": str(tmp_path / "workspace")},
        },
        target="openclaw",
    )
    assert report["result"]["overall_ok"] is True
    names = [s["name"] for s in report["steps"]]
    assert names[:6] == ["detect", "install", "configure", "smoke_test", "verify", "report"]
    readiness = report.get("target_readiness", {})
    assert readiness.get("target") == "openclaw"
    assert readiness.get("counts", {}).get("ready", 0) >= 1
    assert "openclaw" in readiness.get("profiles", {})
    saved = root / "runtime" / "connect_report.json"
    assert saved.exists()
    saved_obj = json.loads(saved.read_text(encoding="utf-8"))
    assert saved_obj["result"]["overall_ok"] is True


def test_smoke_test_step_shape(monkeypatch) -> None:
    class _Svc:
        def submit(self, _p):
            return {"ok": True}

        def query(self, _q, top_k=5):
            return {"ok": True, "top_k": top_k}

        def context(self, _q, limit=5):
            return {"ok": True, "limit": limit}

        def status(self):
            return {"ok": True}

    monkeypatch.setattr(
        "ms8.connect.scripts.smoke_test.MemoryServiceInterface.from_config",
        lambda _cfg: _Svc(),
    )
    out = run_smoke_test(config={"mcp": {"enabled": True}})
    assert out["ok"] is True


def test_connect_rollback_selective_removal_keeps_other_servers(monkeypatch, tmp_path: Path) -> None:
    target = tmp_path / "Library" / "Application Support" / "Claude" / "claude_desktop_config.json"
    monkeypatch.setattr(
        "ms8.connect.scripts.rollback_client_configs.target_paths",
        lambda _target="all": {"claude_desktop": target},
    )
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        json.dumps(
            {
                "mcpServers": {
                    "ms8-memory": {"command": "python", "args": ["-m", "ms8"]},
                    "other-tool": {"command": "node", "args": ["server.js"]},
                }
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    out = rollback_client_configs(target="claude_desktop")
    assert out["ok"] is True
    assert str(target) in out["modified"]
    payload = json.loads(target.read_text(encoding="utf-8"))
    servers = payload.get("mcpServers", {})
    assert "ms8-memory" not in servers
    assert "other-tool" in servers


def test_connect_rollback_dry_run_does_not_modify(monkeypatch, tmp_path: Path) -> None:
    target = tmp_path / ".cursor" / "mcp.json"
    monkeypatch.setattr(
        "ms8.connect.scripts.rollback_client_configs.target_paths",
        lambda _target="all": {"cursor": target},
    )
    target.parent.mkdir(parents=True, exist_ok=True)
    original = {"mcpServers": {"ms8-memory": {"command": "python"}, "x": {"command": "node"}}}
    target.write_text(json.dumps(original, ensure_ascii=False, indent=2), encoding="utf-8")
    out = rollback_client_configs(target="cursor", dry_run=True)
    assert out["ok"] is True
    assert str(target) not in out["modified"]
    current = json.loads(target.read_text(encoding="utf-8"))
    assert current == original


def test_connect_rollback_force_delete_full_config(monkeypatch, tmp_path: Path) -> None:
    target = tmp_path / ".windsurf" / "mcp.json"
    monkeypatch.setattr(
        "ms8.connect.scripts.rollback_client_configs.target_paths",
        lambda _target="all": {"windsurf": target},
    )
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps({"mcpServers": {"ms8-memory": {"command": "python"}}}), encoding="utf-8")
    out = rollback_client_configs(target="windsurf", force_delete_full_config=True)
    assert out["ok"] is True
    assert str(target) in out["removed"]
    assert not target.exists()


def test_connect_rollback_skips_missing_target(monkeypatch, tmp_path: Path) -> None:
    target = tmp_path / "missing.json"
    monkeypatch.setattr(
        "ms8.connect.scripts.rollback_client_configs.target_paths",
        lambda _target="all": {"claude_desktop": target},
    )
    out = rollback_client_configs(target="claude_desktop")
    assert out["ok"] is True
    assert out["modified"] == []
    assert out["removed"] == []
    assert any(item.get("action") == "skip_missing" for item in out["preview"])


def test_connect_rollback_skips_when_no_ms8_entry(monkeypatch, tmp_path: Path) -> None:
    target = tmp_path / "config.json"
    monkeypatch.setattr(
        "ms8.connect.scripts.rollback_client_configs.target_paths",
        lambda _target="all": {"cursor": target},
    )
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps({"mcpServers": {"other": {"command": "node"}}}), encoding="utf-8")
    out = rollback_client_configs(target="cursor")
    assert out["ok"] is True
    assert out["modified"] == []
    assert any(item.get("action") == "skip_no_ms8_entry" for item in out["preview"])


def test_connect_rollback_invalid_json_marks_failed(monkeypatch, tmp_path: Path) -> None:
    target = tmp_path / "broken.json"
    monkeypatch.setattr(
        "ms8.connect.scripts.rollback_client_configs.target_paths",
        lambda _target="all": {"windsurf": target},
    )
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("{broken", encoding="utf-8")
    out = rollback_client_configs(target="windsurf", force_delete_full_config=True)
    assert out["ok"] is True
    # invalid json still allows full-delete path; file is removed
    assert str(target) in out["removed"]


def test_connect_rollback_write_failure_restores_from_backup(monkeypatch, tmp_path: Path) -> None:
    from ms8.connect.scripts import rollback_client_configs as rcc

    target = tmp_path / "restore.json"
    monkeypatch.setattr(
        "ms8.connect.scripts.rollback_client_configs.target_paths",
        lambda _target="all": {"claude_desktop": target},
    )
    target.parent.mkdir(parents=True, exist_ok=True)
    original = {"mcpServers": {"ms8-memory": {"command": "python"}, "other": {"command": "node"}}}
    target.write_text(json.dumps(original, ensure_ascii=False, indent=2), encoding="utf-8")

    def _boom(_path, _payload):
        raise OSError("write boom")

    monkeypatch.setattr(rcc, "_write_json", _boom)
    out = rollback_client_configs(target="claude_desktop")
    assert out["ok"] is False
    assert len(out["failed"]) == 1
    restored = json.loads(target.read_text(encoding="utf-8"))
    assert restored == original


def test_bootstrap_dry_run_creates_report(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("OPENCLAW_MEMORY_AUTO_ROOT", str(tmp_path / "connect_root"))
    out = run_bootstrap(target="claude_desktop", dry_run=True)
    assert out["ok"] is True
    assert out["dry_run"] is True
    report = tmp_path / "connect_root" / "runtime" / "bootstrap_report.json"
    assert report.exists()
    first_install_json = tmp_path / "connect_root" / "runtime" / "first_install_connect_report.json"
    first_install_txt = tmp_path / "connect_root" / "runtime" / "first_install_connect_report.txt"
    assert first_install_json.exists()
    assert first_install_txt.exists()


def test_bootstrap_auto_fix_retry_path(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("OPENCLAW_MEMORY_AUTO_ROOT", str(tmp_path / "connect_root"))
    monkeypatch.setattr("ms8.connect.scripts.bootstrap._target_exists", lambda _t: True)
    monkeypatch.setattr(
        "ms8.connect.scripts.bootstrap.run_connect_flow",
        lambda target="all": {
            "result": {"overall_ok": True},
            "target_readiness": {
                "counts": {"ready": 1, "degraded": 0, "manual": 0},
                "profiles": {"claude_desktop": {"status": "ready", "path": "/tmp/mock", "guidance": "ok"}},
            },
        },
    )
    monkeypatch.setattr("ms8.connect.scripts.bootstrap.generate_client_configs", lambda target="all": {"ok": True})
    monkeypatch.setattr("ms8.connect.scripts.bootstrap.apply_client_configs", lambda target="all": {"ok": True})
    seq = iter([{"ok": False}, {"ok": True}])
    monkeypatch.setattr("ms8.connect.scripts.bootstrap.verify_client_configs", lambda target="all": next(seq))
    monkeypatch.setattr("ms8.connect.scripts.bootstrap.run_smoke_test", lambda: {"ok": True})
    out = run_bootstrap(target="claude_desktop", auto_fix=True)
    assert out["ok"] is True
    names = [s["name"] for s in out["steps"]]
    assert "auto_fix_retry" in names


def test_bootstrap_skips_when_claude_not_detected(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("OPENCLAW_MEMORY_AUTO_ROOT", str(tmp_path / "connect_root"))
    monkeypatch.setattr("ms8.connect.scripts.bootstrap._target_exists", lambda _t: False)
    out = run_bootstrap(target="claude_desktop", auto_fix=True, silent=True)
    assert out["ok"] is True
    assert out.get("skipped") is True
    assert out.get("reason") == "target_not_installed_or_config_not_present"
    assert "first_install_report" in out


def test_bootstrap_skips_when_codex_not_detected(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("OPENCLAW_MEMORY_AUTO_ROOT", str(tmp_path / "connect_root"))
    monkeypatch.setattr("ms8.connect.scripts.bootstrap._target_exists", lambda _t: False)
    out = run_bootstrap(target="codex", auto_fix=True, silent=True)
    assert out["ok"] is True
    assert out.get("skipped") is True
    assert out.get("reason") == "target_not_installed_or_config_not_present"
    assert "Codex config not detected" in str(out.get("hint", ""))
