from __future__ import annotations

import json
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

from ms8.absorb.project_memory.cli import run_project_memory_cli
from ms8.absorb.project_memory.health import project_status
from ms8.absorb.project_memory.parser import parse_document
from ms8.absorb.project_memory.search import search_registered_projects
from ms8.absorb.project_memory.watch import run_project_cycle
from ms8.ask import run_ask


def _last_json_payload(out: str) -> dict:
    start = out.find("{")
    assert start >= 0, out
    return json.loads(out[start:])


def test_project_memory_init_scan_status_build_search(tmp_path: Path, monkeypatch, capsys) -> None:
    monkeypatch.setenv("MS8_HOME", str(tmp_path / ".ms8"))
    root = tmp_path / "proj"
    docs = root / "docs"
    src = root / "src"
    docs.mkdir(parents=True)
    src.mkdir(parents=True)
    (root / "README.md").write_text("# Demo\nMS8 uses SQLite for project memory.", encoding="utf-8")
    (docs / "architecture.rst").write_text("MemoryCore depends on KnowledgeGraph\n", encoding="utf-8")
    (root / "settings.toml").write_text('title = "demo"\n[tool]\nname = "ms8"\n', encoding="utf-8")
    (src / "core.py").write_text('"""Core module docs"""\n# engine comment\n', encoding="utf-8")

    assert run_project_memory_cli(SimpleNamespace(pm_cmd="init", project_dir=str(root), name="demo")) == 0
    init_payload = _last_json_payload(capsys.readouterr().out)
    assert init_payload["name"] == "demo"

    assert run_project_memory_cli(SimpleNamespace(pm_cmd="scan", name="demo")) == 0
    scan_payload = _last_json_payload(capsys.readouterr().out)
    assert scan_payload["status"] == "scan_complete"
    assert scan_payload["content_db_ready"] is True
    assert scan_payload["search_index_ready"] is False
    assert scan_payload["index_status"] == "stale"
    assert scan_payload["files_scanned"] >= 4
    assert scan_payload["chunks_created"] >= 4

    assert run_project_memory_cli(SimpleNamespace(pm_cmd="status", name="demo")) == 0
    status_payload = _last_json_payload(capsys.readouterr().out)
    assert status_payload["db_readable"] is True
    assert status_payload["whoosh_exists"] is True
    assert status_payload["search_index_ready"] is False
    assert status_payload["file_count"] >= 4

    assert run_project_memory_cli(SimpleNamespace(pm_cmd="index", name="demo")) == 0
    index_payload = _last_json_payload(capsys.readouterr().out)
    assert index_payload["status"] == "indexed"
    assert index_payload["index_state"]["status"] == "ready"
    assert index_payload["index_mode"] in {"full_rebuild", "incremental"}

    assert run_project_memory_cli(SimpleNamespace(pm_cmd="status", name="demo")) == 0
    status_after_index = _last_json_payload(capsys.readouterr().out)
    assert status_after_index["search_index_ready"] is True
    assert status_after_index["index_status"] == "ready"

    assert run_project_memory_cli(SimpleNamespace(pm_cmd="build", name="demo")) == 0
    build_payload = _last_json_payload(capsys.readouterr().out)
    ai_context = Path(build_payload["output"]["ai_context"])
    project_summary = Path(build_payload["output"]["project_summary"])
    relations = Path(build_payload["output"]["relations"])
    reading_order = Path(build_payload["output"]["reading_order"])
    hot_files = Path(build_payload["output"]["hot_files"])
    code_index = Path(build_payload["output"]["code_index"])
    assert ai_context.exists()
    assert project_summary.exists()
    assert relations.exists()
    assert reading_order.exists()
    assert hot_files.exists()
    assert code_index.exists()
    assert "Project Context: demo" in ai_context.read_text(encoding="utf-8")
    assert "Recommended Reading Order" in ai_context.read_text(encoding="utf-8")
    assert "Python Code Map" in ai_context.read_text(encoding="utf-8")
    assert "Project Summary: demo" in project_summary.read_text(encoding="utf-8")
    assert "Python Modules" in project_summary.read_text(encoding="utf-8")
    assert build_payload["stats"]["python_modules_count"] >= 1
    code_index_payload = json.loads(code_index.read_text(encoding="utf-8"))
    assert code_index_payload["modules"]
    assert any(module["path"] == "src/core.py" for module in code_index_payload["modules"])

    assert run_project_memory_cli(SimpleNamespace(pm_cmd="doctor", name="demo")) == 0
    doctor_payload = _last_json_payload(capsys.readouterr().out)
    assert doctor_payload["status"] == "healthy"
    assert doctor_payload["summary"]["total"] >= 5

    assert run_project_memory_cli(SimpleNamespace(pm_cmd="search", query="KnowledgeGraph", name="demo", limit=10, pretty=False)) == 0
    search_payload = _last_json_payload(capsys.readouterr().out)
    assert search_payload["matches"]
    assert search_payload["matches"][0]["relative_path"].endswith("architecture.rst")

    assert not (tmp_path / ".ms8" / "memory" / "auto_memory_records.jsonl").exists()


