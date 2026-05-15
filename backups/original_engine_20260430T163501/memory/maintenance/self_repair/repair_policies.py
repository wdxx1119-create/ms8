from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Dict

from ...file_write_guard import atomic_write_json
from .repair_schema import RepairPolicy


@dataclass
class PolicyHooks:
    pre_check: Callable[[Any, Dict[str, Any]], Dict[str, Any]]
    dry_run: Callable[[Any, Dict[str, Any]], Dict[str, Any]]
    apply: Callable[[Any, Dict[str, Any]], Dict[str, Any]]
    rollback: Callable[[Any, Dict[str, Any]], Dict[str, Any]]


def _ok(**kwargs: Any) -> Dict[str, Any]:
    return {"status": "ok", **kwargs}


def _path_from_core(core: Any, rel: str) -> Path:
    return Path(core.config["memory_dir"]) / rel


def _noop(_core: Any, _ctx: Dict[str, Any]) -> Dict[str, Any]:
    return _ok(reason="noop")


def _dry_ok(_core: Any, _ctx: Dict[str, Any]) -> Dict[str, Any]:
    return _ok(estimated=True)


def _rollback_manual(action: str) -> Callable[[Any, Dict[str, Any]], Dict[str, Any]]:
    def _f(_core: Any, _ctx: Dict[str, Any]) -> Dict[str, Any]:
        return {"status": "skipped", "reason": "manual_required", "action": action}

    return _f


def _restart_launchd(label: str) -> Callable[[Any, Dict[str, Any]], Dict[str, Any]]:
    allowed = {"com.openclaw.memory.mcp", "com.openclaw.memory.maintenance"}

    def _f(_core: Any, _ctx: Dict[str, Any]) -> Dict[str, Any]:
        if str(label or "") not in allowed:
            return {"status": "error", "error": "label_not_allowed", "label": str(label)}
        target = f"gui/{os.getuid()}/{label}"
        try:
            cp = subprocess.run(
                ["launchctl", "kickstart", "-k", target],
                capture_output=True,
                text=True,
                timeout=10,
                check=False,
            )
            return _ok(
                label=label,
                target=target,
                returncode=int(cp.returncode),
                stdout=str(cp.stdout or "")[-2000:],
                stderr=str(cp.stderr or "")[-2000:],
            )
        except Exception as exc:
            return {"status": "error", "error": str(exc), "label": label}

    return _f


def _pre_shadow(core: Any, _ctx: Dict[str, Any]) -> Dict[str, Any]:
    if not getattr(core, "shadow", None):
        return {"status": "blocked", "reason": "shadow_unavailable"}
    return _ok(shadow_available=True)


def _pre_file_exists(core: Any, ctx: Dict[str, Any], rel: str) -> Dict[str, Any]:
    params = ctx.get("params", {}) if isinstance(ctx.get("params", {}), dict) else {}
    target = str(params.get("target_file", ctx.get("target_file", rel)))
    path = _path_from_core(core, target)
    if not path.exists():
        return {"status": "blocked", "reason": "file_missing", "path": str(path)}
    return _ok(path=str(path))


def _dry_launchd(label: str) -> Callable[[Any, Dict[str, Any]], Dict[str, Any]]:
    def _f(_core: Any, _ctx: Dict[str, Any]) -> Dict[str, Any]:
        running = False
        try:
            out = subprocess.run(
                ["launchctl", "list", label],
                check=False,
                capture_output=True,
                text=True,
                timeout=3,
            )
            running = out.returncode == 0
        except Exception:
            running = False
        return _ok(
            action="kickstart",
            label=label,
            currently_running=running,
            impact="service_restart",
        )

    return _f


def _dry_jsonl(core: Any, ctx: Dict[str, Any]) -> Dict[str, Any]:
    params = ctx.get("params", {}) if isinstance(ctx.get("params", {}), dict) else {}
    rel = str(params.get("target_file", ctx.get("target_file", "auto_memory_records.jsonl")))
    path = _path_from_core(core, rel)
    if not path.exists():
        return {"status": "blocked", "reason": "missing", "path": str(path)}
    raw = path.read_text(encoding="utf-8", errors="ignore").splitlines()
    bad = 0
    for ln in raw:
        t = ln.strip()
        if not t:
            continue
        try:
            json.loads(t)
        except Exception:
            bad += 1
    return _ok(path=str(path), total_lines=len(raw), bad_lines=bad, would_repair=bad > 0)


