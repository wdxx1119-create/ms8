from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path

import yaml

from ms8.engine_core.maintenance.self_repair import repair_policies as rp


@dataclass
class _Core:
    config: dict[str, str]
    shadow: object | None = None


def test_ok_ctx_apply_path_and_pre_file_exists(tmp_path: Path) -> None:
    core = _Core(config={"memory_dir": str(tmp_path)})
    out = rp._ok(a=1)
    assert out["status"] == "ok"
    assert out["a"] == 1

    app = rp._ctx_apply({"details": {"apply": {"x": 1}}})
    assert app == {"x": 1}
    assert rp._ctx_apply({}) == {}

    p = rp._path_from_core(core, "x/y")
    assert p == tmp_path / "x/y"

    blocked = rp._pre_file_exists(core, {"target_file": "missing.txt"}, "auto_memory_records.jsonl")
    assert blocked["status"] == "blocked"
    target = tmp_path / "exists.txt"
    target.write_text("ok", encoding="utf-8")
    ok = rp._pre_file_exists(core, {"target_file": "exists.txt"}, "auto_memory_records.jsonl")
    assert ok["status"] == "ok"


def test_dry_jsonl_and_jsonl_repair_and_rollback(tmp_path: Path) -> None:
    core = _Core(config={"memory_dir": str(tmp_path)})
    rec = tmp_path / "auto_memory_records.jsonl"
    rec.write_text('{"a":1}\n{bad}\n\n{"b":2}\n', encoding="utf-8")

    dry = rp._dry_jsonl(core, {"target_file": "auto_memory_records.jsonl"})
    assert dry["status"] == "ok"
    assert dry["bad_lines"] == 1
    assert dry["would_repair"] is True

    rep = rp._jsonl_repair(core, {"target_file": "auto_memory_records.jsonl"})
    assert rep["status"] == "ok"
    assert rep["repaired"] is True
    assert rep["bad_lines"] == 1
    assert '"a": 1' not in rec.read_text(encoding="utf-8")

    no_bad = rp._jsonl_repair(core, {"target_file": "auto_memory_records.jsonl"})
    assert no_bad["repaired"] is False

    restored = rp._rollback_jsonl(core, {"target_file": "auto_memory_records.jsonl", "details": {"apply": rep}})
    assert restored["status"] == "ok"


def test_quarantine_profile_and_reinit_notice_and_truncate_log(tmp_path: Path, monkeypatch) -> None:
    fake_home = tmp_path / "home"
    monkeypatch.setattr(rp, "get_ms8_home", lambda: fake_home)

    profiles = fake_home / "connect" / "profiles"
    profiles.mkdir(parents=True, exist_ok=True)
    (profiles / "good.yaml").write_text(yaml.safe_dump({"name": "ok"}), encoding="utf-8")
    (profiles / "bad.yaml").write_text("[]", encoding="utf-8")
    (profiles / "bad2.yml").write_text(":::bad:::", encoding="utf-8")
    out = rp._quarantine_bad_profile(None, {})
    assert out["status"] == "ok"
    assert out["moved"] >= 1
    assert (profiles / ".quarantine").exists()

    reset = rp._reinit_llm_notice_state(None, {})
    assert reset["status"] == "ok"
    state = fake_home / "health" / "llm_notice_state.json"
    data = json.loads(state.read_text(encoding="utf-8"))
    assert "updated_at" in data

    log = fake_home / "connect" / "runtime" / "auto_repair_log.jsonl"
    log.parent.mkdir(parents=True, exist_ok=True)
    log.write_text("x\n", encoding="utf-8")
    trunc = rp._truncate_or_reinit_log(None, {})
    assert trunc["status"] == "ok"
    assert log.read_text(encoding="utf-8") == ""


