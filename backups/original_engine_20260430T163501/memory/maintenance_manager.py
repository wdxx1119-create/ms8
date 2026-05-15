"""Automatic long-term memory maintenance manager."""
from __future__ import annotations

import json
import shutil
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List
from .file_write_guard import atomic_write_json, guarded_file_write, secure_read_text
from .security import CryptoLockedError, get_crypto_manager
from .utils import list_daily_log_files


class MaintenanceManager:
    @staticmethod
    def _utc_now() -> datetime:
        return datetime.now(timezone.utc)

    def __init__(self, config: Dict[str, Any], file_store: Any) -> None:
        self.config = config
        self.file_store = file_store
        self.settings = config["settings"]["memory"].get("maintenance", {})
        self.enabled = bool(self.settings.get("enabled", True))
        self.workspace = config["workspace_dir"]
        self.memory_dir = config["memory_dir"]
        self.memory_md = config["memory_md"]

        self.state_file = self._resolve(self.settings.get("state_file", "memory/maintenance_state.json"))
        self.backup_dir = self._resolve(self.settings.get("backup_dir", "memory/backups"))
        self.sync_audit_file = self._resolve(self.settings.get("sync_audit_file", "memory/memory_sync_audit.jsonl"))
        self.crypto = get_crypto_manager(config)
        self.state_file.parent.mkdir(parents=True, exist_ok=True)
        self.backup_dir.mkdir(parents=True, exist_ok=True)
        self.sync_audit_file.parent.mkdir(parents=True, exist_ok=True)
        if not self.sync_audit_file.exists():
            with guarded_file_write(self.sync_audit_file):
                self.sync_audit_file.write_text("", encoding="utf-8")

        if not self.state_file.exists():
            self._save_state({
                "last_backup_at": "",
                "last_sync_at": "",
                "last_cleanup_at": "",
                "last_restore_drill_at": "",
                "synced_auto_memory_ids": [],
            })

    def _resolve(self, raw: str) -> Path:
        path = Path(raw).expanduser()
        return path if path.is_absolute() else self.workspace / path

    def _load_state(self) -> Dict[str, Any]:
        try:
            return json.loads(self.state_file.read_text(encoding="utf-8"))
        except Exception:
            return {
                "last_backup_at": "",
                "last_sync_at": "",
                "last_cleanup_at": "",
                "last_restore_drill_at": "",
                "synced_auto_memory_ids": [],
            }

    def _save_state(self, state: Dict[str, Any]) -> None:
        atomic_write_json(self.state_file, state, ensure_ascii=False, indent=2)

    def _append_sync_audit(self, payload: Dict[str, Any]) -> None:
        row = {"timestamp": self._utc_now().isoformat(), **payload}
        with guarded_file_write(self.sync_audit_file):
            with self.sync_audit_file.open("a", encoding="utf-8") as f:
                f.write(json.dumps(row, ensure_ascii=False) + "\n")

    def _parse_time(self, value: str) -> datetime | None:
        if not value:
            return None
        try:
            raw = str(value).strip()
            if raw.endswith("Z"):
                raw = raw[:-1] + "+00:00"
            dt = datetime.fromisoformat(raw)
            if dt.tzinfo is None:
                return dt.replace(tzinfo=timezone.utc)
            return dt.astimezone(timezone.utc)
        except Exception:
            return None

    def _due(self, last_value: str, hours: int) -> bool:
        last = self._parse_time(last_value)
        if not last:
            return True
        return self._utc_now() - last >= timedelta(hours=max(1, hours))

    def ensure_memory_md(self) -> bool:
        if self.memory_md.exists():
            return False
        self.file_store.write_memory_md(self.file_store.read_memory_md())
        return True

    def backup_assets(self) -> Dict[str, Any]:
        blocked = self._blocked_by_lock("backup_assets")
        if blocked:
            return blocked
        if not self.settings.get("backup_enabled", True):
            return {"status": "disabled"}

        keep = int(self.settings.get("backup_keep", 7))
        stamp = self._utc_now().strftime("%Y%m%d_%H%M%S")
        target = self.backup_dir / f"memory_backup_{stamp}"
        target.mkdir(parents=True, exist_ok=True)

        copied: List[str] = []
        for rel in ["MEMORY.md", "memory/memory.db", "memory/knowledge_graph.db", "memory/auto_memory_records.jsonl", "memory/auto_memory_index.json"]:
            src = self.workspace / rel
            if not src.exists():
                continue
            dst = target / src.name
            shutil.copy2(src, dst)
            self._maybe_encrypt_backup_file(dst)
            copied.append(str(dst))

        backups = sorted(self.backup_dir.glob("memory_backup_*"), reverse=True)
        for stale in backups[keep:]:
            if stale.is_dir():
                shutil.rmtree(stale, ignore_errors=True)

        # Tiered retention for historical backups: 7/30/90 days windows.
        pruned = self._prune_backups_tiered()

        return {"status": "success", "backup": str(target), "copied": copied, "pruned": pruned}

    def _maybe_encrypt_backup_file(self, path: Path) -> None:
        if not self.crypto.is_enabled():
            return
        if not self.crypto.is_unlocked():
            raise CryptoLockedError("memory_security_locked")
        if path.suffix.lower() not in {".md", ".json", ".jsonl", ".log", ".db"}:
            return
        raw = path.read_bytes()
        # Avoid double-encrypt.
        file_type = "binary"
        if path.suffix.lower() in {".md", ".txt"}:
            file_type = "text"
        elif path.suffix.lower() in {".json", ".jsonl"}:
            file_type = "json"
        elif path.suffix.lower() == ".log":
            file_type = "log"
        elif path.suffix.lower() in {".db", ".sqlite"}:
            file_type = "sqlite"
        try:
            secured = self.crypto.encrypt_before_write(raw, file_type=file_type, target_path=path)
        except Exception:
            return
        if secured != raw:
            with guarded_file_write(path):
                path.write_bytes(secured)

    def _backup_time_from_name(self, path: Path) -> datetime | None:
        name = path.name
        if not name.startswith("memory_backup_"):
            return None
        raw = name.replace("memory_backup_", "")
        try:
            return datetime.strptime(raw, "%Y%m%d_%H%M%S").replace(tzinfo=timezone.utc)
        except Exception:
            return None

    def _prune_backups_tiered(self) -> Dict[str, Any]:
        cfg = self.settings.get("backup_retention_days", {})
        daily_days = int(cfg.get("daily_full_keep_days", 7))
        weekly_days = int(cfg.get("weekly_sample_keep_days", 30))
        monthly_days = int(cfg.get("monthly_sample_keep_days", 90))
        now = self._utc_now()

        backups = []
        for p in self.backup_dir.glob("memory_backup_*"):
            ts = self._backup_time_from_name(p)
            if ts is None:
                continue
            backups.append((p, ts))
        backups.sort(key=lambda x: x[1], reverse=True)

        keep_paths = set()
        kept_daily = set()
        kept_weekly = set()
        kept_monthly = set()
        removed = []
        for path, ts in backups:
            age_days = (now - ts).days
            if age_days <= daily_days:
                keep_paths.add(path)
                continue
            if age_days <= weekly_days:
                key = ts.strftime("%Y-%m-%d")
                if key not in kept_daily:
                    kept_daily.add(key)
                    keep_paths.add(path)
                continue
            if age_days <= monthly_days:
                key = f"{ts.isocalendar().year}-W{ts.isocalendar().week:02d}"
                if key not in kept_weekly:
                    kept_weekly.add(key)
                    keep_paths.add(path)
                continue
            key = ts.strftime("%Y-%m")
            if key not in kept_monthly:
                kept_monthly.add(key)
                keep_paths.add(path)
                continue
            removed.append(path)

        for path, _ in backups:
            if path in keep_paths:
                continue
            try:
                shutil.rmtree(path, ignore_errors=True)
                removed.append(path)
            except Exception:
                continue
        # unique removed paths
        removed_unique = []
        seen = set()
        for p in removed:
            if str(p) in seen:
                continue
            seen.add(str(p))
            removed_unique.append(str(p))
        return {"removed": removed_unique, "kept": len(keep_paths)}

    def sync_memory_md_from_auto_memory(self, limit: int = 80) -> Dict[str, Any]:
        blocked = self._blocked_by_lock("sync_memory_md_from_auto_memory")
        if blocked:
            self._append_sync_audit(blocked)
            return blocked
        if not self.settings.get("sync_memory_md", True):
            out = {"status": "disabled"}
            self._append_sync_audit({"status": "disabled", "reason": "sync_memory_md_disabled"})
            return out

        auto_file = self.memory_dir / "auto_memory_records.jsonl"
        if not auto_file.exists():
            out = {"status": "skipped", "reason": "no_auto_memory_records"}
            self._append_sync_audit(out)
            return out

        state = self._load_state()
        synced_ids = set(state.get("synced_auto_memory_ids", []))
        fresh: List[Dict[str, Any]] = []
        for line in secure_read_text(auto_file).splitlines():
            if not line.strip():
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            meta = row.get("meta", {})
            rid = str(row.get("id", "") or meta.get("id", ""))
            if not rid or rid in synced_ids:
                continue
            if row.get("status") != "accepted":
                continue
            if float(row.get("confidence", 0.0) or 0.0) < 0.72:
                continue
            fresh.append(row)

        if not fresh:
            out = {"status": "skipped", "reason": "no_new_high_quality_records", "candidate_count": 0}
            self._append_sync_audit(out)
            return out

        fresh = fresh[-limit:]
        section_title = f"## Auto Memory Sync - {self._utc_now().date().isoformat()}"
        lines = [section_title]
        for row in fresh:
            rid = str(row.get("id", "") or row.get("meta", {}).get("id", ""))
            category = row.get("category", "unknown")
            conf = float(row.get("confidence", 0.0) or 0.0)
            base_text = str(row.get("normalized_text", row.get("text", ""))).replace("\n", " ").strip()
            try:
                from app.pipeline.memory_admission_engine import evaluate_candidate
                decision = evaluate_candidate(base_text, metadata={"source": "maintenance_sync"})
                if not bool(decision.should_write_memory_md):
                    continue
                text = str(decision.normalized_text).replace("\n", " ").strip()
            except Exception:
                try:
                    from app.rules.privacy_rules import redact_sensitive_text
                    text = str(redact_sensitive_text(base_text).get("redacted_text", base_text)).replace("\n", " ").strip()
                except Exception:
                    text = base_text
            text = text[:180]
            lines.append(f"- [{category}] ({conf:.2f}) {text}")
            if rid:
                synced_ids.add(rid)

        current = self.file_store.read_memory_md()
        if section_title in current:
            state["synced_auto_memory_ids"] = list(synced_ids)[-1000:]
            self._save_state(state)
            out = {
                "status": "skipped",
                "reason": "already_synced_today",
                "candidate_count": len(fresh),
                "marked_synced": len(fresh),
            }
            self._append_sync_audit(out)
            return out
        updated = current.rstrip() + "\n\n" + "\n".join(lines) + "\n"
        self.file_store.write_memory_md(updated)

        state["synced_auto_memory_ids"] = list(synced_ids)[-1000:]
        self._save_state(state)
        out = {"status": "success", "synced": len(fresh), "candidate_count": len(fresh)}
        self._append_sync_audit(out)
        return out

    def apply_tiering_plan(self, plan: List[Dict[str, Any]], owner: str = "maintenance") -> Dict[str, Any]:
        """Apply learning-provided tiering plan; final execution authority stays in maintenance."""
        moved: List[str] = []
        skipped: List[str] = []
        for item in plan:
            src = Path(str(item.get("source_path", "")))
            dst = Path(str(item.get("target_path", "")))
            if not src.exists():
                skipped.append(f"missing:{src}")
                continue
            dst.parent.mkdir(parents=True, exist_ok=True)
            if dst.exists():
                skipped.append(f"exists:{dst}")
                continue
            shutil.move(str(src), str(dst))
            moved.append(str(dst))

        return {
            "status": "success",
            "owner": owner,
            "plan_count": len(plan),
            "moved": moved,
            "skipped": skipped,
        }

    def cleanup_old_low_importance_logs(self) -> Dict[str, Any]:
        blocked = self._blocked_by_lock("cleanup_old_low_importance_logs")
        if blocked:
            return blocked
        if not self.settings.get("cleanup_enabled", True):
            return {"status": "disabled"}

        days = int(self.settings.get("cleanup_days", 90))
        cutoff = self._utc_now().date() - timedelta(days=days)
        low_priority_archive = self.memory_dir / "archive" / "low_priority"
        low_priority_archive.mkdir(parents=True, exist_ok=True)

        moved: List[str] = []
        important_markers = ["决定", "计划", "必须", "偏好", "故障", "风险", "deadline"]

        for file_path in list_daily_log_files(self.memory_dir, self.config.get("daily_dir")):
            try:
                file_date = datetime.fromisoformat("-".join(file_path.stem.split("-")[0:3])).date()
            except Exception:
                continue
            if file_date >= cutoff:
                continue

            content = file_path.read_text(encoding="utf-8")
            if any(token in content for token in important_markers):
                continue

            month_dir = low_priority_archive / file_date.strftime("%Y-%m")
            month_dir.mkdir(parents=True, exist_ok=True)
            dst = month_dir / file_path.name
            if dst.exists():
                continue
            shutil.move(str(file_path), str(dst))
            moved.append(str(dst))

        return {"status": "success", "moved": moved}

    def cleanup_memory_md_quality(self) -> Dict[str, Any]:
        blocked = self._blocked_by_lock("cleanup_memory_md_quality")
        if blocked:
            return blocked
        if not self.memory_md.exists():
            return {"status": "skipped", "reason": "memory_md_missing"}
        text = self.memory_md.read_text(encoding="utf-8")
        lines = text.splitlines()
        test_markers = ("verification", "smoke test", "test_key", "verify_canary", "样本", "test_graph_sync")
        cleaned: List[str] = []
        dropped = 0
        for line in lines:
            low = line.strip().lower()
            if line.startswith("## ") and any(m in low for m in test_markers):
                dropped += 1
                continue
            if any(m in low for m in test_markers) and line.strip().startswith("- "):
                dropped += 1
                continue
            cleaned.append(line)
        # Remove duplicate non-empty lines while preserving order.
        seen = set()
        deduped: List[str] = []
        dup_removed = 0
        for line in cleaned:
            key = line.strip()
            if not key:
                deduped.append(line)
                continue
            if key in seen:
                dup_removed += 1
                continue
            seen.add(key)
            deduped.append(line)
        updated = "\n".join(deduped).rstrip() + "\n"
        self.file_store.write_memory_md(updated)
        return {
            "status": "success",
            "dropped_test_lines": dropped,
            "removed_duplicate_lines": dup_removed,
            "final_lines": len(updated.splitlines()),
        }

    def cleanup_legacy_backup_dirs(self) -> Dict[str, Any]:
        blocked = self._blocked_by_lock("cleanup_legacy_backup_dirs")
        if blocked:
            return blocked
        if not self.settings.get("cleanup_legacy_root_backups", True):
            return {"status": "disabled"}
        keep = int(self.settings.get("legacy_backup_keep", 1))
        patterns = ("backup_*", "cleanup_backup_*", "session_fix_backup_*")
        candidates: List[Path] = []
        for pat in patterns:
            candidates.extend(self.workspace.glob(pat))
        candidates = [p for p in candidates if p.is_dir()]
        candidates.sort(key=lambda p: p.stat().st_mtime, reverse=True)
        removed: List[str] = []
        for stale in candidates[keep:]:
            shutil.rmtree(stale, ignore_errors=True)
            removed.append(str(stale))
        # Cleanup one-off snapshots and fix backups in memory dir.
        for pat in ("backup_before_fix_*",):
            for p in self.memory_dir.glob(pat):
                if p.is_dir():
                    shutil.rmtree(p, ignore_errors=True)
                    removed.append(str(p))
        snapshots_keep = int(self.settings.get("cleanup_snapshots_keep", 2))
        snapshots = sorted((self.memory_dir / "cleanup_snapshots").glob("*"), reverse=True)
        for stale in snapshots[snapshots_keep:]:
            if stale.is_dir():
                shutil.rmtree(stale, ignore_errors=True)
            else:
                stale.unlink(missing_ok=True)
            removed.append(str(stale))
        return {"status": "success", "removed": removed, "kept": min(keep, len(candidates))}

    def run_restore_drill(self) -> Dict[str, Any]:
        blocked = self._blocked_by_lock("run_restore_drill")
        if blocked:
            return blocked
        if not self.settings.get("restore_drill_enabled", True):
            return {"status": "disabled"}
        backups = sorted(self.backup_dir.glob("memory_backup_*"), reverse=True)
        if not backups:
            return {"status": "skipped", "reason": "no_backup_found"}
        latest = backups[0]
        stamp = self._utc_now().strftime("%Y%m%d_%H%M%S")
        drill_dir = self.memory_dir / "restore_drill" / f"drill_{stamp}"
        drill_dir.mkdir(parents=True, exist_ok=True)
        restored = []
        for src in latest.glob("*"):
            if not src.is_file():
                continue
            dst = drill_dir / src.name
            shutil.copy2(src, dst)
            restored.append(str(dst))

        checks = {
            "has_memory_md_backup": any(Path(p).name.lower().startswith("memory") and Path(p).suffix == ".md" for p in restored),
            "restored_files_count": len(restored),
        }
        ok = bool(checks["restored_files_count"] > 0 and checks["has_memory_md_backup"])
        report = {
            "timestamp": self._utc_now().isoformat(),
            "backup_source": str(latest),
            "restore_dir": str(drill_dir),
            "checks": checks,
            "status": "success" if ok else "failed",
        }
        report_file = drill_dir / "restore_drill_report.json"
        atomic_write_json(report_file, report, ensure_ascii=False, indent=2)

        keep = int(self.settings.get("restore_drill_keep_reports", 8))
        drills = sorted((self.memory_dir / "restore_drill").glob("drill_*"), reverse=True)
        for stale in drills[keep:]:
            shutil.rmtree(stale, ignore_errors=True)

        return report

    def run_maintenance(self, force: bool = False) -> Dict[str, Any]:
        blocked = self._blocked_by_lock("run_maintenance")
        if blocked:
            return blocked
        if not self.enabled:
            return {"status": "disabled"}

        state = self._load_state()
        interval = int(self.settings.get("backup_interval_hours", 24))

        created_memory_md = self.ensure_memory_md()
        backup_result = {"status": "skipped"}
        sync_result = {"status": "skipped"}
        cleanup_result = {"status": "skipped"}
        memory_md_cleanup_result = {"status": "skipped"}
        legacy_cleanup_result = {"status": "skipped"}
        restore_drill_result = {"status": "skipped"}

        should_run = force or self._due(state.get("last_backup_at", ""), interval)
        if should_run:
            backup_result = self.backup_assets()
            sync_result = self.sync_memory_md_from_auto_memory()
            cleanup_result = self.cleanup_old_low_importance_logs()
            memory_md_cleanup_result = self.cleanup_memory_md_quality()
            legacy_cleanup_result = self.cleanup_legacy_backup_dirs()
            now = self._utc_now().isoformat()
            state["last_backup_at"] = now
            state["last_sync_at"] = now
            state["last_cleanup_at"] = now
            self._save_state(state)

        drill_days = int(self.settings.get("restore_drill_interval_days", 7))
        should_drill = force or self._due(state.get("last_restore_drill_at", ""), max(24, drill_days * 24))
        if should_drill:
            restore_drill_result = self.run_restore_drill()
            state["last_restore_drill_at"] = self._utc_now().isoformat()
            self._save_state(state)

        return {
            "status": "success",
            "created_memory_md": created_memory_md,
            "backup": backup_result,
            "sync": sync_result,
            "cleanup": cleanup_result,
            "memory_md_cleanup": memory_md_cleanup_result,
            "legacy_cleanup": legacy_cleanup_result,
            "restore_drill": restore_drill_result,
            "ran": bool(should_run),
        }

    def _blocked_by_lock(self, action: str) -> Dict[str, Any] | None:
        security_cfg = self.config["settings"]["memory"].get("security", {})
        require_unlock = bool(security_cfg.get("require_unlock_for_maintenance", True))
        if not require_unlock:
            return None
        if self.crypto.is_enabled() and not self.crypto.is_unlocked():
            return {"status": "blocked", "reason": "memory_security_locked", "action": action}
        return None