def _dry_rebuild_index(core: Any, _ctx: Dict[str, Any]) -> Dict[str, Any]:
    idx = _path_from_core(core, "auto_memory_index.json")
    rec = _path_from_core(core, "auto_memory_records.jsonl")
    rec_lines = 0
    if rec.exists():
        rec_lines = len(rec.read_text(encoding="utf-8", errors="ignore").splitlines())
    idx_size = idx.stat().st_size if idx.exists() else 0
    return _ok(records_lines=rec_lines, index_size_before=idx_size, target=str(idx), would_rebuild=True)


def _dry_cleanup_disk(core: Any, _ctx: Dict[str, Any]) -> Dict[str, Any]:
    mem = Path(core.config["memory_dir"])
    backup_dir = mem / "backups"
    total = 0
    count = 0
    if backup_dir.exists():
        for p in backup_dir.rglob("*"):
            if p.is_file():
                count += 1
                try:
                    total += int(p.stat().st_size)
                except Exception:
                    pass
    return _ok(backup_files=count, backup_bytes=total, estimated_free_mb=round(total / (1024 * 1024), 2))


def _dry_self_check_l1(_core: Any, _ctx: Dict[str, Any]) -> Dict[str, Any]:
    return _ok(action="run_self_check", level="L1")


def _dry_shadow_replay(core: Any, _ctx: Dict[str, Any]) -> Dict[str, Any]:
    try:
        st = core.shadow_status()
    except Exception:
        st = {}
    backlog = 0
    if isinstance(st, dict):
        backlog = int((st.get("manifest", {}) or {}).get("spool_pending_count", 0) or 0)
    return _ok(spool_pending_count=backlog, would_replay=backlog > 0)


def _dry_shadow_reset(_core: Any, _ctx: Dict[str, Any]) -> Dict[str, Any]:
    return _ok(action="reset_checkpoint", impact="rebuild_shadow_checkpoints")


def _dry_client_configs(_core: Any, _ctx: Dict[str, Any]) -> Dict[str, Any]:
    root = Path.home() / "openclaw-memory-auto" / "runtime" / "connect_report.json"
    exists = root.exists()
    return _ok(connect_report_exists=exists, action_chain=["generate", "apply", "verify"])


def _ctx_apply(ctx: Dict[str, Any]) -> Dict[str, Any]:
    det = ctx.get("details", {}) if isinstance(ctx.get("details", {}), dict) else {}
    app = det.get("apply", {}) if isinstance(det.get("apply", {}), dict) else {}
    return app


def _rollback_jsonl(core: Any, ctx: Dict[str, Any]) -> Dict[str, Any]:
    app = _ctx_apply(ctx)
    rel = str(ctx.get("target_file", "auto_memory_records.jsonl"))
    path = _path_from_core(core, rel)
    bak = str(app.get("backup", "") or "")
    if not bak:
        bak = str(path.with_suffix(path.suffix + ".repair.bak"))
    b = Path(bak)
    if not b.exists():
        return {"status": "skipped", "reason": "backup_missing", "backup": str(b)}
    path.write_text(b.read_text(encoding="utf-8", errors="ignore"), encoding="utf-8")
    return _ok(restored=True, backup=str(b), target=str(path))


def _rollback_rebuild_index(core: Any, _ctx: Dict[str, Any]) -> Dict[str, Any]:
    idx = _path_from_core(core, "auto_memory_index.json")
    bak = idx.with_suffix(idx.suffix + ".repair.bak")
    if not bak.exists():
        return {"status": "skipped", "reason": "backup_missing", "backup": str(bak)}
    idx.write_text(bak.read_text(encoding="utf-8", errors="ignore"), encoding="utf-8")
    return _ok(restored=True, backup=str(bak), target=str(idx))


