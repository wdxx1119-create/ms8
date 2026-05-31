from __future__ import annotations

from pathlib import Path

import pytest

from ms8.engine_core import config as cfg_mod


def test_default_workspace_dir_prefers_scored_runtime(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    fake_home = tmp_path / "home"
    modern = fake_home / ".ms8"
    legacy = fake_home / ".ms8_runtime"
    (legacy / "memory").mkdir(parents=True, exist_ok=True)
    (legacy / "memory" / "auto_memory_records.jsonl").write_text("x", encoding="utf-8")
    monkeypatch.setattr(cfg_mod.Path, "home", staticmethod(lambda: fake_home))
    monkeypatch.delenv("OPENCLAW_MEMORY_WORKSPACE", raising=False)
    monkeypatch.delenv("OPENCLAW_WORKSPACE", raising=False)
    monkeypatch.delenv("MS8_HOME", raising=False)
    assert cfg_mod._default_workspace_dir() == legacy
    # If env is set, env must win.
    monkeypatch.setenv("MS8_HOME", str(modern))
    assert cfg_mod._default_workspace_dir() == modern


def test_deep_merge_and_load_yaml_error(tmp_path: Path) -> None:
    merged = cfg_mod._deep_merge({"a": {"x": 1}, "b": 2}, {"a": {"y": 3}, "b": 4})
    assert merged == {"a": {"x": 1, "y": 3}, "b": 4}
    ws = tmp_path / "ws"
    ws.mkdir(parents=True, exist_ok=True)
    (ws / "config.yaml").write_text(":\nbad", encoding="utf-8")
    # Invalid YAML should be swallowed and return {}.
    assert cfg_mod.load_yaml_config(ws) == {}


def test_prefer_migrated_path_and_get_config_rewrites(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    workspace = tmp_path / "runtime"
    (workspace / "memory" / "db").mkdir(parents=True, exist_ok=True)
    (workspace / "memory" / "index").mkdir(parents=True, exist_ok=True)
    (workspace / "memory" / "security").mkdir(parents=True, exist_ok=True)
    (workspace / "memory" / "db" / "memory.db").write_text("", encoding="utf-8")
    (workspace / "memory" / "db" / "knowledge_graph.db").write_text("", encoding="utf-8")
    (workspace / "memory" / "index" / "whoosh_index").mkdir(parents=True, exist_ok=True)

    monkeypatch.setattr(cfg_mod, "_default_workspace_dir", lambda: workspace)

    base = {
        "memory": {
            "long_term": {"path": "legacy/memory.db"},
            "keyword": {"index_dir": "legacy/whoosh"},
            "git": {"repo_path": "memory/git"},
            "knowledge_graph": {"db_path": "legacy/kg.db"},
            "auto_memory": {"log_file": "memory/auto_memory_log.json"},
            "compression": {"report_dir": "memory/reports/compression"},
            "meta_cognition": {"report_dir": "memory/reports/meta", "task_log_file": "memory/meta_task_log.jsonl"},
            "subagents": {"log_dir": "memory/subagents/logs"},
            "learning": {"task_log_file": "memory/learning_log.jsonl"},
            "working_memory": {
                "persistence_file": "memory/working_memory.json",
                "usage_log_file": "memory/working_memory_usage.jsonl",
            },
            "maintenance": {
                "backup_dir": "backups",
                "state_file": "memory/maintenance_state.json",
                "sync_audit_file": "memory/sync_audit.jsonl",
            },
            "security": {
                "security_dir": "memory/security",
                "state_file": "memory/security/state.json",
                "key_material_file": "memory/security/keys.json",
                "recovery_material_file": "memory/security/recovery.json",
                "shadow": {"shadow_dir": "memory/security/shadow_data", "backup_dir": "~/.shadow_backup"},
                "encrypted_targets": ["memory/MEMORY.md", str(workspace / "memory" / "db" / "memory.db")],
            },
            "monitoring": {
                "daily_report_file": "memory/reports/daily.json",
                "daily_report_markdown": "memory/reports/daily.md",
                "alerts": {"alert_log_file": "memory/reports/alerts.jsonl"},
            },
        }
    }

    class _Engine:
        def __init__(self, *_a, **_k):
            pass

        def resolve(self):
            return base, {"status": "ok"}

    monkeypatch.setattr(cfg_mod, "ConfigPriorityEngine", _Engine)
    monkeypatch.setenv("OPENCLAW_MEMORY_SESSION_INGEST_ENABLED", "true")

    conf = cfg_mod.get_config()
    settings = conf["settings"]["memory"]
    assert settings["long_term"]["path"].endswith("memory/db/memory.db")
    assert settings["keyword"]["index_dir"].endswith("memory/index/whoosh_index")
    assert settings["knowledge_graph"]["db_path"].endswith("memory/db/knowledge_graph.db")
    assert settings["security"]["shadow"]["backup_dir"].endswith("memory/security/shadow_backup")
    assert settings["auto_memory"]["session_ingestion"]["enabled"] is True