def test_backup_candidate_and_restore_core_files(tmp_path: Path) -> None:
    memory_dir = tmp_path / "memory"
    workspace = tmp_path / "workspace"
    memory_dir.mkdir(parents=True, exist_ok=True)
    workspace.mkdir(parents=True, exist_ok=True)
    core = _Core(config={"memory_dir": str(memory_dir), "workspace_dir": str(workspace)})

    backups = memory_dir / "backups" / "20260522"
    backups.mkdir(parents=True, exist_ok=True)
    b = backups / "MEMORY.md.bak"
    b.write_text("backup-content", encoding="utf-8")

    cand = rp._find_backup_candidate(memory_dir, "MEMORY.md")
    assert cand is not None
    assert cand.name.endswith(".bak")

    dry = rp._dry_restore_core_files(core, {"params": {"missing_files": [str(workspace / "MEMORY.md")]}})
    assert dry["status"] == "ok"
    assert dry["would_restore"] is True

    res = rp._restore_core_files(core, {"params": {"missing_files": [str(workspace / "MEMORY.md")]}})
    assert res["status"] == "ok"
    assert (workspace / "MEMORY.md").exists()

    err = rp._restore_core_files(core, {"params": {"missing_files": [str(workspace / "missing.db")]}})
    assert err["status"] == "error"
    assert "unresolved" in err


def test_launchd_and_dry_launchd(monkeypatch) -> None:
    class _CP:
        returncode = 0
        stdout = "ok"
        stderr = ""

    monkeypatch.setattr(rp.subprocess, "run", lambda *a, **k: _CP())
    ok = rp._restart_launchd("com.openclaw.memory.mcp")(None, {})
    assert ok["status"] == "ok"
    assert ok["returncode"] == 0

    blocked = rp._restart_launchd("bad.label")(None, {})
    assert blocked["status"] == "error"

    dry = rp._dry_launchd("com.openclaw.memory.mcp")(None, {})
    assert dry["status"] == "ok"
    assert "currently_running" in dry


def test_baseline_request_dry_and_apply(tmp_path: Path) -> None:
    memory_dir = tmp_path / "memory"
    reports = memory_dir / "reports"
    reports.mkdir(parents=True, exist_ok=True)
    core = _Core(config={"memory_dir": str(memory_dir)})

    missing = rp._dry_resolve_baseline_update_request(core, {})
    assert missing["request_exists"] is False

    req = reports / "baseline_update_request.json"
    req.write_text(json.dumps({"status": "pending"}), encoding="utf-8")
    # Baseline empty vs current hashes -> mismatch, auto resolve false.
    d = rp._dry_resolve_baseline_update_request(core, {})
    assert d["request_exists"] is True
    assert d["can_auto_resolve"] is False

    blocked = rp._resolve_baseline_update_request(core, {})
    assert blocked["status"] == "blocked"
    assert blocked["reason"] == "manual_authorization_required"

    # Align baseline hashes with current to allow resolve.
    cur = rp._current_self_check_hashes()
    (reports / "self_check_integrity_baseline.json").write_text(json.dumps({"hashes": cur}), encoding="utf-8")
    ok = rp._resolve_baseline_update_request(core, {})
    assert ok["status"] == "ok"
    assert ok["resolved"] is True
    assert not req.exists()


def test_get_policy_and_get_hooks() -> None:
    assert rp.get_policy("l1_core_files") is not None
    assert rp.get_policy("unknown") is None
    assert rp.get_hooks("rebuild_index") is not None
    assert rp.get_hooks("unknown") is None


def test_cleanup_backups_and_fix_shadow_permissions(tmp_path: Path) -> None:
    # cleanup old repair backups
    target = tmp_path / "auto_memory_records.jsonl"
    target.write_text("ok\n", encoding="utf-8")
    for i in range(7):
        p = tmp_path / f"auto_memory_records.jsonl.{i}.repair.bak"
        p.write_text(str(i), encoding="utf-8")
        os.utime(p, (1_700_000_000 + i, 1_700_000_000 + i))
    rp._cleanup_repair_backups(target, keep=3)
    remain = list(tmp_path.glob("auto_memory_records.jsonl*.repair.bak*"))
    assert len(remain) <= 3

    class _Perm:
        def ensure_shadow_permissions(self):
            return [{"changed": True}, {"changed": False}]

    class _Shadow:
        permissions = _Perm()

    core_ok = _Core(config={"memory_dir": str(tmp_path)}, shadow=_Shadow())
    out_ok = rp._fix_shadow_permissions(core_ok, {})
    assert out_ok["status"] == "ok"
    assert out_ok["changed"] == 1
    assert out_ok["entries"] == 2

    class _BadPerm:
        def ensure_shadow_permissions(self):
            raise OSError("perm fail")

    class _BadShadow:
        permissions = _BadPerm()

    core_bad = _Core(config={"memory_dir": str(tmp_path)}, shadow=_BadShadow())
    out_bad = rp._fix_shadow_permissions(core_bad, {})
    assert out_bad["status"] == "error"
    assert "perm fail" in out_bad["error"]