def _rollback_client_configs(_core: Any, _ctx: Dict[str, Any]) -> Dict[str, Any]:
    return _run_ocma_script("rollback_client_configs.py")


def _cleanup_repair_backups(path: Path, keep: int = 5) -> None:
    parent = path.parent
    name = path.name
    pats = sorted(parent.glob(f"{name}*.repair.bak*"), key=lambda p: p.stat().st_mtime, reverse=True)
    for old in pats[max(1, int(keep)):]:
        try:
            old.unlink(missing_ok=True)
        except Exception:
            pass


def _fix_shadow_permissions(core: Any, _ctx: Dict[str, Any]) -> Dict[str, Any]:
    if not getattr(core, "shadow", None):
        return {"status": "blocked", "reason": "shadow_unavailable"}
    try:
        rows = core.shadow.permissions.ensure_shadow_permissions()
        changed = sum(1 for r in rows if bool(r.get("changed", False)))
        return _ok(changed=changed, entries=len(rows))
    except Exception as exc:
        return {"status": "error", "error": str(exc)}


def _jsonl_repair(core: Any, ctx: Dict[str, Any]) -> Dict[str, Any]:
    params = ctx.get("params", {}) if isinstance(ctx.get("params", {}), dict) else {}
    rel = str(params.get("target_file", ctx.get("target_file", "auto_memory_records.jsonl")))
    path = _path_from_core(core, rel)
    if not path.exists():
        return {"status": "skipped", "reason": "missing", "path": str(path)}
    backup = path.with_suffix(path.suffix + ".repair.bak")
    raw = path.read_text(encoding="utf-8", errors="ignore").splitlines()
    good = []
    bad = 0
    for ln in raw:
        t = ln.strip()
        if not t:
            continue
        try:
            json.loads(t)
            good.append(t)
        except Exception:
            bad += 1
    if bad <= 0:
        return _ok(path=str(path), repaired=False, bad_lines=0)
    backup.write_text(path.read_text(encoding="utf-8", errors="ignore"), encoding="utf-8")
    path.write_text(("\n".join(good) + ("\n" if good else "")), encoding="utf-8")
    _cleanup_repair_backups(path)
    return _ok(path=str(path), repaired=True, bad_lines=bad, backup=str(backup))


def _rebuild_index(core: Any, _ctx: Dict[str, Any]) -> Dict[str, Any]:
    idx = _path_from_core(core, "auto_memory_index.json")
    rec = _path_from_core(core, "auto_memory_records.jsonl")
    if not rec.exists():
        return {"status": "skipped", "reason": "records_missing"}
    items = []
    for ln in rec.read_text(encoding="utf-8", errors="ignore").splitlines():
        raw = ln.strip()
        if not raw:
            continue
        try:
            row = json.loads(raw)
        except Exception:
            continue
        if str(row.get("status", "accepted")) not in {"accepted", "pending_review"}:
            continue
        txt = str(row.get("normalized_text", row.get("text", "")) or "").strip()
        if not txt:
            continue
        rid = str(row.get("id", row.get("meta", {}).get("id", "")) or "")
        if not rid:
            rid = hashlib.sha1(txt.encode("utf-8", errors="ignore")).hexdigest()[:16]
        items.append(
            {
                "id": rid,
                "status": str(row.get("status", "accepted")),
                "excluded": False,
                "normalized_text": txt.lower(),
                "source": str(row.get("source", "")),
            }
        )
    payload = {"items": items}
    bak = idx.with_suffix(idx.suffix + ".repair.bak")
    if idx.exists():
        bak.write_text(idx.read_text(encoding="utf-8", errors="ignore"), encoding="utf-8")
    atomic_write_json(idx, payload, ensure_ascii=False, indent=2)
    _cleanup_repair_backups(idx)
    return _ok(index_file=str(idx), items=len(items), backup=str(bak) if bak.exists() else "")


