from __future__ import annotations

from pathlib import Path

from ms8.engine_core.maintenance.self_check import check_specs as cs


def test_to_aware_variants() -> None:
    assert cs._to_aware("") is None
    assert cs._to_aware("bad-ts") is None
    assert cs._to_aware("2026-05-25T10:00:00Z") is not None
    assert cs._to_aware("2026-05-25T10:00:00") is not None


def test_pipeline_log_candidates_collects_primary_logs_and_logs_dir(tmp_path: Path) -> None:
    primary = tmp_path / "auto_memory_pipeline.log"
    rot = tmp_path / "auto_memory_pipeline.1.log"
    logs = tmp_path / "logs"
    logs.mkdir(parents=True, exist_ok=True)
    log2 = logs / "auto_memory_pipeline.2.log"
    primary.write_text("p", encoding="utf-8")
    rot.write_text("r", encoding="utf-8")
    log2.write_text("l", encoding="utf-8")

    out = cs._pipeline_log_candidates(tmp_path)
    assert primary in out
    assert rot in out
    assert log2 in out
    assert len(out) == len(set(out))


def test_current_self_check_hashes_contains_targets() -> None:
    hashes = cs._current_self_check_hashes()
    assert set(hashes.keys()) == {"check_specs.py", "check_runner.py", "reporter.py"}
    assert all(isinstance(v, str) for v in hashes.values())


def test_launchctl_running_handles_oserror(monkeypatch) -> None:
    def _boom(*_a, **_k):
        raise OSError("x")

    monkeypatch.setattr(cs.subprocess, "run", _boom)
    assert cs._launchctl_running("com.example.none") is False