def test_project_memory_pretty_search_and_registry_auto_pick(tmp_path: Path, monkeypatch, capsys) -> None:
    monkeypatch.setenv("MS8_HOME", str(tmp_path / ".ms8"))
    root = tmp_path / "one"
    root.mkdir()
    (root / "README.md").write_text("SQLite fallback search unique phrase", encoding="utf-8")

    assert run_project_memory_cli(SimpleNamespace(pm_cmd="init", project_dir=str(root), name=None)) == 0
    capsys.readouterr()
    assert run_project_memory_cli(SimpleNamespace(pm_cmd="scan", name=None)) == 0
    capsys.readouterr()
    assert run_project_memory_cli(SimpleNamespace(pm_cmd="index", name=None)) == 0
    capsys.readouterr()
    assert run_project_memory_cli(SimpleNamespace(pm_cmd="search", query="unique phrase", name=None, limit=10, pretty=True)) == 0
    out = capsys.readouterr().out
    assert "PROJECT_MEMORY_SEARCH" in out
    assert "matches=" in out


def test_project_memory_search_uses_sqlite_fallback_when_index_stale(tmp_path: Path, monkeypatch, capsys) -> None:
    monkeypatch.setenv("MS8_HOME", str(tmp_path / ".ms8"))
    root = tmp_path / "fallback"
    root.mkdir()
    (root / "README.md").write_text("fallback only phrase", encoding="utf-8")

    assert run_project_memory_cli(SimpleNamespace(pm_cmd="init", project_dir=str(root), name="fallback")) == 0
    capsys.readouterr()
    assert run_project_memory_cli(SimpleNamespace(pm_cmd="scan", name="fallback")) == 0
    capsys.readouterr()
    assert run_project_memory_cli(SimpleNamespace(pm_cmd="search", query="fallback only", name="fallback", limit=10, pretty=False)) == 0
    payload = _last_json_payload(capsys.readouterr().out)
    assert payload["matches"]
    assert payload["matches"][0]["search_backend"] == "sqlite"


def test_project_memory_incremental_index_updates_changed_file(tmp_path: Path, monkeypatch, capsys) -> None:
    monkeypatch.setenv("MS8_HOME", str(tmp_path / ".ms8"))
    root = tmp_path / "inc"
    root.mkdir()
    readme = root / "README.md"
    readme.write_text("first phrase", encoding="utf-8")

    assert run_project_memory_cli(SimpleNamespace(pm_cmd="init", project_dir=str(root), name="inc")) == 0
    capsys.readouterr()
    assert run_project_memory_cli(SimpleNamespace(pm_cmd="scan", name="inc")) == 0
    capsys.readouterr()
    assert run_project_memory_cli(SimpleNamespace(pm_cmd="index", name="inc")) == 0
    first_index = _last_json_payload(capsys.readouterr().out)
    assert first_index["index_mode"] == "full_rebuild"

    readme.write_text("second phrase updated", encoding="utf-8")
    assert run_project_memory_cli(SimpleNamespace(pm_cmd="scan", name="inc")) == 0
    scan_payload = _last_json_payload(capsys.readouterr().out)
    assert scan_payload["files_scanned"] == 1
    assert scan_payload["files_unchanged"] == 0

    assert run_project_memory_cli(SimpleNamespace(pm_cmd="index", name="inc")) == 0
    second_index = _last_json_payload(capsys.readouterr().out)
    assert second_index["index_mode"] == "incremental"
    assert "README.md" in second_index["updated_paths"]

    assert run_project_memory_cli(SimpleNamespace(pm_cmd="search", query="second phrase", name="inc", limit=10, pretty=False)) == 0
    payload = _last_json_payload(capsys.readouterr().out)
    assert payload["matches"]
    assert "second phrase updated" in payload["matches"][0]["text"]