def _shadow_self_heal(core: Any, _ctx: Dict[str, Any]) -> Dict[str, Any]:
    if not getattr(core, "shadow", None):
        return {"status": "blocked", "reason": "shadow_unavailable"}
    return core.shadow_startup_self_heal()


def _shadow_reset_checkpoint(core: Any, _ctx: Dict[str, Any]) -> Dict[str, Any]:
    return core.shadow_reset_checkpoint()


def _shadow_replay(core: Any, _ctx: Dict[str, Any]) -> Dict[str, Any]:
    return core.shadow_replay_spool()


def _cleanup_disk(core: Any, _ctx: Dict[str, Any]) -> Dict[str, Any]:
    base = core.maintenance.cleanup_old_low_importance_logs()
    memory_dir = Path(core.config.get("memory_dir", ""))
    archive_root = memory_dir / "archive" / "low_priority"
    pruned = []
    try:
        days = int(core.maintenance.settings.get("cleanup_days", 90)) if hasattr(core.maintenance, "settings") else 90
    except Exception:
        days = 90
    cutoff_days = max(30, int(days))
    # Aggressive but safe: prune very old low-priority archive files by date prefix.
    if archive_root.exists():
        for fp in archive_root.rglob("*.md"):
            if not fp.is_file():
                continue
            stem = fp.stem
            parts = stem.split("-")
            file_date = None
            if len(parts) >= 3:
                try:
                    file_date = __import__('datetime').date.fromisoformat("-".join(parts[:3]))
                except Exception:
                    file_date = None
            if file_date is None:
                continue
            age_days = (__import__('datetime').datetime.now(__import__('datetime').timezone.utc).date() - file_date).days
            if age_days < cutoff_days:
                continue
            try:
                sz = int(fp.stat().st_size)
                fp.unlink(missing_ok=True)
                pruned.append({"file": str(fp), "size": sz, "age_days": age_days})
            except Exception:
                continue
    out = dict(base) if isinstance(base, dict) else {"status": "ok"}
    out["archive_pruned"] = len(pruned)
    out["archive_pruned_bytes"] = int(sum(int(x.get("size", 0)) for x in pruned))
    if pruned:
        out["archive_pruned_preview"] = pruned[:20]
    return out


def _refresh_health_card(core: Any, _ctx: Dict[str, Any]) -> Dict[str, Any]:
    from ..self_check.reporter import build_health_card, persist_health_card

    card = build_health_card(core, snapshot_reason="post_repair")
    sealed = False
    try:
        st = core.shadow_status() if hasattr(core, "shadow_status") else {}
        sealed = bool(st.get("sealed", False)) if isinstance(st, dict) else False
    except Exception:
        sealed = False
    out = persist_health_card(Path(core.config["memory_dir"]), card, sealed=sealed, force=False)
    return {"status": "ok", "health_card": out}


def _reload_short_term(core: Any, _ctx: Dict[str, Any]) -> Dict[str, Any]:
    core._restore_short_term_memory()
    return {"status": "ok"}


def _repair_semantic_cache(core: Any, _ctx: Dict[str, Any]) -> Dict[str, Any]:
    return core.repair_semantic_cache(limit=80)


def _run_self_check_l1(core: Any, _ctx: Dict[str, Any]) -> Dict[str, Any]:
    return core.run_self_check(level="L1")


def _dry_write_then_search_probe(core: Any, _ctx: Dict[str, Any]) -> Dict[str, Any]:
    idx = _path_from_core(core, "auto_memory_index.json")
    rec = _path_from_core(core, "auto_memory_records.jsonl")
    return _ok(
        index_exists=idx.exists(),
        records_exists=rec.exists(),
        action="probe_write_then_search",
        risk="read_only_probe",
    )


def _probe_write_then_search(core: Any, _ctx: Dict[str, Any]) -> Dict[str, Any]:
    """
    Lightweight post-rebuild retrieval probe.
    It is read-only and never mutates memory data.
    """
    query = "系统 记忆"
    try:
        rows = core.retrieve_memories(query=query, top_k=3)
        count = len(rows) if isinstance(rows, list) else 0
        return _ok(query=query, result_count=count, status_detail="probe_completed")
    except Exception as exc:
        # Keep action non-fatal; this is a best-effort probe evidence step.
        return {"status": "ok", "query": query, "result_count": 0, "status_detail": "probe_error", "error": str(exc)}


