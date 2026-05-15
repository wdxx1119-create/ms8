"""Automatic memory extraction from interactions (hybrid rule-first pipeline)."""
# Subdomain: ingestion (logical taxonomy; path unchanged)
from __future__ import annotations

import json
import hashlib
import re
import glob
import threading
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from .file_write_guard import atomic_write_json

try:
    from app.main import build_pipeline
except Exception:  # pragma: no cover
    build_pipeline = None


class AutoMemoryExtractor:
    """Rule-first memory extraction with gray-zone LLM fallback."""

    @staticmethod
    def _utc_now() -> datetime:
        return datetime.now(timezone.utc)

    def __init__(self, memory_core: Any) -> None:
        self.memory_core = memory_core
        settings = memory_core.config["settings"]["memory"].get("auto_memory", {})
        self.enabled = bool(settings.get("enabled", True))
        self.use_llm = bool(settings.get("use_llm", False))

        wm_cfg = memory_core.config["settings"]["memory"].get("working_memory", {})
        auto_thresholds = settings.get("thresholds", {})
        self.promotion_min_confidence = float(
            auto_thresholds.get(
                "global_min_confidence",
                wm_cfg.get("long_term_promotion_min_confidence", 0.66),
            )
        )
        self.promotion_categories = set(wm_cfg.get("long_term_promotion_categories", []))
        self.allow_pending_review = bool(wm_cfg.get("long_term_promotion_allow_pending_review", False))

        log_path = settings.get("log_file", "memory/auto_memory_log.json")
        self.log_file = Path(log_path)
        if not self.log_file.is_absolute():
            self.log_file = memory_core.config["workspace_dir"] / self.log_file
        self.log_file.parent.mkdir(parents=True, exist_ok=True)
        if not self.log_file.exists():
            atomic_write_json(self.log_file, {"entries": []}, ensure_ascii=False, indent=2)

        session_cfg = settings.get("session_ingestion", {})
        self.session_ingest_enabled = bool(session_cfg.get("enabled", True))
        self.session_allowed_roles = {str(r).strip().lower() for r in session_cfg.get("allowed_roles", ["user"])}
        sessions_dir_raw = str(session_cfg.get("sessions_dir", str(Path.home() / ".openclaw" / "agents" / "main" / "sessions")))
        self.session_dir = Path(sessions_dir_raw).expanduser()
        self.session_dirs_glob = str(session_cfg.get("sessions_dirs_glob", str(Path.home() / ".openclaw" / "agents" / "*" / "sessions")))
        self.session_scan_limit_files = int(session_cfg.get("scan_limit_files", 40))
        self.session_max_messages_per_run = int(session_cfg.get("max_messages_per_run", 120))
        self.session_min_chars = int(session_cfg.get("min_message_chars", 8))
        self.session_max_chars = int(session_cfg.get("max_message_chars", 1600))
        self.session_sync_interval_seconds = int(session_cfg.get("sync_interval_seconds", 45))
        self.session_process_timeout_seconds = int(session_cfg.get("process_timeout_seconds", 8))
        self.session_test_keywords = [str(x).lower() for x in session_cfg.get("test_keywords", [])]
        state_raw = str(session_cfg.get("state_file", "memory/openclaw_session_ingest_state.json"))
        self.session_state_file = Path(state_raw)
        if not self.session_state_file.is_absolute():
            self.session_state_file = memory_core.config["workspace_dir"] / self.session_state_file
        self.session_state_file.parent.mkdir(parents=True, exist_ok=True)

        self.pipeline = None
        if build_pipeline is not None:
            try:
                self.pipeline = build_pipeline(memory_core.config["workspace_dir"], memory_core.config["settings"].get("memory", {}))
            except Exception as exc:  # fallback to disabled pipeline
                self._save_log(
                    {
                        "timestamp": datetime.now().isoformat(),
                        "status": "error",
                        "source": "init",
                        "reason": f"pipeline_build_failed:{exc}",
                    }
                )
                self.pipeline = None

    def _load_session_state(self) -> Dict[str, Any]:
        if not self.session_state_file.exists():
            return {"files": {}, "recent_hashes": [], "last_sync_at": ""}
        try:
            state = json.loads(self.session_state_file.read_text(encoding="utf-8"))
            if not isinstance(state, dict):
                return {"files": {}, "recent_hashes": [], "last_sync_at": ""}
            state.setdefault("files", {})
            state.setdefault("recent_hashes", [])
            state.setdefault("last_sync_at", "")
            return state
        except Exception:
            return {"files": {}, "recent_hashes": [], "last_sync_at": ""}

    def _save_session_state(self, state: Dict[str, Any]) -> None:
        state["recent_hashes"] = list(state.get("recent_hashes", []))[-2500:]
        atomic_write_json(self.session_state_file, state, ensure_ascii=False, indent=2)

    def _extract_text_from_message(self, row: Dict[str, Any]) -> Tuple[str, str, str]:
        message = row.get("message", {}) if isinstance(row, dict) else {}
        role = str(message.get("role", "")).strip().lower()
        content = message.get("content")
        parts: List[str] = []
        if isinstance(content, list):
            for item in content:
                if not isinstance(item, dict):
                    continue
                if item.get("type") != "text":
                    continue
                text = str(item.get("text", "")).strip()
                if text:
                    parts.append(text)
        elif isinstance(content, str):
            if content.strip():
                parts.append(content.strip())
        text = "\n".join(parts).strip()
        text = self._sanitize_session_text(text)
        msg_id = str(row.get("id", ""))
        return role, text, msg_id

    def _sanitize_session_text(self, text: str) -> str:
        raw = (text or "").strip()
        if not raw:
            return ""

        hard_drop_tokens = [
            "Read HEARTBEAT.md if it exists",
            "OpenClaw runtime context (internal)",
            "A new session was started via /new or /reset",
            "Run your Session Startup sequence",
        ]
        for token in hard_drop_tokens:
            if token.lower() in raw.lower():
                return ""

        cleaned = raw
        # Strip wrappers like Sender/Conversation metadata blocks but keep real prompt body after them.
        cleaned = re.sub(
            r"(?is)(^|\n)\s*Conversation info \(untrusted metadata\):\s*```json.*?```\s*",
            "\n",
            cleaned,
        )
        cleaned = re.sub(
            r"(?is)(^|\n)\s*Sender \(untrusted metadata\):\s*```json.*?```\s*",
            "\n",
            cleaned,
        )
        cleaned = re.sub(
            r"(?is)(^|\n)\s*Replied message \(untrusted, for context\):\s*```json.*?```\s*",
            "\n",
            cleaned,
        )
        cleaned = re.sub(
            r"(?is)(^|\n)\s*System:\s*\[[^\]]+\]\s*Exec completed.*",
            "\n",
            cleaned,
        )
        cleaned = re.sub(r"(?m)^\[[^\]]+\]\s*", "", cleaned)  # drop prefixed timestamp tags
        cleaned = re.sub(r"(?m)^\s*Current time:.*$", "", cleaned)
        cleaned = re.sub(r"\n{3,}", "\n\n", cleaned).strip()
        return cleaned

    def _get_session_dirs(self) -> List[Path]:
        out: List[Path] = []
        if self.session_dir.exists():
            out.append(self.session_dir)
        pattern = self.session_dirs_glob
        if pattern.startswith("~"):
            pattern = str(Path(pattern).expanduser())
        for raw in sorted(glob.glob(pattern)):
            p = Path(raw)
            if p.is_dir() and p not in out:
                out.append(p)
        return out

    def _iter_session_files(self) -> List[Path]:
        files: List[Path] = []
        for d in self._get_session_dirs():
            files.extend([p for p in d.glob("*.jsonl") if p.is_file()])
        files = sorted(files, key=lambda p: p.stat().st_mtime, reverse=True)
        return files[: max(1, self.session_scan_limit_files)]

    def _build_source(self, fp: Path, session_id: str) -> str:
        parts = fp.parts
        agent = "main"
        if "agents" in parts:
            idx = parts.index("agents")
            if idx + 1 < len(parts):
                agent = parts[idx + 1]
        return f"openclaw_session:{agent}:{session_id}"

    def _process_interaction_with_timeout(self, text: str, source: str) -> bool:
        holder: Dict[str, Any] = {"error": None}

        def runner() -> None:
            try:
                self.process_interaction(text, source=source)
            except Exception as exc:
                holder["error"] = exc

        thread = threading.Thread(target=runner, daemon=True)
        thread.start()
        thread.join(timeout=max(1, self.session_process_timeout_seconds))
        if thread.is_alive():
            return False
        return holder["error"] is None

    def _is_session_noise(self, text: str) -> bool:
        stripped = (text or "").strip()
        if len(stripped) < max(1, self.session_min_chars):
            return True
        if len(stripped) > max(self.session_min_chars, self.session_max_chars):
            return True
        lowered = stripped.lower()
        default_noise = [
            "a new session was started via /new or /reset",
            "run your session startup sequence",
            "[subagent context]",
            "[subagent task]",
            "openclaw runtime context",
            "read heartbeat.md",
            "exec completed (quiet-shell)",
            "system:",
            "verification interaction",
            "working_memory_check",
            "监控验证",
        ]
        for token in default_noise:
            if token in lowered:
                return True
        for token in self.session_test_keywords:
            if token and token in lowered:
                return True
        return False

    def sync_openclaw_sessions(self, force: bool = False, max_messages: Optional[int] = None) -> Dict[str, Any]:
        if not self.session_ingest_enabled:
            return {"status": "skipped", "reason": "session_ingest_disabled"}
        if not self.enabled:
            return {"status": "skipped", "reason": "auto_memory_disabled"}
        if not self.pipeline:
            return {"status": "skipped", "reason": "pipeline_unavailable"}
        if not self._get_session_dirs():
            return {"status": "skipped", "reason": "session_dir_missing", "path": str(self.session_dir)}

        state = self._load_session_state()
        now = self._utc_now()
        if not force:
            last_sync_raw = str(state.get("last_sync_at", ""))
            if last_sync_raw:
                try:
                    raw = str(last_sync_raw).strip()
                    if raw.endswith("Z"):
                        raw = raw[:-1] + "+00:00"
                    dt = datetime.fromisoformat(raw)
                    if dt.tzinfo is None:
                        dt = dt.replace(tzinfo=timezone.utc)
                    else:
                        dt = dt.astimezone(timezone.utc)
                    elapsed = (now - dt).total_seconds()
                    if elapsed < max(1, self.session_sync_interval_seconds):
                        return {"status": "skipped", "reason": "session_sync_not_due"}
                except Exception:
                    pass

        limit = int(max_messages or self.session_max_messages_per_run)
        files_state = state.get("files", {})
        recent_hashes = set(state.get("recent_hashes", []))
        processed = 0
        skipped_noise = 0
        skipped_dupe = 0
        skipped_error = 0
        scanned_files = 0

        files = self._iter_session_files()
        for fp in files:
            scanned_files += 1
            key = str(fp)
            offset = int(files_state.get(key, 0) or 0)
            try:
                lines = fp.read_text(encoding="utf-8").splitlines()
            except Exception:
                continue
            if offset > len(lines):
                offset = 0
            session_id = fp.stem
            for idx in range(offset, len(lines)):
                line = lines[idx].strip()
                if not line:
                    continue
                try:
                    row = json.loads(line)
                except Exception:
                    continue
                if row.get("type") == "session":
                    session_id = str(row.get("id", session_id))
                    continue
                if row.get("type") != "message":
                    continue
                role, text, msg_id = self._extract_text_from_message(row)
                if role not in self.session_allowed_roles:
                    continue
                if self._is_session_noise(text):
                    skipped_noise += 1
                    continue
                payload_hash = hashlib.sha1(f"{key}|{msg_id}|{text}".encode("utf-8")).hexdigest()
                if payload_hash in recent_hashes:
                    skipped_dupe += 1
                    continue

                source = self._build_source(fp, session_id)
                ok = self._process_interaction_with_timeout(text, source=source)
                if not ok:
                    skipped_error += 1
                    continue
                recent_hashes.add(payload_hash)
                processed += 1

                if processed >= limit:
                    files_state[key] = idx + 1
                    state["files"] = files_state
                    state["recent_hashes"] = list(recent_hashes)[-2500:]
                    state["last_sync_at"] = now.isoformat()
                    self._save_session_state(state)
                    summary = {
                        "timestamp": datetime.now().isoformat(),
                        "status": "session_sync",
                        "processed": processed,
                        "scanned_files": scanned_files,
                        "skipped_noise": skipped_noise,
                        "skipped_dupe": skipped_dupe,
                        "skipped_error": skipped_error,
                        "truncated": True,
                    }
                    self._save_log(summary)
                    return summary

            files_state[key] = len(lines)

        state["files"] = files_state
        state["recent_hashes"] = list(recent_hashes)[-2500:]
        state["last_sync_at"] = now.isoformat()
        self._save_session_state(state)
        summary = {
            "timestamp": datetime.now().isoformat(),
            "status": "session_sync",
            "processed": processed,
            "scanned_files": scanned_files,
            "skipped_noise": skipped_noise,
            "skipped_dupe": skipped_dupe,
            "skipped_error": skipped_error,
            "truncated": False,
        }
        self._save_log(summary)
        return summary

    def _load_log(self) -> Dict[str, Any]:
        if not self.log_file.exists():
            return {"entries": []}
        try:
            return json.loads(self.log_file.read_text(encoding="utf-8"))
        except Exception:
            return {"entries": []}

    def _save_log(self, entry: Dict[str, Any]) -> None:
        state = self._load_log()
        entries = state.get("entries", [])
        entries.append(entry)
        state["entries"] = entries[-500:]
        atomic_write_json(self.log_file, state, ensure_ascii=False, indent=2)

    def _should_promote_to_long_term(self, record: Any) -> Tuple[bool, str]:
        status = str(getattr(record, "status", ""))
        if status == "pending_review" and not self.allow_pending_review:
            return False, "pending_review_blocked"
        if status not in {"accepted", "pending_review"}:
            return False, f"status_blocked:{status}"

        confidence = float(getattr(record, "confidence", 0.0) or 0.0)
        if confidence < self.promotion_min_confidence:
            return False, f"below_promotion_confidence:{confidence:.2f}"

        category = str(getattr(record, "category", ""))
        if self.promotion_categories and category not in self.promotion_categories:
            return False, f"category_not_promoted:{category}"

        if bool(getattr(record, "conflict_flag", False)):
            return False, "conflict_flagged"

        return True, "promoted"

    def process_interaction(self, text: str, source: str = "interaction") -> List[Dict[str, Any]]:
        if not self.enabled:
            return []
        if not self.pipeline:
            fallback: List[Dict[str, Any]] = []
            if hasattr(self.memory_core, "remember"):
                try:
                    remember_status = self.memory_core.remember(
                        f"interaction: {str(text)[:80]}",
                        text,
                        auto_generate_reason=True,
                        validate=True,
                        use_llm=self.use_llm,
                    )
                    fallback.append(
                        {
                            "entry": {
                                "timestamp": datetime.now().isoformat(),
                                "source": source,
                                "status": "fallback_remember",
                            },
                            "record": {"text": text, "source": source},
                            "result": remember_status,
                        }
                    )
                except Exception:
                    pass
            self._save_log(
                {
                    "timestamp": datetime.now().isoformat(),
                    "source": source,
                    "status": "skipped",
                    "reason": "pipeline_unavailable",
                }
            )
            return fallback

        result = self.pipeline.process(text, source=source)
        event = {
            "timestamp": datetime.now().isoformat(),
            "source": source,
            "status": result.status,
            "trace_id": result.trace_id,
            "metrics": result.metrics,
            "dropped": result.dropped,
            "records": [asdict(r) for r in result.records],
        }
        self._save_log(event)

        if not result.records and hasattr(self.memory_core, "remember"):
            try:
                remember_status = self.memory_core.remember(
                    f"interaction: {str(text)[:80]}",
                    text,
                    auto_generate_reason=True,
                    validate=True,
                    use_llm=self.use_llm,
                )
                return [{"entry": event, "record": {"text": text, "source": source}, "result": remember_status}]
            except Exception:
                return []

        out: List[Dict[str, Any]] = []
        for record in result.records:
            remember_status: Dict[str, Any] = {"status": "skipped"}
            allowed, reason = self._should_promote_to_long_term(record)
            if allowed and hasattr(self.memory_core, "remember"):
                try:
                    instruction = f"{record.category}: {record.normalized_text[:80]}"
                    remember_status = self.memory_core.remember(
                        instruction,
                        record.text,
                        auto_generate_reason=True,
                        validate=True,
                        use_llm=self.use_llm,
                    )
                except Exception as exc:
                    remember_status = {"status": "error", "error": str(exc)}
            else:
                remember_status = {"status": "skipped", "reason": reason}
            out.append({"entry": event, "record": asdict(record), "result": remember_status})

        return out

    def record_feedback(
        self,
        memory_id: str,
        category: str,
        signal: str,
        helpful: bool,
        note: str = "",
        source: str = "user",
        confidence: float = 0.0,
    ) -> Dict[str, Any]:
        if not self.pipeline or not hasattr(self.pipeline, "record_feedback"):
            return {"status": "skipped", "reason": "pipeline_feedback_unavailable"}
        return self.pipeline.record_feedback(
            memory_id=memory_id,
            category=category,
            signal=signal,
            helpful=helpful,
            note=note,
            source=source,
            confidence=confidence,
        )

    def weekly_threshold_suggestions(self) -> Dict[str, Any]:
        if not self.pipeline or not hasattr(self.pipeline, "weekly_threshold_suggestions"):
            return {"status": "skipped", "reason": "pipeline_feedback_unavailable"}
        return self.pipeline.weekly_threshold_suggestions()