def test_project_memory_second_scan_skips_reparse_for_unchanged_file(tmp_path: Path, monkeypatch, capsys) -> None:
    monkeypatch.setenv("MS8_HOME", str(tmp_path / ".ms8"))
    root = tmp_path / "unchanged"
    root.mkdir()
    readme = root / "README.md"
    readme.write_text("unchanged body", encoding="utf-8")

    assert run_project_memory_cli(SimpleNamespace(pm_cmd="init", project_dir=str(root), name="unchanged")) == 0
    capsys.readouterr()
    assert run_project_memory_cli(SimpleNamespace(pm_cmd="scan", name="unchanged")) == 0
    capsys.readouterr()

    def _boom(_path: Path):
        raise AssertionError("parse_document should not run for unchanged file")

    monkeypatch.setattr("ms8.absorb.project_memory.scanner.parse_document", _boom)
    assert run_project_memory_cli(SimpleNamespace(pm_cmd="scan", name="unchanged")) == 0
    payload = _last_json_payload(capsys.readouterr().out)
    assert payload["files_unchanged"] == 1
    assert payload["files_scanned"] == 0


def test_project_memory_parser_supports_rst_toml_ini_docx(tmp_path: Path) -> None:
    rst = tmp_path / "note.rst"
    toml = tmp_path / "cfg.toml"
    ini = tmp_path / "tool.ini"
    docx = tmp_path / "brief.docx"
    rst.write_text("Section\n=======\nbody text\n", encoding="utf-8")
    toml.write_text('name = "ms8"\n[project]\nversion = "0.1"\n', encoding="utf-8")
    ini.write_text("[service]\nname = ms8\n", encoding="utf-8")
    document_mod = pytest.importorskip("docx")
    document = document_mod.Document()
    document.add_paragraph("Project memory docx brief")
    document.save(str(docx))

    assert parse_document(rst).parse_status == "parsed"
    assert '"version": "0.1"' in parse_document(toml).content_text
    assert "name = ms8" in parse_document(ini).content_text
    assert "Project memory docx brief" in parse_document(docx).content_text