def _run_ocma_script(script_name: str, env_extra: Dict[str, str] | None = None) -> Dict[str, Any]:
    root = Path.home() / "openclaw-memory-auto"
    script = root / "scripts" / script_name
    if not script.exists():
        return {"status": "error", "error": "script_missing", "script": str(script)}
    env = os.environ.copy()
    if env_extra:
        env.update({str(k): str(v) for k, v in env_extra.items()})
    try:
        cp = subprocess.run(
            [sys.executable, str(script)],
            capture_output=True,
            text=True,
            timeout=30,
            env=env,
            check=False,
        )
        return {
            "status": "ok" if cp.returncode == 0 else "error",
            "script": str(script_name),
            "returncode": int(cp.returncode),
            "stdout": str(cp.stdout or "")[-2000:],
            "stderr": str(cp.stderr or "")[-2000:],
        }
    except Exception as exc:
        return {"status": "error", "script": str(script_name), "error": str(exc)}


def _repair_client_configs(_core: Any, _ctx: Dict[str, Any]) -> Dict[str, Any]:
    gen = _run_ocma_script("generate_client_configs.py")
    apply_res = _run_ocma_script("apply_client_configs.py")
    verify = _run_ocma_script("verify_client_configs.py")
    ok = (
        str(gen.get("status", "")) == "ok"
        and str(apply_res.get("status", "")) == "ok"
        and str(verify.get("status", "")) == "ok"
    )
    return {
        "status": "ok" if ok else "error",
        "generate": gen,
        "apply": apply_res,
        "verify": verify,
    }


def _regen_connect_report(_core: Any, _ctx: Dict[str, Any]) -> Dict[str, Any]:
    return _run_ocma_script("status.py")


def _find_backup_candidate(memory_dir: Path, name: str) -> Path | None:
    backups = memory_dir / "backups"
    if not backups.exists():
        return None
    cands = []
    base = str(name)
    for p in backups.rglob("*"):
        if not p.is_file():
            continue
        n = p.name
        if n == base or n.startswith(base + ".") or n.endswith(base + ".bak"):
            cands.append(p)
    if not cands:
        return None
    cands.sort(key=lambda x: x.stat().st_mtime, reverse=True)
    return cands[0]


def _dry_restore_core_files(core: Any, ctx: Dict[str, Any]) -> Dict[str, Any]:
    params = ctx.get("params", {}) if isinstance(ctx.get("params", {}), dict) else {}
    missing = [str(x) for x in (params.get("missing_files", []) or [])]
    ws = Path(core.config.get("workspace_dir", ""))
    mem = Path(core.config.get("memory_dir", ""))
    if not missing:
        defaults = [ws / "MEMORY.md", mem / "memory.db", mem / "knowledge_graph.db", ws / "config.yaml"]
        missing = [str(x) for x in defaults if not x.exists()]
    scan = []
    for raw in missing:
        t = Path(raw)
        cand = _find_backup_candidate(mem, t.name)
        scan.append({"target": str(t), "backup": str(cand) if cand else ""})
    return _ok(missing_count=len(missing), candidates=scan, would_restore=any(x.get("backup") for x in scan))


def _restore_core_files(core: Any, ctx: Dict[str, Any]) -> Dict[str, Any]:
    params = ctx.get("params", {}) if isinstance(ctx.get("params", {}), dict) else {}
    missing = [str(x) for x in (params.get("missing_files", []) or [])]
    ws = Path(core.config.get("workspace_dir", ""))
    mem = Path(core.config.get("memory_dir", ""))
    if not missing:
        defaults = [ws / "MEMORY.md", mem / "memory.db", mem / "knowledge_graph.db", ws / "config.yaml"]
        missing = [str(x) for x in defaults if not x.exists()]
    restored = []
    unresolved = []
    for raw in missing:
        target = Path(raw)
        if target.exists():
            continue
        backup = _find_backup_candidate(mem, target.name)
        if backup is None:
            unresolved.append(str(target))
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(backup, target)
        restored.append({"target": str(target), "backup": str(backup)})
    if unresolved:
        return {"status": "error", "restored": restored, "unresolved": unresolved}
    return _ok(restored=restored, restored_count=len(restored))