def test_rebuild_index_and_rollback_and_probe_error(monkeypatch, tmp_path: Path) -> None:
    core = _Core(config={"memory_dir": str(tmp_path)})
    rec = tmp_path / "auto_memory_records.jsonl"
    rec.write_text(
        '\n'.join(
            [
                '{"id":"a1","status":"accepted","normalized_text":"Hello"}',
                '{"status":"rejected","normalized_text":"ignored"}',
                '{"status":"pending_review","text":"Needs review"}',
                '{"status":"accepted","text":"NoId"}',
                "{bad}",
                "",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    idx = tmp_path / "auto_memory_index.json"
    idx.write_text('{"items":[]}', encoding="utf-8")
    rebuilt = rp._rebuild_index(core, {})
    assert rebuilt["status"] == "ok"
    payload = json.loads(idx.read_text(encoding="utf-8"))
    assert len(payload["items"]) == 3

    rolled = rp._rollback_rebuild_index(core, {})
    assert rolled["status"] == "ok"
    assert "restored" in rolled

    class _ProbeCore:
        config = {"memory_dir": str(tmp_path)}

        def retrieve_memories(self, query: str, top_k: int):
            raise OSError("probe failed")

    probe = rp._probe_write_then_search(_ProbeCore(), {})
    assert probe["status"] == "ok"
    assert probe["status_detail"] == "probe_error"
    assert probe["result_count"] == 0


def test_ocma_script_and_client_configs_chain(monkeypatch, tmp_path: Path) -> None:
    home = tmp_path / "home"
    scripts = home / "connect" / "scripts"
    scripts.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(rp, "get_ms8_home", lambda: home)

    missing = rp._run_ocma_script("missing.py")
    assert missing["status"] == "error"
    assert missing["error"] == "script_missing"

    # Create placeholder script file, then fail subprocess call to hit error branch.
    (scripts / "generate_client_configs.py").write_text("#!/usr/bin/env python\n", encoding="utf-8")
    monkeypatch.setattr(rp.subprocess, "run", lambda *a, **k: (_ for _ in ()).throw(OSError("boom")))
    err = rp._run_ocma_script("generate_client_configs.py")
    assert err["status"] == "error"
    assert "boom" in err["error"]

    class _CP:
        def __init__(self, code: int):
            self.returncode = code
            self.stdout = "ok"
            self.stderr = ""

    for name in ("generate_client_configs.py", "apply_client_configs.py", "verify_client_configs.py"):
        (scripts / name).write_text("#!/usr/bin/env python\n", encoding="utf-8")

    seq = iter([_CP(0), _CP(1), _CP(0)])
    monkeypatch.setattr(rp.subprocess, "run", lambda *a, **k: next(seq))
    chain = rp._repair_client_configs(None, {})
    assert chain["status"] == "error"
    assert chain["apply"]["returncode"] == 1


def test_seal_history_recover_and_cleanup_disk(tmp_path: Path) -> None:
    class _SealCore:
        config = {"memory_dir": str(tmp_path)}

        def __init__(self):
            self.maintenance = type(
                "M",
                (),
                {
                    "cleanup_old_low_importance_logs": staticmethod(lambda: {"status": "ok"}),
                    "settings": {"cleanup_days": 30},
                },
            )()

        def shadow_status(self):
            return {"sealed": True}

        def shadow_reset_checkpoint(self):
            return {"status": "ok", "step": "reset"}

        def shadow_replay_spool(self):
            return {"status": "ok", "step": "replay"}

        def shadow_recover_from_events(self):
            return {"status": "error", "step": "recover"}

        def shadow_verify(self):
            return {"status": "ok", "step": "verify"}

    core = _SealCore()
    chain = rp._seal_history_recover(core, {})
    assert chain["status"] == "error"
    assert "reset_checkpoint" in chain
    assert "recover_events" in chain

    archive = tmp_path / "archive" / "low_priority"
    archive.mkdir(parents=True, exist_ok=True)
    old = archive / "2026-01-01-sample.md"
    old.write_text("x", encoding="utf-8")
    res = rp._cleanup_disk(core, {})
    assert res["status"] == "ok"
    assert "archive_pruned" in res
