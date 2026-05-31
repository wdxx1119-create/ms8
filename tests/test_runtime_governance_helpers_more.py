from __future__ import annotations

import json
from pathlib import Path

from ms8 import runtime


def test_persist_governance_report_history_write_failure(monkeypatch, tmp_path: Path) -> None:
    health_dir = tmp_path / "health"
    health_dir.mkdir(parents=True, exist_ok=True)
    report = {"at": "2026-05-19T00:00:00Z", "ok": True}

    original_open = Path.open

    def _patched_open(self: Path, *args, **kwargs):
        if self.name == "governance_report_history.jsonl":
            raise OSError("open failed")
        return original_open(self, *args, **kwargs)

    monkeypatch.setattr(Path, "open", _patched_open, raising=False)
    # Should not raise even when history append fails.
    runtime._persist_governance_report(report=report, health_dir=health_dir)
    latest = health_dir / "governance_report_latest.json"
    assert latest.exists()


def test_governance_trend_handles_invalid_lines(monkeypatch, tmp_path: Path) -> None:
    health_dir = tmp_path / "health"
    health_dir.mkdir(parents=True, exist_ok=True)
    history = health_dir / "governance_report_history.jsonl"
    history.write_text(
        "\n".join(
            [
                "{bad-json}",
                json.dumps(["not-dict"], ensure_ascii=False),
                json.dumps({"at": "bad-time", "fallback_write_count": 1}, ensure_ascii=False),
                json.dumps({"at": "2026-05-19T00:00:00Z", "fallback_write_count": 1}, ensure_ascii=False),
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(runtime, "_load_governance_risk_config", lambda: {"red": {}, "yellow": {}})
    out = runtime._governance_trend(health_dir=health_dir)
    assert "window_24h" in out
    assert "window_7d" in out


def test_redact_support_text_masks_sensitive_tokens() -> None:
    raw = (
        "email=test@example.com\n"
        "phone=13800138000\n"
        "api_key=\"sk-abcdefghijklmnopqrstuvwxyz123\"\n"
        "password=\"secret\"\n"
        "bearer abcdefghijklmnopqrstuvwxyz\n"
        "/Users/alice/Documents/private\n"
    )
    out = runtime._redact_support_text(raw)
    assert "[REDACTED_EMAIL]" in out
    assert "[REDACTED_PHONE]" in out
    assert "[REDACTED_TOKEN]" in out
    assert "[REDACTED_PASSWORD]" in out
    assert "/Users/<redacted>" in out


def test_support_bundle_candidates_only_existing_files(tmp_path: Path) -> None:
    root = tmp_path / "ms8"
    (root / "health").mkdir(parents=True, exist_ok=True)
    wanted = root / "health" / "governance_report_latest.json"
    wanted.write_text("{}", encoding="utf-8")
    got = runtime._support_bundle_candidates(root)
    assert wanted in got
    assert all(p.exists() for p in got)


def test_get_engine_status_fallbacks(monkeypatch) -> None:
    class _EngineNoMethods:
        pass

    monkeypatch.setattr(runtime, "_engine", lambda: _EngineNoMethods())
    assert runtime.get_engine_monitoring_status()["enabled"] is False
    assert runtime.get_engine_shadow_status()["status"] == "unsupported"
    kg = runtime.get_engine_knowledge_graph_stats()
    assert kg["entity_total"] == 0
    llm = runtime.get_engine_llm_status()
    assert llm["available"] is False


def test_export_support_bundle_skips_large_and_read_errors(monkeypatch, tmp_path: Path) -> None:
    root = tmp_path / "ms8_home"
    health = root / "health"
    health.mkdir(parents=True, exist_ok=True)
    small = health / "governance_report_latest.json"
    large = health / "governance_report_history.jsonl"
    bad = health / "review_governance_latest.json"
    small.write_text('{"ok":true}', encoding="utf-8")
    large.write_text("x" * (runtime._SUPPORT_BUNDLE_TEXT_MAX_BYTES + 10), encoding="utf-8")
    bad.write_text("bad", encoding="utf-8")

    monkeypatch.setattr(
        runtime,
        "ensure_runtime_dirs",
        lambda: {"root": root},
    )

    original_read_text = Path.read_text

    def _patched_read_text(self: Path, *args, **kwargs):
        if self == bad:
            raise OSError("read failed")
        return original_read_text(self, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", _patched_read_text, raising=False)
    out = runtime.export_support_bundle_runtime(output=str(health / "bundle.zip"), redact=True, dry_run=False)
    assert out["ok"] is True
    assert any(x["file"].endswith("governance_report_history.jsonl") and x["reason"] == "too_large" for x in out["files_skipped"])
    assert any(x["file"].endswith("review_governance_latest.json") for x in out["files_skipped"])


def test_capability_reachability_missing_and_parse_fail(monkeypatch, tmp_path: Path) -> None:
    # missing core.py branch
    monkeypatch.setattr(runtime.Path, "resolve", lambda self: tmp_path / "fake_runtime.py", raising=False)
    out_missing = runtime.get_capability_reachability_report(top_unreachable=5)
    assert out_missing["status"] == "error"
    assert out_missing["reason"] == "core_file_missing"

    # parse fail branch
    base = tmp_path / "pkg"
    (base / "engine_core").mkdir(parents=True, exist_ok=True)
    core = base / "engine_core" / "core.py"
    core.write_text("class MemoryCore(:\n", encoding="utf-8")  # invalid syntax

    monkeypatch.setattr(runtime.Path, "resolve", lambda self: base / "runtime.py", raising=False)
    out_parse = runtime.get_capability_reachability_report(top_unreachable=5)
    assert out_parse["status"] == "error"
    assert str(out_parse["reason"]).startswith("ast_parse_failed:")