def _seal_history_recover(core: Any, _ctx: Dict[str, Any]) -> Dict[str, Any]:
    chain: Dict[str, Any] = {"status": "ok"}
    st = {}
    try:
        st = core.shadow_status()
    except Exception:
        st = {}
    if not isinstance(st, dict):
        st = {}
    if bool(st.get("sealed", False)):
        chain["reset_checkpoint"] = core.shadow_reset_checkpoint()
    chain["replay_spool"] = core.shadow_replay_spool()
    chain["recover_events"] = core.shadow_recover_from_events()
    # Best-effort verify
    chain["verify"] = core.shadow_verify()
    statuses = [str(v.get("status", "")) for v in chain.values() if isinstance(v, dict)]
    if any(s in {"error", "failed"} for s in statuses):
        chain["status"] = "error"
    return chain


POLICIES: Dict[str, RepairPolicy] = {
    "l1_launchd_mcp": RepairPolicy("l1_launchd_mcp", "restart_launchd_mcp", "connect", "R1", target="launchd:mcp"),
    "l1_launchd_maintenance": RepairPolicy(
        "l1_launchd_maintenance", "restart_launchd_maintenance", "connect", "R1", target="launchd:maintenance"
    ),
    "l1_disk_space": RepairPolicy("l1_disk_space", "cleanup_disk", "memory", "R1", target="memory:logs_backups"),
    "l1_core_files": RepairPolicy("l1_core_files", "restore_core_files", "memory", "R2", target="memory:core_files"),
    "l1_health_card_diff": RepairPolicy("l1_health_card_diff", "refresh_health_card", "memory", "R1", target="memory:health_card"),
    "l1_self_check_framework": RepairPolicy("l1_self_check_framework", "run_self_check_l1", "memory", "R1", target="memory:self_check"),
    "l2_pipeline_stages": RepairPolicy("l2_pipeline_stages", "run_self_check_l1", "memory", "R1", target="memory:pipeline_probe"),
    "l1_shadow_files": RepairPolicy("l1_shadow_files", "shadow_self_heal", "security", "R1", target="shadow:data"),
    "l1_shadow_sealed": RepairPolicy("l1_shadow_sealed", "seal_history_recover", "security", "R2", target="shadow:seal_chain"),
    "l3_shadow_health": RepairPolicy("l3_shadow_health", "shadow_self_heal", "security", "R1", target="shadow:data"),
    "l3_shadow_permissions": RepairPolicy(
        "l3_shadow_permissions", "fix_shadow_permissions", "security", "R1", target="shadow:permissions"
    ),
    "s2_shadow_ops_audit_writable": RepairPolicy(
        "s2_shadow_ops_audit_writable", "fix_shadow_permissions", "security", "R1", target="shadow:permissions"
    ),
    "l2_jsonl_parse": RepairPolicy("l2_jsonl_parse", "repair_jsonl", "memory", "R2", target="memory:auto_memory_records"),
    "l2_index_consistency": RepairPolicy("l2_index_consistency", "rebuild_index", "memory", "R2", target="memory:index"),
    "l2_write_then_search": RepairPolicy("l2_write_then_search", "rebuild_index", "memory", "R2", target="memory:index"),
    "c6_client_config_presence": RepairPolicy(
        "c6_client_config_presence", "repair_client_configs", "connect", "R2", target="connect:client_configs"
    ),
    "c8_connect_report_health": RepairPolicy(
        "c8_connect_report_health", "regen_connect_report", "connect", "R1", target="connect:report"
    ),
    "m1_short_term_persistence": RepairPolicy("m1_short_term_persistence", "reload_short_term", "memory", "R1", target="memory:short_term"),
    "m5_semantic_cache_health": RepairPolicy("m5_semantic_cache_health", "repair_semantic_cache", "memory", "R1", target="memory:semantic_cache"),
    "l3_manifest_signature": RepairPolicy(
        "l3_manifest_signature", "shadow_reset_checkpoint", "security", "R2", target="shadow:checkpoint"
    ),
    "l3_checkpoint_verify": RepairPolicy(
        "l3_checkpoint_verify", "shadow_reset_checkpoint", "security", "R2", target="shadow:checkpoint"
    ),
    "l3_spool_backlog": RepairPolicy(
        "l3_spool_backlog",
        "shadow_replay_spool",
        "security",
        "R2",
        target="shadow:spool",
        depends_on=["shadow_reset_checkpoint"],
    ),
    "l3_seal_history": RepairPolicy("l3_seal_history", "seal_history_recover", "security", "R2", target="shadow:seal_chain"),
}


