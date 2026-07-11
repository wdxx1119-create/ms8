from __future__ import annotations

import json
from pathlib import Path

import ms8.recovery as recovery


def test_interrupted_restore_is_reported_cleaned_and_retryable(tmp_path, monkeypatch) -> None:
    source = tmp_path / "source"
    source.mkdir()
    (source / "a.txt").write_text("alpha", encoding="utf-8")
    (source / "b.txt").write_text("beta", encoding="utf-8")
    archive = tmp_path / "runtime.zip"
    created = recovery.create_runtime_backup(root=source, output=archive, tag="chaos")
    assert created["ok"] is True

    target = tmp_path / "target"
    real_replace = recovery.os.replace

    def fail_on_b(src: str | Path, dst: str | Path) -> None:
        if Path(dst).name == "b.txt":
            raise OSError("simulated interruption")
        real_replace(src, dst)

    monkeypatch.setattr(recovery.os, "replace", fail_on_b)
    failed = recovery.restore_runtime_backup(archive, target_root=target, apply=True)
    assert failed["ok"] is False
    assert failed["applied"] is False
    assert failed["error"] == "restore_apply_failed:OSError"
    assert not list(target.rglob("*.restore-tmp"))
    audit = target / "memory" / "logs" / "restore_audit.jsonl"
    event = json.loads(audit.read_text(encoding="utf-8").splitlines()[-1])
    assert event["event"] == "runtime_restore_failed"

    monkeypatch.setattr(recovery.os, "replace", real_replace)
    retried = recovery.restore_runtime_backup(archive, target_root=target, apply=True)
    assert retried["ok"] is True
    assert retried["applied"] is True
    assert (target / "a.txt").read_text(encoding="utf-8") == "alpha"
    assert (target / "b.txt").read_text(encoding="utf-8") == "beta"
