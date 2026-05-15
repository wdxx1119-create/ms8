"""
Active learning and memory evolution implementation
"""
import json
import re
import shutil
import schedule
import datetime
import time
import threading
from collections import Counter
from typing import List, Dict, Tuple, Optional, Any
from pathlib import Path

from .config import get_config
from .file_store import FileMemoryStore
from .knowledge_graph import KnowledgeGraph
from .memory_section_parser import parse_memory_sections
from .sqlite_store import SQLiteMemoryStore
from .file_write_guard import atomic_write_json, guarded_file_write
from .security import get_crypto_manager
from .utils import list_daily_log_files

class MemoryLearning:
    """Handle active learning and memory evolution."""
    _GLOBAL_SCHEDULER_LOCK = threading.Lock()
    _GLOBAL_SCHEDULE_REGISTERED = False
    _GLOBAL_SCHEDULER_THREAD: Optional[threading.Thread] = None
    _GLOBAL_SCHEDULER_STOP = threading.Event()
    _GLOBAL_INSTANCE_SEQ = 0
    
    def __init__(self, knowledge_graph: Optional[KnowledgeGraph] = None, memory_core=None):
        self.config = get_config()
        self.file_store = FileMemoryStore()
        self.sqlite_store = SQLiteMemoryStore()
        self.knowledge_graph = knowledge_graph
        self.memory_core = memory_core
        self.crypto = get_crypto_manager(self.config)
        self.learning_enabled = self.config['settings']['memory']['learning']['enabled']
        self.learning_state_file = self.config["memory_dir"] / "learning_runtime_state.json"
        self._scheduler_stop = threading.Event()
        self._scheduler_thread: Optional[threading.Thread] = None
        with MemoryLearning._GLOBAL_SCHEDULER_LOCK:
            MemoryLearning._GLOBAL_INSTANCE_SEQ += 1
            self._instance_id = MemoryLearning._GLOBAL_INSTANCE_SEQ
        
        if self.learning_enabled:
            self._setup_scheduled_tasks()
            self._start_scheduler_loop()
            self._maybe_bootstrap_meta_task()
    
    def _meta_log_path(self) -> Optional[Path]:
        meta_cfg = self.config['settings']['memory'].get('meta_cognition', {})
        log_path = meta_cfg.get('task_log_file', '')
        if not log_path:
            return None
        p = Path(log_path)
        if not p.is_absolute():
            p = self.config['workspace_dir'] / p
        p.parent.mkdir(parents=True, exist_ok=True)
        return p

    def _maybe_bootstrap_meta_task(self) -> None:
        meta_cfg = self.config['settings']['memory'].get('meta_cognition', {})
        if not meta_cfg.get('enabled', False):
            return
        if not self.memory_core:
            self._meta_task_log({
                "timestamp": datetime.datetime.now().isoformat(),
                "status": "skipped",
                "reason": "memory_core_unavailable",
            })
            return
        cooldown_seconds = int(meta_cfg.get('bootstrap_cooldown_seconds', 21600))
        log_path = self._meta_log_path()
        if log_path and log_path.exists() and log_path.stat().st_size > 0:
            try:
                last_line = ''
                for line in log_path.read_text(encoding='utf-8', errors='ignore').splitlines()[-20:]:
                    if line.strip():
                        last_line = line
                if last_line:
                    row = json.loads(last_line)
                    ts = row.get('timestamp')
                    if ts:
                        last = datetime.datetime.fromisoformat(str(ts))
                        if (datetime.datetime.now() - last).total_seconds() < max(60, cooldown_seconds):
                            return
            except Exception:
                pass
        try:
            self._run_meta_cognition_task()
        except Exception:
            pass

    def _setup_scheduled_tasks(self):
        """Setup scheduled learning tasks."""
        with MemoryLearning._GLOBAL_SCHEDULER_LOCK:
            if MemoryLearning._GLOBAL_SCHEDULE_REGISTERED:
                self._log_learning_event(
                    "scheduler_setup",
                    "reused",
                    {"instance_id": self._instance_id},
                )
                return
        daily_time = self.config['settings']['memory']['learning']['daily_summary_time']
        compression_day = self.config['settings']['memory']['learning']['compression_day']
        meta_cfg = self.config['settings']['memory'].get('meta_cognition', {})
        
        # Daily learning task
        schedule.every().day.at(daily_time).do(self._run_daily_learning_with_log).tag("memory_learning_daily")
        
        # Weekly compression task
        getattr(schedule.every(), compression_day.lower()).do(self._run_weekly_compression_with_log).tag("memory_learning_weekly")

        # Meta-cognition scheduled task
        if meta_cfg.get("enabled", False) and self.memory_core:
            interval_hours = int(meta_cfg.get("monitor_interval_hours", 24))
            if interval_hours and interval_hours >= 1:
                schedule.every(interval_hours).hours.do(self._run_meta_cognition_task).tag("memory_learning_meta")
            else:
                schedule.every().day.at(meta_cfg.get("schedule_time", "04:00")).do(self._run_meta_cognition_task).tag("memory_learning_meta")
        # Lightweight auto-review consumer for pending queue.
        auto_review_enabled = bool(self.config["settings"]["memory"]["learning"].get("auto_review_enabled", True))
        if auto_review_enabled and self.memory_core:
            interval_hours = int(self.config["settings"]["memory"]["learning"].get("auto_review_interval_hours", 4))
            schedule.every(max(1, interval_hours)).hours.do(self._run_auto_review_task).tag("memory_learning_review")
        if self.memory_core and bool(self.config["settings"]["memory"]["learning"].get("context_opt_enabled", True)):
            interval_hours = int(self.config["settings"]["memory"]["learning"].get("context_opt_interval_hours", 6))
            schedule.every(max(1, interval_hours)).hours.do(self._run_context_optimization_task).tag("memory_learning_context_opt")
        MemoryLearning._GLOBAL_SCHEDULE_REGISTERED = True

    def _learning_log_path(self) -> Path:
        learning_cfg = self.config["settings"]["memory"].get("learning", {})
        log_path = learning_cfg.get("task_log_file", "")
        path = Path(log_path) if log_path else (self.config["memory_dir"] / "learning_task_log.jsonl")
        if not path.is_absolute():
            path = self.config["workspace_dir"] / path
        path.parent.mkdir(parents=True, exist_ok=True)
        return path

    def _log_learning_event(self, event: str, status: str, detail: Optional[Dict[str, any]] = None) -> None:
        payload = {
            "timestamp": datetime.datetime.now().isoformat(),
            "event": event,
            "status": status,
            "detail": detail or {},
        }
        try:
            log_path = self._learning_log_path()
            with guarded_file_write(log_path):
                with open(log_path, "a", encoding="utf-8") as handle:
                    handle.write(json.dumps(payload, ensure_ascii=False) + "\n")
        except Exception:
            pass

    def _run_daily_learning_with_log(self):
        start = datetime.datetime.now()
        try:
            date_str = (datetime.date.today() - datetime.timedelta(days=1)).isoformat()
            if self._already_daily_done(date_str):
                self._log_learning_event("daily_learning", "skipped", {"reason": "already_done", "date": date_str})
                return
            self.daily_learning_task()
            self._mark_daily_done(date_str)
            self._log_learning_event("daily_learning", "success", {"duration_seconds": (datetime.datetime.now() - start).total_seconds()})
        except Exception as exc:
            self._log_learning_event("daily_learning", "error", {"error": str(exc)})
            raise

    def _run_weekly_compression_with_log(self):
        start = datetime.datetime.now()
        try:
            self.weekly_compression_task()
            self._log_learning_event("weekly_compression", "success", {"duration_seconds": (datetime.datetime.now() - start).total_seconds()})
        except Exception as exc:
            self._log_learning_event("weekly_compression", "error", {"error": str(exc)})
            raise

    def _start_scheduler_loop(self) -> None:
        with MemoryLearning._GLOBAL_SCHEDULER_LOCK:
            existing = MemoryLearning._GLOBAL_SCHEDULER_THREAD
            if existing and existing.is_alive():
                self._scheduler_thread = existing
                self._log_learning_event(
                    "scheduler",
                    "reused",
                    {"instance_id": self._instance_id},
                )
                return

        poll_seconds = int(self.config["settings"]["memory"].get("learning", {}).get("scheduler_poll_seconds", 60))

        def _runner() -> None:
            while not MemoryLearning._GLOBAL_SCHEDULER_STOP.is_set():
                try:
                    schedule.run_pending()
                except Exception as exc:
                    self._log_learning_event("scheduler_tick", "error", {"error": str(exc)})
                MemoryLearning._GLOBAL_SCHEDULER_STOP.wait(max(5, poll_seconds))

        thread = threading.Thread(target=_runner, daemon=True)
        thread.start()
        with MemoryLearning._GLOBAL_SCHEDULER_LOCK:
            MemoryLearning._GLOBAL_SCHEDULER_THREAD = thread
            self._scheduler_thread = thread
        self._log_learning_event("scheduler", "started", {"poll_seconds": poll_seconds, "instance_id": self._instance_id})

    def _meta_task_log(self, payload: Dict[str, any]) -> None:
        meta_cfg = self.config['settings']['memory'].get('meta_cognition', {})
        log_path = meta_cfg.get('task_log_file', '')
        if not log_path:
            return
        log_path = Path(log_path)
        if not log_path.is_absolute():
            log_path = self.config['workspace_dir'] / log_path
        log_path.parent.mkdir(parents=True, exist_ok=True)
        try:
            with guarded_file_write(log_path):
                with open(log_path, 'a', encoding='utf-8') as handle:
                    handle.write(json.dumps(payload, ensure_ascii=False) + '\n')
        except Exception:
            pass

    def _run_meta_cognition_task(self):
        """Run meta-cognition monitoring if enabled."""
        if not self.memory_core or not hasattr(self.memory_core, "run_meta_cognition"):
            self._meta_task_log({
                "timestamp": datetime.datetime.now().isoformat(),
                "status": "skipped",
                "reason": "meta_api_unavailable",
            })
            return
        self._log_learning_event("meta_cognition", "scheduled", {})
        retries = [60, 300, 900]
        attempt = 0
        start = datetime.datetime.now()
        while True:
            attempt += 1
            try:
                result = self.memory_core.run_meta_cognition()
                self._meta_task_log({
                    "timestamp": datetime.datetime.now().isoformat(),
                    "status": "success",
                    "attempt": attempt,
                    "duration_seconds": (datetime.datetime.now() - start).total_seconds(),
                    "result": result,
                })
                self._log_learning_event("meta_cognition", "success", {"attempt": attempt})
                return
            except Exception as exc:
                self._meta_task_log({
                    "timestamp": datetime.datetime.now().isoformat(),
                    "status": "error",
                    "attempt": attempt,
                    "error": str(exc),
                })
                self._log_learning_event("meta_cognition", "error", {"attempt": attempt, "error": str(exc)})
                if attempt > len(retries):
                    print(f"[MemoryLearning] meta cognition task error: {exc}")
                    return
                time.sleep(retries[attempt - 1])

    def _run_auto_review_task(self):
        if not self.memory_core or not hasattr(self.memory_core, "batch_review"):
            return
        lcfg = self.config["settings"]["memory"]["learning"]
        mode = str(lcfg.get("auto_review_mode", "accept_low_risk"))
        limit = int(lcfg.get("auto_review_batch_limit", 30))
        accept_min = float(lcfg.get("auto_review_accept_conf_min", 0.62))
        reject_max = float(lcfg.get("auto_review_reject_conf_max", 0.20))
        per_cat_limit = int(lcfg.get("auto_review_per_category_limit", 6))
        try:
            result = self.memory_core.batch_review(
                mode=mode,
                limit=limit,
                accept_conf_min=accept_min,
                reject_conf_max=reject_max,
                per_category_limit=per_cat_limit,
            )
            self._log_learning_event(
                "auto_review",
                "success",
                {
                    "mode": mode,
                    "limit": limit,
                    "accept_conf_min": accept_min,
                    "reject_conf_max": reject_max,
                    "per_category_limit": per_cat_limit,
                    "result": result,
                },
            )
        except Exception as exc:
            self._log_learning_event("auto_review", "error", {"mode": mode, "error": str(exc)})

    def _run_context_optimization_task(self):
        if not self.memory_core or not hasattr(self.memory_core, "get_context_optimization_suggestions"):
            return
        window = int(self.config["settings"]["memory"]["learning"].get("context_opt_window", 300))
        try:
            result = self.memory_core.get_context_optimization_suggestions(window=window)
            self._log_learning_event("context_optimization", "success", {"window": window, "result": result})
        except Exception as exc:
            self._log_learning_event("context_optimization", "error", {"window": window, "error": str(exc)})

    def _load_learning_state(self) -> Dict[str, Any]:
        try:
            return json.loads(self.learning_state_file.read_text(encoding="utf-8"))
        except Exception:
            return {"daily_done_dates": []}

    def _save_learning_state(self, state: Dict[str, Any]) -> None:
        self.learning_state_file.parent.mkdir(parents=True, exist_ok=True)
        with guarded_file_write(self.learning_state_file):
            atomic_write_json(self.learning_state_file, state, ensure_ascii=False, indent=2)

    def _already_daily_done(self, date_str: str) -> bool:
        state = self._load_learning_state()
        dates = [str(x) for x in state.get("daily_done_dates", [])]
        return date_str in dates

    def _mark_daily_done(self, date_str: str) -> None:
        state = self._load_learning_state()
        dates = [str(x) for x in state.get("daily_done_dates", [])]
        if date_str not in dates:
            dates.append(date_str)
        state["daily_done_dates"] = dates[-30:]
        state["last_daily_done_at"] = datetime.datetime.now(datetime.timezone.utc).isoformat()
        self._save_learning_state(state)
    
    def extract_entities_from_text(self, text: str) -> List[str]:
        """
        Extract entities from text using simple rules.
        This is a basic implementation that can be enhanced later.
        """
        # Rule 1: Proper nouns (capitalized words)
        proper_nouns = re.findall(r'\b[A-Z][a-z]+(?:\s+[A-Z][a-z]+)*\b', text)
        
        # Rule 2: Email addresses
        emails = re.findall(r'\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Z|a-z]{2,}\b', text)
        
        # Rule 3: URLs
        urls = re.findall(r'https?://[^\s]+', text)
        
        # Rule 4: Phone numbers (basic pattern)
        phones = re.findall(r'\b\d{3}[-.]?\d{3}[-.]?\d{4}\b', text)
        
        # Combine all entities
        all_entities = proper_nouns + emails + urls + phones
        
        # Remove duplicates while preserving order
        seen = set()
        unique_entities = []
        for entity in all_entities:
            if entity not in seen:
                seen.add(entity)
                unique_entities.append(entity)
        
        return unique_entities
    
    def analyze_daily_log(self, date_str: str) -> Dict[str, any]:
        """
        Analyze a daily log file and extract knowledge.
        
        Args:
            date_str: Date string in YYYY-MM-DD format
            
        Returns:
            Dictionary containing extracted entities, relations, and summary
        """
        daily_dir = self.config.get("daily_dir", self.config["memory_dir"] / "daily")
        log_file = daily_dir / f"{date_str}.md"
        if not log_file.exists():
            # Legacy flat layout fallback.
            log_file = self.config["memory_dir"] / f"{date_str}.md"
        
        if not log_file.exists():
            return {}
        
        with open(log_file, 'r', encoding='utf-8') as f:
            content = f.read()
        
        # Extract entities
        entities = self.extract_entities_from_text(content)
        
        # Count entity frequencies
        entity_freq = Counter(entities)
        
        # Extract relations (simple co-occurrence within sentences)
        sentences = re.split(r'[.!?]+', content)
        relations = []
        
        for sentence in sentences:
            sent_entities = [e for e in entities if e in sentence]
            # Create relations between co-occurring entities
            for i in range(len(sent_entities)):
                for j in range(i + 1, len(sent_entities)):
                    relations.append((sent_entities[i], "co_occurs_with", sent_entities[j]))
        
        # Create summary
        top_entities = [entity for entity, _ in entity_freq.most_common(5)]
        summary = f"Learned about: {', '.join(top_entities[:3])}" if top_entities else "No significant entities found."
        
        return {
            'entities': list(entity_freq.keys()),
            'entity_frequencies': dict(entity_freq),
            'relations': relations,
            'summary': summary,
            'total_entities': len(entities),
            'unique_entities': len(entity_freq)
        }
    
    def daily_learning_task(self):
        """Perform daily learning task on yesterday's log."""
        if self._security_locked():
            self._log_learning_event("daily_learning", "skipped", {"reason": "memory_security_locked"})
            return
        if not self.learning_enabled:
            return
        
        # Get yesterday's date
        yesterday = (datetime.date.today() - datetime.timedelta(days=1)).isoformat()
        
        # Analyze yesterday's log
        analysis = self.analyze_daily_log(yesterday)
        
        if not analysis:
            return
        
        # Store entities in SQLite
        for entity in analysis['entities']:
            self.sqlite_store.add_entity(entity, "learned")
        
        # Store relations in SQLite
        for subject, predicate, obj in analysis['relations']:
            self.sqlite_store.add_relation(subject, predicate, obj, strength=0.5)
        
        # Update MEMORY.md with learning summary
        current_memory = self.file_store.read_memory_md()
        learning_section = f"\n## Learning Summary - {yesterday}\n{analysis['summary']}"
        safe_section = learning_section
        try:
            from app.pipeline.memory_admission_engine import evaluate_candidate
            decision = evaluate_candidate(learning_section, metadata={"source": "daily_learning_task"})
            if not bool(decision.should_write_memory_md):
                return
            safe_section = str(decision.normalized_text)
        except Exception:
            try:
                from app.rules.privacy_rules import redact_sensitive_text
                safe_section = str(redact_sensitive_text(learning_section).get("redacted_text", learning_section))
            except Exception:
                safe_section = learning_section
        
        # Check if this section already exists
        if f"## Learning Summary - {yesterday}" not in current_memory:
            updated_memory = current_memory + safe_section
            self.file_store.write_memory_md(updated_memory)

        if self.knowledge_graph and self.knowledge_graph.is_enabled():
            try:
                daily_dir = self.config.get("daily_dir", self.config["memory_dir"] / "daily")
                log_file = daily_dir / f"{yesterday}.md"
                if not log_file.exists():
                    log_file = self.config["memory_dir"] / f"{yesterday}.md"
                if log_file.exists():
                    self.knowledge_graph.ingest_memory(
                        memory_ref=f"file::{log_file.name}",
                        content=log_file.read_text(encoding='utf-8'),
                        source=f"daily_log:{log_file.name}",
                        title=f"Daily Log - {yesterday}",
                    )
            except Exception as exc:
                print(f"[MemoryLearning] knowledge graph daily ingest error: {exc}")

    def _compression_settings(self) -> Dict[str, any]:
        return self.config['settings']['memory'].get('compression', {})

    def _compression_report_dir(self) -> Path:
        report_dir = Path(self._compression_settings().get("report_dir", "memory/compression_reports"))
        if not report_dir.is_absolute():
            report_dir = self.config['workspace_dir'] / report_dir
        report_dir.mkdir(parents=True, exist_ok=True)
        return report_dir

    def _summarize_text(self, text: str, max_sentences: int = 2, max_chars: int = 220) -> str:
        raw = str(text or "").strip()
        if not raw:
            return ""

        # Prefer high-signal bullet lines first.
        lines = [ln.strip() for ln in raw.splitlines() if ln.strip()]
        bullet_lines = [ln for ln in lines if ln.startswith(("-", "*", "1.", "2.", "3.", "•"))]
        picked: List[str] = []
        for ln in bullet_lines[:max(1, max_sentences)]:
            if len(" ".join(picked + [ln])) <= max_chars:
                picked.append(ln)

        if not picked:
            sentences = re.split(r"[。！？!?；;\n]+", raw)
            for s in [x.strip() for x in sentences if x.strip()]:
                if len(picked) >= max(1, max_sentences):
                    break
                if len("。".join(picked + [s])) <= max_chars:
                    picked.append(s)

        summary = "。".join(picked).strip()
        if not summary:
            summary = raw[:max_chars]
        if len(summary) > max_chars:
            summary = summary[: max_chars - 1].rstrip() + "…"
        return summary

    def _important_markers(self) -> List[str]:
        return [
            "偏好",
            "喜欢",
            "习惯",
            "决定",
            "采用",
            "应该",
            "必须",
            "经验教训",
            "不要",
        ]

    def _is_important(self, text: str) -> bool:
        return any(marker in text for marker in self._important_markers())

    def _collect_learning_summaries(self, memory_text: str) -> List[Dict[str, str]]:
        summaries: List[Dict[str, str]] = []
        for section in parse_memory_sections(memory_text):
            if section.get("section_type") != "learning_summary":
                continue
            date_part = str(section.get("estimated_date", "") or "")
            if not date_part:
                title = str(section.get("title", ""))
                date_part = title.replace("Learning Summary - ", "").strip()
            summaries.append(
                {
                    "date": date_part,
                    "header": f"## {section.get('title', '')}",
                    "content": str(section.get("body", "")),
                }
            )
        return summaries

    def _section_age_days(self, section: Dict[str, Any], today: datetime.date) -> int:
        raw = str(section.get("estimated_date", "") or "")
        if raw:
            try:
                return (today - datetime.date.fromisoformat(raw)).days
            except Exception:
                pass
        return 0

    def _compress_memory_sections(self, memory_text: str) -> Dict[str, Any]:
        def _summarize_compat(text: str, max_sentences: int, max_chars: int) -> str:
            try:
                return self._summarize_text(text, max_sentences=max_sentences, max_chars=max_chars)
            except TypeError:
                # Backward-compatible call path for tests/monkeypatch stubs with old signature.
                return self._summarize_text(text, max_sentences=max_sentences)

        cfg = self._compression_settings()
        today = datetime.date.today()
        min_age_days = int(cfg.get("min_age_days", 7))
        sections = parse_memory_sections(memory_text)

        seen_body_hash = set()
        removed_sections: List[str] = []
        summarized_sections: List[str] = []
        merged_duplicates: List[str] = []
        kept_sections: List[str] = []
        out_chunks: List[str] = []

        for sec in sections:
            title = str(sec.get("title", "")).strip()
            body = str(sec.get("body", "")).strip()
            section_type = str(sec.get("section_type", "unknown"))
            age_days = self._section_age_days(sec, today)
            body_key = re.sub(r"\s+", " ", body.lower())

            is_duplicate = bool(body_key) and body_key in seen_body_hash
            if is_duplicate:
                merged_duplicates.append(title)
                continue
            if body_key:
                seen_body_hash.add(body_key)

            if section_type in {"verification", "smoke_test"}:
                summary = _summarize_compat(body, max_sentences=1, max_chars=140)
                out_chunks.append(f"## {title}\n[Archived test section summary] {summary}")
                summarized_sections.append(title)
                continue

            if section_type == "learning_summary" and len(body) == 0:
                removed_sections.append(title or "learning_summary_empty")
                continue

            aggressive_types = {"learning_summary", "storyline", "research", "unknown"}
            is_old = age_days > min_age_days
            is_large = len(body) > 160
            is_sync_section = ("auto memory sync" in title.lower()) and len(body) > 300 and age_days >= 1
            if (section_type in aggressive_types and is_old and is_large) or is_sync_section:
                summary = _summarize_compat(body, max_sentences=2, max_chars=220)
                out_chunks.append(f"## {title}\n{summary}")
                summarized_sections.append(title)
                continue

            if section_type == "unknown" and not body and not title:
                removed_sections.append(title or "unknown")
                continue

            kept_sections.append(title)
            out_chunks.append(f"## {title}\n{body}" if title != "Preamble" else body)

        compressed_memory = "\n\n".join(chunk.strip() for chunk in out_chunks if chunk.strip()).strip() + "\n"
        return {
            "compressed_memory": compressed_memory,
            "removed_sections": removed_sections,
            "summarized_sections": summarized_sections,
            "merged_duplicates": merged_duplicates,
            "kept_sections": kept_sections,
            "section_count": len(sections),
        }

    def preview_compression_plan(self, confirm: bool = False) -> Dict[str, any]:
        cfg = self._compression_settings()
        today = datetime.date.today()
        min_age_days = int(cfg.get("min_age_days", 7))
        keep_recent_count = int(cfg.get("keep_recent_count", 10))
        min_log_count = int(cfg.get("min_log_count", 0))

        daily_logs = list_daily_log_files(self.config["memory_dir"], self.config.get("daily_dir"))
        eligible_by_count = len(daily_logs) >= min_log_count if min_log_count else True

        current_memory = self.file_store.read_memory_md()
        sections = parse_memory_sections(current_memory)
        plan_items: List[Dict[str, any]] = []
        summary_count = sum(1 for s in sections if str(s.get("section_type")) == "learning_summary")

        for sec in sections:
            section_type = str(sec.get("section_type", "unknown"))
            title = str(sec.get("title", ""))
            body = str(sec.get("body", ""))
            age_days = self._section_age_days(sec, today)
            decision = "keep"
            if section_type in {"verification", "smoke_test"}:
                decision = "summarize"
            elif section_type == "learning_summary" and age_days > min_age_days and summary_count > keep_recent_count:
                decision = "summarize"
            elif section_type in {"research", "storyline", "unknown"} and age_days > min_age_days and len(body) > 180:
                decision = "summarize"
            plan_items.append(
                {
                    "type": section_type,
                    "date": sec.get("estimated_date"),
                    "decision": decision,
                    "title": title,
                    "content_preview": body[:120],
                    "reason": f"age_days={age_days}",
                }
            )

        eligible = bool(plan_items) and eligible_by_count and any(item.get("decision") != "keep" for item in plan_items)
        return {
            "eligible": eligible,
            "confirmed": bool(confirm),
            "items": plan_items,
            "daily_log_count": len(daily_logs),
        }

    def _apply_compression(self, plan: Dict[str, any]) -> Dict[str, any]:
        current_memory = self.file_store.read_memory_md()
        comp = self._compress_memory_sections(current_memory)
        compressed_memory = str(comp["compressed_memory"])
        try:
            from app.rules.privacy_rules import redact_sensitive_text
            compressed_memory = str(redact_sensitive_text(compressed_memory).get("redacted_text", compressed_memory))
        except Exception:
            pass
        pre_size = len(current_memory.encode('utf-8'))
        post_size = len(compressed_memory.encode('utf-8'))
        pre_line_count = len(current_memory.splitlines())
        post_line_count = len(compressed_memory.splitlines())

        self.file_store.write_memory_md(compressed_memory)

        retention_days = self.config['settings']['memory']['learning']['retention_days']
        deleted_count = self.sqlite_store.cleanup_old_entities(retention_days)
        moved = self.trigger_memory_tiering(retention_days)

        if self.knowledge_graph and self.knowledge_graph.is_enabled():
            try:
                self.knowledge_graph.decay_relation_weights()
                self.knowledge_graph.cleanup_isolated_entities()
            except Exception as exc:
                print(f"[MemoryLearning] knowledge graph weekly maintenance error: {exc}")

        report_path = self._write_compression_report(
            current_memory,
            compressed_memory,
            {
                "pre_size": pre_size,
                "post_size": post_size,
                "pre_line_count": pre_line_count,
                "post_line_count": post_line_count,
                "removed_sections": comp["removed_sections"],
                "summarized_sections": comp["summarized_sections"],
                "merged_duplicates": comp["merged_duplicates"],
                "kept": comp["kept_sections"],
                "deleted_entities": deleted_count,
                "moved_logs": moved,
                "plan": plan.get("items", []),
                "section_count": comp["section_count"],
            },
        )

        return {"report_path": str(report_path), "pre_size": pre_size, "post_size": post_size}

    def _write_compression_report(self, before: str, after: str, stats: Dict[str, any]) -> Path:
        report_dir = self._compression_report_dir()
        timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
        report_path = report_dir / f"compression_{timestamp}.json"
        quality = self._compression_quality(before, after)
        report = {
            "timestamp": datetime.datetime.now().isoformat(),
            "stats": stats,
            "quality": quality,
            "summary_before": self._summarize_text(before, max_sentences=3),
            "summary_after": self._summarize_text(after, max_sentences=3),
            "compression_ratio": round((stats["post_size"] / stats["pre_size"]) if stats["pre_size"] else 1.0, 3),
        }
        with guarded_file_write(report_path):
            atomic_write_json(report_path, report, ensure_ascii=False, indent=2)
        if quality["score"] < int(self._compression_settings().get("quality_threshold", 80)):
            print(f"[MemoryLearning] compression quality warning: {quality['score']}")
        return report_path

    def _compression_quality(self, before: str, after: str) -> Dict[str, any]:
        markers = self._important_markers()
        before_hits = {m for m in markers if m in before}
        after_hits = {m for m in markers if m in after}
        missing = sorted(before_hits - after_hits)
        score = max(0, 100 - len(missing) * 10)
        return {"score": score, "missing_markers": missing}

    def _update_archive_index(self, archive_dir: Path) -> None:
        index_path = archive_dir / "index.json"
        entries: List[Dict[str, str]] = []
        for path in sorted(archive_dir.rglob("*.md")):
            rel = str(path.relative_to(archive_dir))
            entries.append({"name": path.name, "path": rel})
        with guarded_file_write(index_path):
            atomic_write_json(index_path, {"entries": entries}, ensure_ascii=False, indent=2)

    def restore_archived_logs(self, date_prefix: Optional[str] = None, limit: Optional[int] = None) -> List[str]:
        archive_dir = self.config['memory_dir'] / 'archive'
        if not archive_dir.exists():
            return []
        restored: List[str] = []
        candidates = list(archive_dir.rglob("*.md"))
        if date_prefix:
            candidates = [p for p in candidates if p.stem.startswith(date_prefix)]
        for path in sorted(candidates):
            daily_dir = self.config.get("daily_dir", self.config["memory_dir"] / "daily")
            daily_dir.mkdir(parents=True, exist_ok=True)
            target = daily_dir / path.name
            if target.exists():
                continue
            shutil.copy2(path, target)
            restored.append(path.name)
            if limit and len(restored) >= limit:
                break
        return restored

    def list_archived_logs(self, limit: int = 50) -> List[Dict[str, str]]:
        archive_dir = self.config['memory_dir'] / 'archive'
        if not archive_dir.exists():
            return []
        items: List[Dict[str, str]] = []
        for path in sorted(archive_dir.rglob("*.md")):
            items.append({"name": path.name, "path": str(path)})
            if len(items) >= limit:
                break
        return items
    
    def weekly_compression_task(self):
        """Perform weekly memory compression."""
        if self._security_locked():
            self._log_learning_event("weekly_compression", "skipped", {"reason": "memory_security_locked"})
            return
        if not self.learning_enabled:
            return
        compression_cfg = self.config['settings']['memory'].get('compression', {})
        if not compression_cfg.get('enabled', True):
            return

        plan = self.preview_compression_plan(confirm=False)
        if not plan.get("eligible"):
            return
        if compression_cfg.get("preview_only"):
            return
        if compression_cfg.get("require_confirmation") and not plan.get("confirmed"):
            return

        result = self._apply_compression(plan)
        if compression_cfg.get("notify_on_compress", True):
            print(f"[MemoryLearning] compression completed: {result.get('report_path')}")

    def run_pending_tasks(self):
        """Run any pending scheduled tasks."""
        if self.learning_enabled:
            schedule.run_pending()
            self._log_learning_event("run_pending", "ok")
    
    def trigger_daily_learning(self, date_str: Optional[str] = None):
        """Manually trigger daily learning for a specific date."""
        if self._security_locked():
            self._log_learning_event("trigger_daily_learning", "skipped", {"reason": "memory_security_locked"})
            return
        if not self.learning_enabled:
            return
        
        if date_str is None:
            date_str = (datetime.date.today() - datetime.timedelta(days=1)).isoformat()
        if self._already_daily_done(date_str):
            self._log_learning_event("trigger_daily_learning", "skipped", {"reason": "already_done", "date": date_str})
            return
        
        # Temporarily override the analysis method to use specific date
        original_analyze = self.analyze_daily_log
        self.analyze_daily_log = lambda x: original_analyze(date_str if x == date_str else x)
        
        self.daily_learning_task()
        self._mark_daily_done(date_str)
        self._log_learning_event("trigger_daily_learning", "success", {"date": date_str})
        
        # Restore original method
        self.analyze_daily_log = original_analyze
        if self.knowledge_graph and self.knowledge_graph.is_enabled():
            try:
                self.knowledge_graph.batch_extract_pending_memories(limit=self.knowledge_graph.settings["batch_size"])
            except Exception as exc:
                print(f"[MemoryLearning] knowledge graph batch extract error: {exc}")
    
    def trigger_weekly_compression(self, confirm: bool = False, preview_only: bool = False) -> Dict[str, any]:
        """Manually trigger weekly compression or preview."""
        if self._security_locked():
            return {"status": "blocked", "reason": "memory_security_locked"}
        if not self.learning_enabled:
            return {"status": "disabled"}
        plan = self.preview_compression_plan(confirm=confirm)
        if preview_only:
            return {"status": "preview", "plan": plan}
        if self._compression_settings().get("require_confirmation") and not confirm:
            return {"status": "needs_confirmation", "plan": plan}
        if not plan.get("eligible"):
            return {"status": "skipped", "plan": plan}
        result = self._apply_compression(plan)
        self._log_learning_event("trigger_weekly_compression", "success", {"eligible": bool(plan.get("eligible"))})
        return {"status": "success", "plan": plan, "result": result}

    def _security_locked(self) -> bool:
        sec = self.config["settings"]["memory"].get("security", {})
        if not bool(sec.get("require_unlock_for_maintenance", True)):
            return False
        return self.crypto.is_enabled() and (not self.crypto.is_unlocked())

    def build_memory_tiering_plan(self, retention_days: Optional[int] = None) -> List[Dict[str, any]]:
        """Build tiering candidates only; execution is owned by maintenance."""
        retention_days = retention_days or self.config['settings']['memory']['learning']['retention_days']
        archive_dir = self.config['memory_dir'] / 'archive'
        archive_dir.mkdir(parents=True, exist_ok=True)

        today = datetime.date.today()
        plan: List[Dict[str, any]] = []
        for log_file in list_daily_log_files(self.config["memory_dir"], self.config.get("daily_dir")):
            try:
                log_date = datetime.date.fromisoformat("-".join(log_file.stem.split("-")[0:3]))
            except ValueError:
                continue
            if (today - log_date).days <= retention_days:
                continue

            month_dir = archive_dir / log_date.strftime("%Y-%m")
            target = month_dir / log_file.name
            plan.append({
                "name": log_file.name,
                "source_path": str(log_file),
                "target_path": str(target),
                "reason": "learning_retention_tiering",
            })
        return plan

    def trigger_memory_tiering(self, retention_days: Optional[int] = None) -> List[str]:
        """Compatibility path: by default only prepares plan; maintenance should execute."""
        plan = self.build_memory_tiering_plan(retention_days=retention_days)
        allow_execute = bool(self.config['settings']['memory']['learning'].get('allow_learning_execute_tiering', False))
        if not allow_execute:
            self._log_learning_event('trigger_memory_tiering', 'planned_only', {'plan_count': len(plan)})
            return []

        archive_dir = self.config['memory_dir'] / 'archive'
        archive_dir.mkdir(parents=True, exist_ok=True)
        manifest_file = archive_dir / 'manifest.json'
        manifest = {'moved_files': []}
        if manifest_file.exists():
            try:
                with open(manifest_file, 'r', encoding='utf-8') as handle:
                    manifest = json.load(handle)
            except Exception:
                manifest = {'moved_files': []}

        moved: List[str] = []
        for item in plan:
            src = Path(item['source_path'])
            dst = Path(item['target_path'])
            dst.parent.mkdir(parents=True, exist_ok=True)
            if not src.exists() or dst.exists():
                continue
            shutil.move(str(src), str(dst))
            moved.append(src.name)
            manifest['moved_files'].append({
                'name': src.name,
                'archive_path': str(dst),
                'archived_at': datetime.datetime.now().isoformat(),
                'owner': 'learning_compat',
            })

        with guarded_file_write(manifest_file):
            with open(manifest_file, 'w', encoding='utf-8') as handle:
                json.dump(manifest, handle, indent=2, ensure_ascii=False)
        self._update_archive_index(archive_dir)
        self._log_learning_event('trigger_memory_tiering', 'compat_execute', {'moved_count': len(moved)})
        return moved