HOOKS: Dict[str, PolicyHooks] = {
    "restart_launchd_mcp": PolicyHooks(_noop, _dry_launchd("com.openclaw.memory.mcp"), _restart_launchd("com.openclaw.memory.mcp"), _noop),
    "restart_launchd_maintenance": PolicyHooks(
        _noop, _dry_launchd("com.openclaw.memory.maintenance"), _restart_launchd("com.openclaw.memory.maintenance"), _noop
    ),
    "cleanup_disk": PolicyHooks(_noop, _dry_cleanup_disk, _cleanup_disk, _noop),
    "restore_core_files": PolicyHooks(_noop, _dry_restore_core_files, _restore_core_files, _rollback_manual("restore_core_files")),
    "refresh_health_card": PolicyHooks(_noop, _dry_ok, _refresh_health_card, _rollback_manual("refresh_health_card")),
    "run_self_check_l1": PolicyHooks(_noop, _dry_self_check_l1, _run_self_check_l1, _noop),
    "shadow_self_heal": PolicyHooks(_pre_shadow, _dry_ok, _shadow_self_heal, _rollback_manual("shadow_self_heal")),
    "fix_shadow_permissions": PolicyHooks(_pre_shadow, _dry_ok, _fix_shadow_permissions, _rollback_manual("fix_shadow_permissions")),
    "repair_jsonl": PolicyHooks(lambda c, x: _pre_file_exists(c, x, "auto_memory_records.jsonl"), _dry_jsonl, _jsonl_repair, _rollback_jsonl),
    "rebuild_index": PolicyHooks(_noop, _dry_rebuild_index, _rebuild_index, _rollback_rebuild_index),
    "repair_client_configs": PolicyHooks(_noop, _dry_client_configs, _repair_client_configs, _rollback_client_configs),
    "regen_connect_report": PolicyHooks(_noop, _dry_client_configs, _regen_connect_report, _rollback_manual("regen_connect_report")),
    "reload_short_term": PolicyHooks(_noop, _dry_ok, _reload_short_term, _rollback_manual("reload_short_term")),
    "repair_semantic_cache": PolicyHooks(_noop, _dry_ok, _repair_semantic_cache, _rollback_manual("repair_semantic_cache")),
    "shadow_reset_checkpoint": PolicyHooks(_pre_shadow, _dry_shadow_reset, _shadow_reset_checkpoint, _rollback_manual("shadow_reset_checkpoint")),
    "shadow_replay_spool": PolicyHooks(_pre_shadow, _dry_shadow_replay, _shadow_replay, _rollback_manual("shadow_replay_spool")),
    "seal_history_recover": PolicyHooks(_pre_shadow, _dry_shadow_replay, _seal_history_recover, _rollback_manual("seal_history_recover")),
    "probe_write_then_search": PolicyHooks(_noop, _dry_write_then_search_probe, _probe_write_then_search, _rollback_manual("probe_write_then_search")),
}


def get_policy(check_id: str) -> RepairPolicy | None:
    return POLICIES.get(str(check_id or ""))


def get_hooks(action: str) -> PolicyHooks | None:
    return HOOKS.get(str(action or ""))