def test_project_memory_run_cycle_scans_indexes_and_builds(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("MS8_HOME", str(tmp_path / ".ms8"))
    root = tmp_path / "cycle"
    src = root / "src"
    src.mkdir(parents=True)
    (root / "README.md").write_text("# Cycle\n", encoding="utf-8")
    (src / "mod.py").write_text('"""Module docs"""\n\ndef answer():\n    return 42\n', encoding="utf-8")

    init_args = SimpleNamespace(pm_cmd="init", project_dir=str(root), name="cycle")
    assert run_project_memory_cli(init_args) == 0

    from ms8.absorb.project_memory.scope import project_dir_paths

    paths = project_dir_paths("cycle")
    payload = run_project_cycle(
        project_name="cycle",
        project_root=root,
        db_path=paths["db_path"],
        whoosh_dir=paths["whoosh_dir"],
        output_dir=paths["output_dir"],
        index_state_path=paths["index_state_path"],
        build_state_path=paths["build_state_path"],
        auto_index=True,
        auto_build=True,
    )
    assert payload["ok"] is True
    assert payload["scan"]["status"] == "scan_complete"
    assert payload["index"]["status"] == "indexed"
    assert payload["build"]["stats"]["python_modules_count"] >= 1


def test_project_memory_scan_accepts_docx(tmp_path: Path, monkeypatch, capsys) -> None:
    monkeypatch.setenv("MS8_HOME", str(tmp_path / ".ms8"))
    document_mod = pytest.importorskip("docx")
    root = tmp_path / "docxproj"
    root.mkdir()
    brief = root / "brief.docx"
    document = document_mod.Document()
    document.add_paragraph("Absorb docx support")
    document.save(str(brief))

    assert run_project_memory_cli(SimpleNamespace(pm_cmd="init", project_dir=str(root), name="docxproj")) == 0
    capsys.readouterr()
    assert run_project_memory_cli(SimpleNamespace(pm_cmd="scan", name="docxproj")) == 0
    scan_payload = _last_json_payload(capsys.readouterr().out)
    assert scan_payload["files_scanned"] >= 1
    assert run_project_memory_cli(SimpleNamespace(pm_cmd="search", query="Absorb docx support", name="docxproj", limit=10, pretty=False)) == 0
    payload = _last_json_payload(capsys.readouterr().out)
    assert payload["matches"]
    assert payload["matches"][0]["relative_path"].endswith("brief.docx")


def test_project_memory_registered_search_aggregates_hits(tmp_path: Path, monkeypatch, capsys) -> None:
    monkeypatch.setenv("MS8_HOME", str(tmp_path / ".ms8"))
    alpha = tmp_path / "alpha"
    beta = tmp_path / "beta"
    alpha.mkdir()
    beta.mkdir()
    (alpha / "README.md").write_text("shared query alpha", encoding="utf-8")
    (beta / "README.md").write_text("shared query beta", encoding="utf-8")

    assert run_project_memory_cli(SimpleNamespace(pm_cmd="init", project_dir=str(alpha), name="alpha")) == 0
    capsys.readouterr()
    assert run_project_memory_cli(SimpleNamespace(pm_cmd="init", project_dir=str(beta), name="beta")) == 0
    capsys.readouterr()
    assert run_project_memory_cli(SimpleNamespace(pm_cmd="scan", name="alpha")) == 0
    capsys.readouterr()
    assert run_project_memory_cli(SimpleNamespace(pm_cmd="scan", name="beta")) == 0
    capsys.readouterr()

    rows = search_registered_projects("shared query", limit=10)
    assert len(rows) >= 2
    assert {"alpha", "beta"} <= {str(row.get("project_name", "")) for row in rows}


def test_run_ask_includes_project_memory_hits(tmp_path: Path, monkeypatch, capsys) -> None:
    monkeypatch.setenv("MS8_HOME", str(tmp_path / ".ms8"))
    root = tmp_path / "askproj"
    root.mkdir()
    (root / "README.md").write_text("project bridge unique phrase", encoding="utf-8")

    assert run_project_memory_cli(SimpleNamespace(pm_cmd="init", project_dir=str(root), name="askproj")) == 0
    capsys.readouterr()
    assert run_project_memory_cli(SimpleNamespace(pm_cmd="scan", name="askproj")) == 0
    capsys.readouterr()

    assert run_ask("project bridge unique phrase", limit=5) == 0
    out = capsys.readouterr().out
    assert "project_memory:askproj" in out


def test_project_memory_submit_summary_writes_main_memory_once(tmp_path: Path, monkeypatch, capsys) -> None:
    monkeypatch.setenv("MS8_HOME", str(tmp_path / ".ms8"))
    root = tmp_path / "submitdemo"
    root.mkdir()
    (root / "README.md").write_text("# Submit Demo\n\nproject summary payload", encoding="utf-8")

    written: list[str] = []
    monkeypatch.setattr(
        "ms8.runtime.write_memory",
        lambda text, source="project_memory": written.append(text) or {"id": f"pm_{len(written)}", "text": text, "source": source},
    )

    assert run_project_memory_cli(SimpleNamespace(pm_cmd="init", project_dir=str(root), name="submitdemo")) == 0
    capsys.readouterr()
    assert run_project_memory_cli(SimpleNamespace(pm_cmd="scan", name="submitdemo")) == 0
    capsys.readouterr()
    assert run_project_memory_cli(SimpleNamespace(pm_cmd="build", name="submitdemo")) == 0
    capsys.readouterr()
    assert run_project_memory_cli(SimpleNamespace(pm_cmd="submit", name="submitdemo", force=False)) == 0
    out = capsys.readouterr().out
    assert '"status": "submitted"' in out
    assert len(written) == 1
    assert "Project memory summary [submitdemo]" in written[0]

    assert run_project_memory_cli(SimpleNamespace(pm_cmd="submit", name="submitdemo", force=False)) == 0
    out2 = capsys.readouterr().out
    assert '"status": "noop"' in out2
    assert len(written) == 1


def test_project_memory_watch_can_auto_submit_summary(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("MS8_HOME", str(tmp_path / ".ms8"))
    root = tmp_path / "watchsubmit"
    root.mkdir()
    (root / "README.md").write_text("# Watch Demo\n\nwatch submit payload", encoding="utf-8")

    written: list[str] = []
    monkeypatch.setattr(
        "ms8.runtime.write_memory",
        lambda text, source="project_memory": written.append(text) or {"id": f"pm_{len(written)}", "text": text, "source": source},
    )

    assert run_project_memory_cli(SimpleNamespace(pm_cmd="init", project_dir=str(root), name="watchsubmit")) == 0

    from ms8.absorb.project_memory.scope import project_dir_paths

    paths = project_dir_paths("watchsubmit")
    payload = run_project_cycle(
        project_name="watchsubmit",
        project_root=root,
        db_path=paths["db_path"],
        whoosh_dir=paths["whoosh_dir"],
        output_dir=paths["output_dir"],
        index_state_path=paths["index_state_path"],
        build_state_path=paths["build_state_path"],
        auto_index=True,
        auto_build=True,
        auto_submit_main_memory=True,
    )
    assert payload["ok"] is True
    assert payload["main_memory_submit"]["status"] == "submitted"
    assert len(written) == 1


def test_project_memory_watch_cycle_failure_is_captured(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("MS8_HOME", str(tmp_path / ".ms8"))
    root = tmp_path / "watchfail"
    root.mkdir()
    (root / "README.md").write_text("# Fail Demo\n\npayload", encoding="utf-8")

    assert run_project_memory_cli(SimpleNamespace(pm_cmd="init", project_dir=str(root), name="watchfail")) == 0

    from ms8.absorb.project_memory.scope import project_dir_paths
    from ms8.absorb.project_memory.watch import _safe_run_project_cycle

    paths = project_dir_paths("watchfail")
    monkeypatch.setattr(
        "ms8.absorb.project_memory.watch.run_project_cycle",
        lambda **_kwargs: (_ for _ in ()).throw(RuntimeError("synthetic watch failure")),
    )
    payload = _safe_run_project_cycle(
        project_name="watchfail",
        project_root=root,
        db_path=paths["db_path"],
        whoosh_dir=paths["whoosh_dir"],
        output_dir=paths["output_dir"],
        index_state_path=paths["index_state_path"],
        build_state_path=paths["build_state_path"],
    )
    assert payload["ok"] is False
    assert payload["status"] == "cycle_failed"


def test_project_memory_status_reports_auto_write_flag(tmp_path: Path, monkeypatch, capsys) -> None:
    monkeypatch.setenv("MS8_HOME", str(tmp_path / ".ms8"))
    root = tmp_path / "autoflag"
    root.mkdir()
    (root / "README.md").write_text("# Auto Flag\n\nstatus", encoding="utf-8")

    assert run_project_memory_cli(SimpleNamespace(pm_cmd="init", project_dir=str(root), name="autoflag")) == 0
    capsys.readouterr()
    assert run_project_memory_cli(SimpleNamespace(pm_cmd="enable-auto-write", name="autoflag")) == 0
    capsys.readouterr()
    assert run_project_memory_cli(SimpleNamespace(pm_cmd="status", name="autoflag")) == 0
    out = capsys.readouterr().out
    assert '"auto_write_main_memory": true' in out


def test_project_memory_status_includes_watch_state(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("MS8_HOME", str(tmp_path / ".ms8"))
    root = tmp_path / "watchstate"
    root.mkdir()
    (root / "README.md").write_text("# Watch State\n", encoding="utf-8")

    assert run_project_memory_cli(SimpleNamespace(pm_cmd="init", project_dir=str(root), name="watchstate")) == 0

    from ms8.absorb.project_memory.scope import project_dir_paths
    from ms8.absorb.project_memory.watch import _update_watch_state

    paths = project_dir_paths("watchstate")
    _update_watch_state(
        paths["watch_state_path"],
        running=True,
        started_at=0.0,
        cycles_run=3,
        last_payload={"ok": True, "status": "indexed"},
    )
    payload = project_status(
        name="watchstate",
        root=str(root),
        db_path=paths["db_path"],
        whoosh_dir=paths["whoosh_dir"],
        output_dir=paths["output_dir"],
        index_state_path=paths["index_state_path"],
        build_state_path=paths["build_state_path"],
    )
    watch_state = payload["watch_state"]
    assert watch_state["running"] is True
    assert watch_state["cycles_run"] == 3
    assert watch_state["last_status"] == "indexed"


def test_project_memory_status_includes_service_state(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("MS8_HOME", str(tmp_path / ".ms8"))
    root = tmp_path / "servicestate"
    root.mkdir()
    (root / "README.md").write_text("# Service State\n", encoding="utf-8")

    assert run_project_memory_cli(SimpleNamespace(pm_cmd="init", project_dir=str(root), name="servicestate")) == 0

    from ms8.absorb.project_memory.scope import project_dir_paths

    paths = project_dir_paths("servicestate")
    monkeypatch.setattr(
        "ms8.service.project_memory_service_status",
        lambda name: {
            "ok": True,
            "project": name,
            "label": "com.ms8.project-memory.servicestate.watch",
            "installed": True,
            "running": True,
            "plist": "/tmp/demo.plist",
        },
    )
    payload = project_status(
        name="servicestate",
        root=str(root),
        db_path=paths["db_path"],
        whoosh_dir=paths["whoosh_dir"],
        output_dir=paths["output_dir"],
        index_state_path=paths["index_state_path"],
        build_state_path=paths["build_state_path"],
    )
    service_state = payload["service_state"]
    assert service_state["installed"] is True
    assert service_state["running"] is True
    assert payload["background_service_ready"] is True
    assert payload["recommended_runtime_mode"] == "background_service"


def test_project_memory_status_prefers_foreground_watch_when_windows_service_blocked(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("MS8_HOME", str(tmp_path / ".ms8"))
    root = tmp_path / "foregroundstate"
    root.mkdir()
    (root / "README.md").write_text("# Foreground State\n", encoding="utf-8")

    assert run_project_memory_cli(SimpleNamespace(pm_cmd="init", project_dir=str(root), name="foregroundstate")) == 0

    from ms8.absorb.project_memory.scope import project_dir_paths

    paths = project_dir_paths("foregroundstate")
    monkeypatch.setattr(
        "ms8.service.project_memory_service_status",
        lambda name: {
            "ok": False,
            "project": name,
            "label": "com.ms8.project-memory.foregroundstate.watch",
            "installed": False,
            "running": False,
            "backend": "schtasks",
            "reason_code": "windows_service_permission_denied",
            "error_kind": "permission_denied",
        },
    )
    monkeypatch.setattr("ms8.absorb.project_memory.health._watch_support", lambda: {"installed": True, "backend": "watchdog"})

    payload = project_status(
        name="foregroundstate",
        root=str(root),
        db_path=paths["db_path"],
        whoosh_dir=paths["whoosh_dir"],
        output_dir=paths["output_dir"],
        index_state_path=paths["index_state_path"],
        build_state_path=paths["build_state_path"],
    )

    assert payload["background_service_ready"] is False
    assert payload["foreground_watch_available"] is True
    assert payload["recommended_runtime_mode"] == "foreground_watch"
    assert "Windows permissions" in payload["runtime_hint"]


def test_project_memory_status_includes_sqlite_and_whoosh_health(tmp_path: Path, monkeypatch, capsys) -> None:
    monkeypatch.setenv("MS8_HOME", str(tmp_path / ".ms8"))
    root = tmp_path / "healthstate"
    root.mkdir()
    (root / "README.md").write_text("# Health State\nsqlite and whoosh", encoding="utf-8")

    assert run_project_memory_cli(SimpleNamespace(pm_cmd="init", project_dir=str(root), name="healthstate")) == 0
    capsys.readouterr()
    assert run_project_memory_cli(SimpleNamespace(pm_cmd="scan", name="healthstate")) == 0
    capsys.readouterr()
    assert run_project_memory_cli(SimpleNamespace(pm_cmd="index", name="healthstate")) == 0
    capsys.readouterr()

    from ms8.absorb.project_memory.scope import project_dir_paths

    paths = project_dir_paths("healthstate")
    payload = project_status(
        name="healthstate",
        root=str(root),
        db_path=paths["db_path"],
        whoosh_dir=paths["whoosh_dir"],
        output_dir=paths["output_dir"],
        index_state_path=paths["index_state_path"],
        build_state_path=paths["build_state_path"],
    )

    assert payload["db_readable"] is True
    assert payload["db_writable"] is True
    assert payload["db_query_ok"] is True
    assert payload["content_db_ready"] is True
    assert payload["sqlite_health"]["journal_mode"] == "wal"
    assert payload["sqlite_health"]["busy_timeout_ms"] == 5000
    assert payload["whoosh_exists"] is True
    assert payload["whoosh_file_count"] >= 1


def test_project_memory_build_outputs_are_high_signal_and_truncated(tmp_path: Path, monkeypatch, capsys) -> None:
    monkeypatch.setenv("MS8_HOME", str(tmp_path / ".ms8"))
    root = tmp_path / "bigproj"
    root.mkdir()
    for idx in range(30):
        body = ("section %d " % idx) + ("x" * 1800)
        (root / f"doc_{idx}.md").write_text(body, encoding="utf-8")
    (root / "README.md").write_text("# Big Project\n\noverview", encoding="utf-8")

    assert run_project_memory_cli(SimpleNamespace(pm_cmd="init", project_dir=str(root), name="bigproj")) == 0
    capsys.readouterr()
    assert run_project_memory_cli(SimpleNamespace(pm_cmd="scan", name="bigproj")) == 0
    capsys.readouterr()
    assert run_project_memory_cli(SimpleNamespace(pm_cmd="build", name="bigproj")) == 0
    build_payload = _last_json_payload(capsys.readouterr().out)

    ai_context = Path(build_payload["output"]["ai_context"]).read_text(encoding="utf-8")
    project_summary = Path(build_payload["output"]["project_summary"]).read_text(encoding="utf-8")
    stats = build_payload["stats"]

    assert "## Focused Document Contents" in ai_context
    assert "[truncated]" in ai_context
    assert "## Relation Summary" in project_summary
    assert stats["document_sections_included"] <= 24


def test_project_memory_build_is_up_to_date_when_nothing_changed(tmp_path: Path, monkeypatch, capsys) -> None:
    monkeypatch.setenv("MS8_HOME", str(tmp_path / ".ms8"))
    root = tmp_path / "uptodate"
    root.mkdir()
    (root / "README.md").write_text("# Stable\n\nnothing changed", encoding="utf-8")

    assert run_project_memory_cli(SimpleNamespace(pm_cmd="init", project_dir=str(root), name="uptodate")) == 0
    capsys.readouterr()
    assert run_project_memory_cli(SimpleNamespace(pm_cmd="scan", name="uptodate")) == 0
    capsys.readouterr()
    assert run_project_memory_cli(SimpleNamespace(pm_cmd="build", name="uptodate")) == 0
    first_build = _last_json_payload(capsys.readouterr().out)
    assert first_build["status"] == "built"

    assert run_project_memory_cli(SimpleNamespace(pm_cmd="build", name="uptodate")) == 0
    second_build = _last_json_payload(capsys.readouterr().out)
    assert second_build["status"] == "up_to_date"
    assert second_build["stats"]["rebuilt_file_entries"] == 0


def test_project_memory_incremental_build_rebuilds_only_changed_python_file(tmp_path: Path, monkeypatch, capsys) -> None:
    monkeypatch.setenv("MS8_HOME", str(tmp_path / ".ms8"))
    root = tmp_path / "incbuild"
    src = root / "src"
    src.mkdir(parents=True)
    (root / "README.md").write_text("# Project\n", encoding="utf-8")
    mod_a = src / "a.py"
    mod_b = src / "b.py"
    mod_a.write_text("def a():\n    return 1\n", encoding="utf-8")
    mod_b.write_text("def b():\n    return 2\n", encoding="utf-8")

    assert run_project_memory_cli(SimpleNamespace(pm_cmd="init", project_dir=str(root), name="incbuild")) == 0
    capsys.readouterr()
    assert run_project_memory_cli(SimpleNamespace(pm_cmd="scan", name="incbuild")) == 0
    capsys.readouterr()
    assert run_project_memory_cli(SimpleNamespace(pm_cmd="build", name="incbuild")) == 0
    capsys.readouterr()

    analyzed: list[str] = []
    from ms8.absorb.project_memory import generator as generator_mod

    original = generator_mod._analyze_python_file

    def _recording(path: Path, relative_path: str):
        analyzed.append(relative_path)
        return original(path, relative_path)

    monkeypatch.setattr(generator_mod, "_analyze_python_file", _recording)
    time.sleep(1.1)
    mod_b.write_text("def b():\n    return 2000\n", encoding="utf-8")

    assert run_project_memory_cli(SimpleNamespace(pm_cmd="scan", name="incbuild")) == 0
    scan_payload = _last_json_payload(capsys.readouterr().out)
    assert scan_payload["files_scanned"] == 1
    assert "src/b.py" in scan_payload["changed_paths"]
    assert run_project_memory_cli(SimpleNamespace(pm_cmd="build", name="incbuild")) == 0
    build_payload = _last_json_payload(capsys.readouterr().out)
    assert build_payload["status"] == "built"
    assert build_payload["stats"]["rebuilt_file_entries"] == 1
    assert build_payload["stats"]["reused_file_entries"] >= 2
    assert analyzed == ["src/b.py"]
