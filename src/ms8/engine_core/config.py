"""
Memory skill configuration.
"""

from __future__ import annotations

import copy
import os
from pathlib import Path
from typing import Any

import yaml

from .priority_engine import ConfigPriorityEngine

DEFAULT_CONFIG: dict[str, Any] = {
    "memory": {
        "short_term": {
            "max_size": 200,
            "persist_enabled": True,
        },
        "long_term": {
            "type": "sqlite",
            "path": "memory/memory.db",
        },
        "keyword": {
            "type": "whoosh",
            "index_dir": "memory/whoosh_index",
        },
        "git": {
            "enabled": False,
            "repo_path": ".",
            "auto_commit": False,
        },
        "learning": {
            "enabled": True,
            "daily_summary_time": "03:00",
            "compression_day": "Sunday",
            "retention_days": 30,
            "task_log_file": "memory/learning_task_log.jsonl",
            "scheduler_poll_seconds": 60,
            "allow_learning_execute_tiering": False,
            "auto_review_enabled": True,
            "auto_review_interval_hours": 4,
            "auto_review_mode": "triage_default",
            "auto_review_batch_limit": 30,
            "auto_review_accept_conf_min": 0.62,
            "auto_review_reject_conf_max": 0.20,
            "auto_review_reject_conf_max_backlog": 0.35,
            "auto_review_drain_reject_conf_max": 0.50,
            "auto_review_per_category_limit": 6,
            "context_opt_enabled": True,
            "context_opt_interval_hours": 6,
            "context_opt_window": 300,
        },
        "compression": {
            "enabled": True,
            "min_age_days": 7,
            "keep_recent_count": 10,
            "archive_threshold": 30,
            "quality_threshold": 80,
            "notify_on_compress": True,
            "min_log_count": 20,
            "require_confirmation": False,
            "preview_only": False,
            "report_dir": "memory/compression_reports",
        },
        "synthetic_memory": {
            "enabled": True,
            "max_candidates": 20,
            "min_relation_strength": 0.6,
            "use_llm": False,
            "auto_accept_enabled": True,
            "auto_accept_limit": 8,
            "auto_confirm_high_confidence": 0.90,
            "auto_confirm_medium_confidence": 0.75,
            "auto_confirm_high_risk_categories": [
                "security",
                "permission",
                "decision",
            ],
            "auto_confirm_high_risk_keywords": [
                "安全",
                "风险",
                "漏洞",
                "权限",
                "授权",
                "密钥",
                "token",
                "api key",
                "credential",
                "password",
                "决定",
                "决策",
                "优先级",
                "取舍",
                "should",
                "must",
            ],
            "auto_approval_rollback_keep_hours": 72,
            "accept_threshold": 0.82,
            "review_threshold": 0.68,
            "special_reasoning_enabled": True,
            "force_two_hop_review": True,
            "two_hop_enabled": True,
            "rebalance_on_start": True,
            "rebalance_max_auto_accept": 40,
            "rebalance_writeback": False,
            "review_queue_target": 10,
            "promotion_min_hits": 2,
            "max_rebuttal_before_reject": 2,
            "auto_generate_on_interaction": True,
            "auto_generate_interval_hours": 6,
            "auto_generate_limit": 5,
            "allowed_relations": [
                "uses",
                "depends_on",
                "part_of",
                "belongs_to",
                "creates",
                "evolves_from",
            ],
            "quality_thresholds": {
                "consistency": 0.8,
                "confidence": 0.7,
                "novelty": 0.5,
                "usefulness": 0.6,
            },
            "auto_accept_threshold": 0.9,
            "gap_detection": {
                "min_importance": 0.6,
                "max_relations": 1,
            },
        },
        "meta_cognition": {
            "enabled": True,
            "mode": "monitor_only",
            "report_period": "daily",
            "schedule_time": "04:00",
            "monitor_interval_hours": 24,
            "max_conversations": 120,
            "window_size": 50,
            "time_decay": 0.85,
            "outlier_zscore": 2.5,
            "llm_enabled": True,
            "llm_fallback_enabled": True,
            "report_dir": "memory/meta_reports",
            "backup_keep": 3,
            "task_log_file": "memory/meta_task_log.jsonl",
            "metrics_weights": {
                "response_quality": 0.3,
                "response_speed": 0.2,
                "user_satisfaction": 0.2,
                "task_completion": 0.2,
                "learning_efficiency": 0.1,
            },
        },
        "subagents": {
            "enabled": True,
            "max_concurrent": 3,
            "max_background": 2,
            "task_timeout_seconds": 120,
            "max_retries": 2,
            "loop_window_minutes": 10,
            "max_similar_tasks": 3,
            "log_dir": "memory/subagent_logs",
        },
        "skills_system": {
            # Stable mode default: disable online skill ecosystem features
            # (GitHub discovery / marketplace / auto install).
            "github_enabled": False,
            "marketplace_enabled": False,
            "auto_install_enabled": False,
            "cache_ttl_hours": 6,
            "allow_overwrite": False,
            "auto_suffix_on_conflict": True,
            "sync_on_boot": False,
        },
        "knowledge_graph": {
            "enabled": True,
            "db_path": "memory/knowledge_graph.db",
            "extraction_mode": "hybrid",
            "extraction_model": "llama3.2:3b",
            "auto_extract": True,
            "batch_size": 10,
            "daily_decay_rate": 0.05,
            "min_relation_strength": 0.1,
            "duplicate_merge_increment": 0.1,
            "access_increment": 0.05,
            "isolated_entity_retention_days": 30,
            "default_query_depth": 2,
            "max_query_depth": 5,
            "default_return_limit": 10,
            "context_injection_enabled": True,
            "context_injection_limit": 5,
        },
        "expression_router": {
            "enabled": True,
            "profile": {
                "decay": 0.95,
            },
            "thresholds": {
                "strong_min_weight": 2.0,
                "light_min_weight": 1.0,
                "execute_only_normal_max_weight": 0.5,
                "confidence_divisor": 3.0,
            },
            "cooldown": {
                "enabled": True,
                "reset_rounds_without_strong": 3,
                "max_continuous_strong": 2,
                "confidence_penalty": 0.2,
                "confidence_floor": 0.5,
            },
            "negation": {
                "window_chars": 8,
                "words": [
                    "不是",
                    "并非",
                    "没有",
                    "别",
                    "不用",
                    "无需",
                    "不需要",
                    "不要",
                    "无关",
                    "不算",
                ],
                "sentence_split_regex": "[，。！？；,.!?;]+",
            },
            "signals": {
                "explore": {
                    "weight": 1.0,
                    "keywords": ["本质", "机制", "原理", "为什么", "怎么设计", "更高维", "思路", "方向"],
                },
                "execute": {
                    "weight": 0.5,
                    "keywords": ["直接给", "执行版", "任务书", "步骤", "命令", "落地", "实现", "codex执行", "代码"],
                },
                "stuck": {
                    "weight": 1.5,
                    "keywords": ["卡住", "不对", "乱", "矛盾", "推不动", "哪里有问题", "隐患"],
                },
                "risk": {
                    "weight": 2.0,
                    "keywords": ["风险", "漏洞", "攻击", "兜底", "安全", "失控", "边界"],
                },
                "decision": {
                    "weight": 1.0,
                    "keywords": ["选哪个", "优先级", "取舍", "哪个更好", "推荐", "二选一"],
                },
            },
            "force_normal": {
                "keywords": ["直接", "只给", "仅需", "不要解释", "别解释", "命令", "代码块", "执行版", "任务书"],
            },
        },
        "llm": {
            "enabled": True,
            "timeout_seconds": 25,
            "failover_enabled": True,
            "failover_max_errors": 3,
            "failover_cooldown_seconds": 120,
            "provider_order_chat": ["ollama", "openai", "openrouter"],
            "provider_order_embedding": ["ollama", "openai", "openrouter"],
            "task_provider_order": {
                "kg_extract": ["ollama", "openrouter", "openai"],
                "classification": ["ollama", "openai"],
                "reasoning": ["openai", "openrouter", "ollama"],
            },
            "models": {
                "primary_model": "gemma3:1b",
                "complex_model": "llama3.2:3b",
                "reasoning_model": "llama3.2:3b",
                "embedding_model": "nomic-embed-text:latest",
            },
            "openai": {
                "enabled": True,
                "base_url": "https://api.openai.com/v1",
                "api_key": "",
                "chat_model": "gpt-4.1-mini",
                "embedding_model": "text-embedding-3-small",
            },
            "openrouter": {
                "enabled": True,
                "base_url": "https://openrouter.ai/api/v1",
                "api_key": "",
                "chat_model": "openai/gpt-4.1-mini",
                "embedding_model": "openai/text-embedding-3-small",
            },
        },
        "auto_memory": {
            "enabled": True,
            "min_confidence": 0.55,
            "max_per_interaction": 3,
            "use_llm": False,
            "review_confidence_threshold": 0.62,
            "validate": True,
            "allow_categories": [
                "work_report",
                "plan",
                "configuration",
                "technical_doc",
                "test_result",
                "preference",
                "decision",
                "feedback",
                "pattern",
                "lesson",
            ],
            "cooldown_minutes": 30,
            "log_file": "memory/auto_memory_log.json",
            "thresholds": {
                "global_min_confidence": 0.55,
                "category_thresholds": {
                    "work_report": 0.62,
                    "plan": 0.58,
                    "decision": 0.65,
                    "configuration": 0.68,
                    "technical_doc": 0.64,
                    "test_result": 0.60,
                    "preference": 0.57,
                },
                "low_confidence_review_min": 0.60,
                "cjk_ratio_threshold": 0.30,
                "hybrid_context_dependency_min_confidence": 0.80,
                "feedback_low_ratio": 0.40,
                "feedback_high_ratio": 0.75,
                "feedback_raise_step": 0.03,
                "feedback_drop_step": 0.02,
                "feedback_floor_threshold": 0.45,
                "index_hot_min_confidence": 0.65,
            },
            "quality_gate": {
                "min_len_cjk": 4,
                "min_len_non_cjk": 8,
                "max_len": 4000,
                "noisy_ratio_max": 0.35,
            },
            "dedupe": {
                "hard_block_window_minutes": 5,
                "hard_block_repeat_threshold": 3,
                "similar_window_minutes": 60,
                "similar_soft_threshold": 0.90,
                "similar_hard_threshold": 0.97,
                "category_repeat_thresholds": {
                    "work_report": 2,
                    "plan": 4,
                    "decision": 5,
                    "configuration": 5,
                    "technical_doc": 3,
                    "test_result": 3,
                    "preference": 6,
                },
            },
            "session_ingestion": {
                "enabled": True,
                "sessions_dir": "~/.openclaw/agents/main/sessions",
                "sessions_dirs_glob": "~/.openclaw/agents/*/sessions",
                "state_file": "memory/openclaw_session_ingest_state.json",
                "allowed_roles": ["user"],
                "scan_limit_files": 40,
                "max_messages_per_run": 120,
                "min_message_chars": 8,
                "max_message_chars": 1600,
                "sync_interval_seconds": 45,
                "process_timeout_seconds": 8,
                "test_keywords": [
                    "verification interaction",
                    "working_memory_check",
                    "监控验证",
                ],
            },
        },
        "working_memory": {
            "enabled": True,
            "persistence_file": "memory/working_memory.jsonl",
            "usage_log_file": "memory/memory_usage_log.jsonl",
            "context_snapshot_log_file": "memory/context_snapshots.jsonl",
            "test_filter": {
                "enabled": True,
                "keywords": [
                    "verification interaction",
                    "working_memory_check",
                    "监控验证",
                    "test_only",
                ],
            },
            "max_restore": 120,
            "topic_restore_limit": 60,
            "injection_top_k": 5,
            "max_injection_chars": 1800,
            "force_injection_enabled": True,
            "force_injection_min_items": 2,
            "dynamic_injection_budget": {
                "enabled": True,
                "simple_top_k": 3,
                "complex_top_k": 7,
                "simple_max_chars": 950,
                "complex_max_chars": 2100,
                "low_trust_ratio_cap": 0.30,
                "topic_continue_min_hits": 2,
                "topic_continue_min_match": 0.22,
                "topic_continue_min_consistency": 0.34,
                "topic_hard_switch_max_hits": 0,
                "topic_hard_switch_max_coverage": 0.08,
                "topic_hard_switch_max_consistency": 0.12,
                "topic_consistency_window": 6,
                "hard_switch_low_trust_cap": 1,
                "context_signal_assist_cap": 0.18,
                "context_signal_weight_by_query_type": {
                    "default": 0.06,
                    "direct": 0.05,
                    "analysis": 0.14,
                    "multi_intent": 0.10,
                },
                "hard_switch_cues": [
                    "换个",
                    "新话题",
                    "另一个问题",
                    "不相关",
                    "切换",
                    "by the way",
                    "anyway",
                ],
            },
            "high_importance_keywords": [
                "决定",
                "计划",
                "必须",
                "偏好",
                "配置",
                "deadline",
                "todo",
            ],
            "long_term_promotion_min_confidence": 0.66,
            "long_term_promotion_categories": [
                "decision",
                "configuration",
                "preference",
                "plan",
                "technical_doc",
            ],
            "long_term_promotion_allow_pending_review": False,
            "ranking_weights": {
                "score": 0.45,
                "recency": 0.25,
                "confidence": 0.20,
                "overlap": 0.10,
                "topic_overlap": 0.50,
                "topic_importance": 0.50,
            },
            "importance_estimation": {
                "base_score": 0.45,
                "keyword_hit_bonus": 0.08,
                "long_text_threshold": 180,
                "long_text_bonus": 0.08,
                "punctuation_bonus": 0.05,
            },
            "recency_scoring": {
                "missing_score": 0.40,
                "invalid_score": 0.40,
                "within_1d_score": 1.00,
                "within_7d_score": 0.80,
                "within_30d_score": 0.60,
                "within_90d_score": 0.45,
                "older_score": 0.25,
            },
        },
        "retrieval_fusion": {
            "position_decay_factor": 0.01,
            "graph_score_multiplier": 0.55,
            "incremental_score_multiplier": 0.9,
            "rerank_fusion_weight": 0.82,
            "rerank_trust_weight": 0.18,
            "max_per_source": 3,
            "max_per_source_governance": 2,
            "memory_md_fallback_base_score": 0.45,
            "query_intent_source_prior": {
                "enabled": True,
                "max_boost": 0.18,
                "min_penalty": -0.22,
                "governance_keywords": [
                    "threshold",
                    "审批",
                    "审查",
                    "人工确认",
                    "maintenance",
                    "policy",
                    "review",
                    "backlog",
                    "queue",
                    "config",
                    "配置",
                    "治理",
                    "监控",
                    "告警",
                    "context snapshot",
                ],
                "governance_title_boost_keywords": [
                    "maintenance",
                    "policy",
                    "threshold",
                    "review",
                    "config",
                    "治理",
                    "阈值",
                    "审批",
                ],
                "narrative_penalty_keywords": [
                    "故事",
                    "剧情",
                    "黑帮",
                    "科幻",
                    "世界观",
                    "episode",
                    "chapter",
                ],
                "prefer_source_prefixes": ["mcp:", "openclaw_session:"],
                "memory_md_bonus": 0.06,
                "recent_days_bonus": 0.04,
                "stale_days_penalty": -0.05,
                "stale_days_threshold": 45,
                "recent_days_threshold": 14,
            },
        },
        "governance": {
            "trust_scoring": {
                "base_score": 0.5,
                "memory_md_bonus": 0.35,
                "daily_log_bonus": 0.2,
                "default_source_bonus": 0.1,
                "stale_penalty": 0.2,
            },
            "semantic_overlap_threshold": 0.55,
        },
        "self_improvement_scoring": {
            "weights": {
                "consistency": 0.28,
                "clarity": 0.18,
                "relevance": 0.22,
                "specificity": 0.17,
                "novelty": 0.15,
            },
            "validated_min_score": 0.62,
            "testing_min_score": 0.42,
        },
        "meta_cognition_thresholds": {
            "strength_min_score": 0.8,
            "weakness_max_score": 0.6,
            "trend_change_significant": 0.05,
            "rule_based_quality_default": 0.6,
            "rule_based_satisfaction_default": 0.6,
            "estimated_improvement_fallback": 0.2,
        },
        "config_audit": {
            "enabled": True,
            "report_file": "memory/config_audit_report.json",
        },
        "maintenance": {
            "enabled": True,
            "backup_enabled": True,
            "backup_interval_hours": 24,
            "backup_dir": "memory/backups",
            "backup_keep": 7,
            "backup_retention_days": {
                "daily_full_keep_days": 7,
                "weekly_sample_keep_days": 30,
                "monthly_sample_keep_days": 90,
            },
            "cleanup_enabled": True,
            "cleanup_days": 90,
            "sync_memory_md": True,
            "restore_drill_enabled": True,
            "restore_drill_interval_days": 7,
            "restore_drill_keep_reports": 4,
            "cleanup_legacy_root_backups": True,
            "legacy_backup_keep": 1,
            "cleanup_snapshots_keep": 2,
            "state_file": "memory/maintenance_state.json",
            "sync_audit_file": "memory/memory_sync_audit.jsonl",
        },
        "maintenance_policy": {
            "enabled": True,
            "cooldown_hours": {
                "trigger_weekly_compression": 24,
                "purge_test_memory_data": 24,
                "backfill_auto_memory_record_ids": 24,
                "shadow_replay_spool": 1,
                "shadow_startup_self_heal": 12,
                "shadow_archive_spool": 12,
                "shadow_sync_verified_backup": 24,
                "repair_semantic_cache": 12,
                "rebalance_feedback_distribution": 24,
                "trigger_batch_review": 6,
                "trigger_batch_extract_kg": 2,
                "trigger_memory_tiering": 24,
                "generate_threshold_suggestions": 24,
                "auto_navigate_threshold_suggestions": 6,
                "self_check_l1": 1,
                "self_check_l2l3": 6,
                "self_check_l4": 24,
            },
            "semantic_repair_limit": 30,
            "feedback_rebalance_window": 200,
            "require_threshold_suggestion_approval": True,
            "threshold_auto_navigate_enabled": False,
            "threshold_auto_navigate_batch_limit": 1,
            "threshold_auto_navigate_min_recent_count": 80,
            "threshold_auto_navigate_max_suggestions_per_item": 2,
            "threshold_auto_navigate_max_abs_delta": 0.15,
            "threshold_auto_navigate_max_simple_top_k_delta": 1,
            "threshold_auto_navigate_pending_min_age_minutes": 5,
            "threshold_auto_navigate_auto_reject_failed_guardrail": False,
            "threshold_suggestion_pending_max": 20,
            "auto_review_mode": "triage_default",
            "auto_review_batch_limit": 30,
            "auto_review_accept_conf_min": 0.62,
            "auto_review_reject_conf_max": 0.20,
            "auto_review_reject_conf_max_backlog": 0.35,
            "auto_review_drain_reject_conf_max": 0.50,
            "auto_review_per_category_limit": 6,
            "kg_batch_extract_limit": 20,
            "thresholds": {
                "memory_md_lines_threshold": 500,
                "test_pollution_ratio_threshold": 0.15,
                "semantic_dense_missing_threshold": 20,
                "feedback_dominant_ratio_threshold": 0.9,
                "review_backlog_pending_threshold": 80,
                "review_backlog_pending_soft_threshold": 50,
                "review_backlog_stale_hours_threshold": 24,
                "feedback_recent_min_for_suggestions": 120,
                "threshold_suggestion_pending_min_age_minutes": 5,
                "kg_batch_extract_source_lag_minutes_threshold": 5,
                "tiering_candidate_threshold": 1,
                "tiering_retention_days": 7,
                "shadow_spool_pending_threshold": 1,
                "shadow_spool_archive_threshold": 40,
                "shadow_backup_interval_hours": 24,
            },
            "self_check": {
                "enabled": True,
                "l1_interval_minutes": 30,
                "l2l3_interval_hours": 24,
                "l4_interval_hours": 168,
                "self_repair_enabled": True,
                "self_repair_on_warn": False,
                "allow_r3_auto_apply": False,
                "self_repair_max_per_check_24h": 3,
                "repair_effectiveness_fail_success_rate": 0.5,
                "repair_effectiveness_warn_success_rate": 0.75,
                "repair_effectiveness_warn_rollback_rate": 0.3,
                "dynamic_repair_chain": {
                    "enabled": True,
                    "rules": [],
                },
                "repair_window": {
                    "enabled": True,
                    "recent_write_seconds": 300,
                    "session_active_seconds": 120,
                    "mcp_active_connection_max": 0,
                    "enforce_manual": False,
                },
                "health_card_hash_min_mb": 1,
                "health_card_hash_max_mb": 10,
                "health_card_hash_max_mb_db": 10,
                "health_card_hash_max_mb_markdown": 10,
                "health_card_hash_max_mb_json": 10,
            },
        },
        "self_check": {
            "enabled": True,
            "l1_interval_minutes": 30,
            "l2l3_interval_hours": 24,
            "l4_interval_hours": 168,
            "self_repair_enabled": True,
            "self_repair_on_warn": False,
            "allow_r3_auto_apply": False,
            "self_repair_max_per_check_24h": 3,
            "repair_effectiveness_fail_success_rate": 0.5,
            "repair_effectiveness_warn_success_rate": 0.75,
            "repair_effectiveness_warn_rollback_rate": 0.3,
            "dynamic_repair_chain": {
                "enabled": True,
                "rules": [],
            },
            "repair_window": {
                "enabled": True,
                "recent_write_seconds": 300,
                "session_active_seconds": 120,
                "mcp_active_connection_max": 0,
                "enforce_manual": False,
            },
            "health_card_hash_min_mb": 1,
            "health_card_hash_max_mb": 10,
            "health_card_hash_max_mb_db": 10,
            "health_card_hash_max_mb_markdown": 10,
            "health_card_hash_max_mb_json": 10,
            "heartbeat_path": "/tmp/ocma_self_check_heartbeat",
            "canary_path": "canary_probe.tmp",
            "disk_warn_gb": 5,
            "disk_crit_gb": 1,
            "history_keep_days": 30,
            "history_keep_max": 500,
            "alert_cooldown_hours": 6,
            "alert_max_per_day": 3,
            "healthchecks_enabled": False,
            "healthchecks_url": "",
            "healthchecks_fail_suffix": "/fail",
        },
        "security": {
            "enabled": False,
            "session_cache_enabled": True,
            "require_unlock_for_maintenance": True,
            "security_dir": "memory/security",
            "state_file": "memory/security/security_state.json",
            "key_material_file": "memory/security/key_material.json",
            "recovery_material_file": "memory/security/recovery_material.json",
            "use_keychain": False,
            "keychain_service": "ms8-memory",
            "keychain_account": "master-key",
            "encrypted_targets": [
                "MEMORY.md",
                "memory/memory_blocks.json",
                "memory/auto_memory_records.jsonl",
                "memory/working_memory.jsonl",
                "memory/auto_memory_index.json",
            ],
            "shadow": {
                "enabled": True,
                "shadow_dir": "memory/security/shadow_data",
                "payload_threshold_chars": 500,
                "checkpoint_interval": 100,
                "snapshot_interval": 100,
                "snapshot_keep": 3,
                "auto_self_heal_on_startup": True,
                "auto_seal_on_write_error_level": "soft",
                "soft_to_hard_error_threshold": 3,
                "spool_encryption_enabled": True,
                "spool_archive_hot_days": 7,
                "spool_archive_warm_days": 30,
                "spool_archive_cold_days": 180,
                "immutable_enabled": True,
                "stack_guard_enabled": True,
                # Keep shadow backup colocated with runtime by default to avoid
                # cross-home permission issues in sandboxed environments.
                "backup_dir": "memory/security/shadow_backup",
            },
        },
        "monitoring": {
            "enabled": True,
            "feedback_recent_window": 100,
            "slo": {
                "capture_rate_min": 0.85,
                "capture_rate_min_samples": 30,
                "injection_rate_min": 0.80,
                "injection_rate_min_samples": 10,
                "duplicate_drop_rate_max": 0.20,
                "backup_success_rate_min": 1.0,
                "restore_drill_success_rate_min": 1.0,
                "shadow_replay_success_rate_min": 0.80,
                "shadow_spool_pending_max": 50,
                "shadow_checkpoint_ok_rate_min": 0.95,
            },
            "alerts": {
                "enabled": True,
                "no_new_memory_hours": 6,
                "alert_cooldown_hours": 2,
                "alert_log_file": "memory/alerts.jsonl",
                "review_backlog_pending_threshold": 120,
                "compression_stale_hours": 48,
                "shadow_replay_failed_threshold": 1,
                "shadow_replay_remaining_threshold": 20,
                "shadow_checkpoint_low_threshold": 0.90,
                "self_check_stale_hours": 2,
            },
            "daily_report_file": "memory/health_report_latest.json",
            "daily_report_markdown": "memory/health_report_latest.md",
        },
        "advanced_insight": {
            "enabled": True,
            "context_understanding_enabled": True,
            "pattern_recognition_enabled": True,
            "analyze_interval_interactions": 4,
            "min_turns_before_analyze": 3,
            "max_history_items": 40,
        },
        "knowledge_control": {
            "feedback_log_file": "memory/knowledge_feedback.jsonl",
            "bridge_app_feedback": {
                "enabled": True,
                "store_path": "memory/auto_memory_feedback.jsonl",
            },
            "feedback_rebalance": {
                "enabled": True,
                "recent_window": 120,
                "output_file": "memory/knowledge_feedback_rebalanced.jsonl",
                "enabled_distribution_shaping": True,
                "hard_top_ratio": 0.10,
                "hypothesis_bottom_ratio": 0.15,
                "min_hard_count": 1,
                "min_hypothesis_count": 1,
                "hypothesis_max_score": 0.70,
                "effective_thresholds": {
                    "hard_trust_min": 0.78,
                    "soft_trust_min": 0.55,
                    "hypothesis_min": 0.28,
                },
            },
            "candidate_thresholds": {
                "core_min": 0.92,
                "graph_min": 0.82,
                "short_term_min": 0.68,
            },
            "trust_by_tier": {
                "core": "hard_trust",
                "graph": "soft_trust",
                "short_term": "hypothesis",
                "observation": "hypothesis",
                "rejected": "isolated",
            },
            "usage_permission_by_trust": {
                "hard_trust": {"recall": True, "inject": "primary", "speak": "primary"},
                "soft_trust": {"recall": True, "inject": "auxiliary", "speak": "support"},
                "hypothesis": {"recall": True, "inject": "weak", "speak": "hint"},
                "isolated": {"recall": False, "inject": "none", "speak": "deny"},
            },
            "retrieval_trust_thresholds": {
                "hard_trust_min": 0.78,
                "soft_trust_min": 0.55,
                "hypothesis_min": 0.28,
            },
            "retrieval_calibration": {
                "hybrid_graph_bonus": -0.04,
                "hybrid_bonus": 0.03,
                "lexical_bonus": 0.00,
                "stale_penalty": -0.08,
                "duplicate_mentions_trigger": 3,
                "duplicate_penalty": -0.06,
                "fusion_hard_boost_min": 1.15,
                "fusion_hard_boost": 0.08,
            },
            "retrieval_mix_balancer": {
                "enabled": True,
                "hard_top_ratio": 0.20,
                "hypothesis_bottom_ratio": 0.20,
                "min_hard_count": 1,
                "min_hypothesis_count": 1,
                "hypothesis_max_trust_score": 0.75,
            },
        },
        "knowledge_graph_quality": {
            "relation_low_confidence": 0.45,
            "two_hop_low_confidence": 0.6,
            "relation_reject_confidence": 0.30,
            "relation_long_unused_days": 60,
            "relation_long_unused_max_access": 1,
            "entity_delete_warning_importance": 0.8,
            "entity_search_fuzzy_min_score": 0.62,
            "entity_alias_fuzzy_match_min_score": 0.9,
            "entity_candidate_min_quality": 0.45,
            "entity_weak_quality_threshold": 0.52,
            "entity_quality_url_bonus": 0.90,
            "entity_quality_tech_hint_bonus": 0.55,
            "entity_quality_suffix_bonus": 0.45,
            "entity_quality_camelcase_bonus": 0.25,
            "entity_quality_digit_bonus": 0.15,
            "entity_quality_min_len_bonus": 0.10,
            "entity_quality_long_len_penalty": 0.15,
            "entity_description_empty_score": -1.0,
            "entity_description_name_match_bonus": 0.45,
            "entity_description_tech_hint_bonus": 0.20,
            "entity_description_action_bonus": 0.22,
            "entity_description_reasonable_len_bonus": 0.12,
            "entity_description_noise_penalty": 0.28,
            "entity_description_prefix_penalty": 0.12,
            "relation_extract_entity_min_quality": 0.55,
            "relation_extract_entity_min_quality_relaxed": 0.35,
            "relation_extract_high_pair_quality": 0.86,
            "relation_extract_min_sentence_len": 4,
            "relation_extract_max_object_candidates": 3,
            "relation_extract_pair_fallback_confidence": 0.20,
            "relation_extract_pair_max_sentence_len": 60,
            "relation_extract_confidence_depends_on": 0.78,
            "relation_extract_confidence_part_of": 0.72,
            "relation_extract_confidence_uses": 0.76,
            "relation_extract_confidence_similar_to": 0.68,
            "relation_extract_confidence_replaces": 0.70,
            "relation_extract_confidence_creates": 0.75,
            "relation_extract_confidence_belongs_to": 0.74,
            "relation_extract_confidence_causes": 0.74,
            "relation_extract_confidence_contradicts": 0.70,
            "relation_extract_confidence_evolves_from": 0.72,
            "relation_extract_confidence_learns_from": 0.75,
            "infer_base_confidence": 0.42,
            "infer_depends_on_confidence": 0.58,
            "infer_uses_confidence": 0.54,
            "infer_belongs_to_confidence": 0.57,
            "infer_related_price_confidence": 0.50,
            "infer_related_generic_confidence": 0.40,
            "infer_anchor_max_entities": 4,
            "infer_min_frequency_without_sentence": 2,
            "importance_relation_factor": 0.07,
            "importance_access_factor": 0.03,
            "importance_anchor_factor": 0.03,
            "importance_recency_max_bonus": 0.12,
            "importance_recency_daily_decay": 0.002,
            "importance_base_value": 0.28,
            "importance_quality_factor": 0.28,
            "entity_importance_min_value": 0.45,
            "entity_importance_max_value": 0.90,
            "entity_importance_base_value": 0.35,
            "entity_importance_quality_factor": 0.35,
            "entity_importance_count_cap": 0.20,
            "entity_importance_count_factor": 0.07,
            "prune_high_quality_threshold": 0.78,
            "prune_weak_isolated_quality_keep": 0.72,
            "prune_model_like_quality_keep": 0.68,
            "prune_concept_quality_min": 0.90,
            "prune_importance_keep": 0.82,
            "prune_description_keep": 0.10,
            "prune_access_quality_keep": 0.62,
            "prune_related_type_quality_keep": 0.80,
            "prune_description_score_keep": 0.35,
            "entity_health_duplicate_ratio": 0.92,
            "ingest_strong_quality_threshold": 0.88,
            "ingest_min_frequency": 2,
            "ingest_typed_quality_threshold": 0.68,
            "search_snippet_max_query_tokens": 5,
            "search_snippet_row_fetch_multiplier": 12,
            "search_snippet_entity_min_quality": 0.40,
            "search_snippet_token_len_divisor": 3.0,
            "search_snippet_token_score_cap": 1.0,
            "search_snippet_importance_factor": 0.25,
            "search_snippet_access_cap": 0.30,
            "search_snippet_access_factor": 0.02,
            "search_snippet_quality_factor": 0.50,
            "search_snippet_markup_penalty": 0.60,
            "related_primary_match_limit": 5,
            "related_direct_fetch_multiplier": 4,
            "related_direct_importance_factor": 0.72,
            "related_direct_access_cap": 0.28,
            "related_direct_access_factor": 0.02,
            "related_secondary_entity_limit": 6,
            "related_indirect_base_score": 0.32,
            "related_indirect_importance_factor": 0.45,
            "related_indirect_relatedness_cap": 0.23,
            "context_primary_match_limit": 4,
        },
        "safety": {
            "block_remote_upload": True,
            "allow_remote_skills": False,
            "allow_public_upload": False,
        },
        "blocks": {
            "human": "Name: User. Status: Human.",
            "persona": "I am a helpful AI assistant with persistent memory.",
        },
    },
    "config_layers": {
        "protected_paths": [
            "memory.safety.block_remote_upload",
            "memory.safety.allow_remote_skills",
            "memory.safety.allow_public_upload",
        ],
    },
}


def _default_workspace_dir() -> Path:
    env_value = (
        os.environ.get("OPENCLAW_MEMORY_WORKSPACE")
        or os.environ.get("OPENCLAW_WORKSPACE")
        or os.environ.get("MS8_HOME")
    )
    if env_value:
        return Path(env_value).expanduser()
    modern = Path.home() / ".ms8"
    legacy = Path.home() / ".ms8_runtime"

    def _score(root: Path) -> int:
        score = 0
        markers = [
            (root / "memory" / "auto_memory_records.jsonl", 4),
            (root / "memory" / "knowledge_graph.db", 3),
            (root / "data" / "memories.jsonl", 2),
            (root / "memory" / "auto_memory_index.json", 1),
        ]
        for path, weight in markers:
            if path.exists():
                score += weight
        return score

    modern_score = _score(modern)
    legacy_score = _score(legacy)
    if modern_score > 0 or legacy_score > 0:
        return modern if modern_score >= legacy_score else legacy
    return modern


def _deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    result = copy.deepcopy(base)
    for key, value in override.items():
        if key in result and isinstance(result[key], dict) and isinstance(value, dict):
            result[key] = _deep_merge(result[key], value)
        else:
            result[key] = value
    return result


def load_yaml_config(workspace_dir: Path) -> dict[str, Any]:
    """Load configuration from config.yaml if it exists."""
    config_file = workspace_dir / "config.yaml"
    if not config_file.exists():
        return {}
    try:
        with open(config_file, encoding="utf-8") as handle:
            return yaml.safe_load(handle) or {}
    except (yaml.YAMLError, OSError) as exc:
        print(f"Error loading config.yaml: {exc}")
        return {}


def _resolve_path(workspace_dir: Path, raw_path: str) -> Path:
    path = Path(raw_path).expanduser()
    if path.is_absolute():
        return path
    return workspace_dir / path


def _prefer_migrated_path(workspace_dir: Path, resolved: Path, kind: str) -> Path:
    """
    Backward-compatible runtime layout migration:
    keep old configured paths, but if old path is missing and the new
    categorized location exists, prefer the categorized file/dir.
    """
    if resolved.exists():
        return resolved

    memory_root = workspace_dir / "memory"
    candidates: list[Path] = []
    if kind == "memory_db":
        candidates = [memory_root / "db" / "memory.db"]
    elif kind == "kg_db":
        candidates = [memory_root / "db" / "knowledge_graph.db"]
    elif kind == "whoosh_index":
        candidates = [memory_root / "index" / "whoosh_index"]
    elif kind == "skill_index":
        candidates = [memory_root / "index" / "skill_index"]

    for candidate in candidates:
        if candidate.exists():
            return candidate
    return resolved


def get_config() -> dict[str, Any]:
    """Load configuration with defaults and workspace overrides."""
    workspace_dir = _default_workspace_dir()
    workspace_dir.mkdir(parents=True, exist_ok=True)

    skill_root = Path(__file__).resolve().parents[2]
    engine = ConfigPriorityEngine(workspace_dir, skill_root, DEFAULT_CONFIG)
    config_dict, config_report = engine.resolve()

    # Runtime override for release-isolated runs and tests:
    # disable session ingestion when explicitly requested.
    ingest_override = os.environ.get("OPENCLAW_MEMORY_SESSION_INGEST_ENABLED")
    if ingest_override is not None:
        enabled = str(ingest_override).strip().lower() in {"1", "true", "yes", "on"}
        config_dict.setdefault("memory", {}).setdefault("auto_memory", {}).setdefault("session_ingestion", {})[
            "enabled"
        ] = enabled

    memory_dir = workspace_dir / "memory"
    daily_dir = memory_dir / "daily"
    memory_md = workspace_dir / "MEMORY.md"

    long_term = config_dict["memory"]["long_term"]
    keyword = config_dict["memory"]["keyword"]
    git_cfg = config_dict["memory"]["git"]
    kg_cfg = config_dict["memory"].get("knowledge_graph", {})
    auto_cfg = config_dict["memory"].get("auto_memory", {})
    compression_cfg = config_dict["memory"].get("compression", {})
    meta_cfg = config_dict["memory"].get("meta_cognition", {})
    sub_cfg = config_dict["memory"].get("subagents", {})
    learning_cfg = config_dict["memory"].get("learning", {})
    working_cfg = config_dict["memory"].get("working_memory", {})
    maintenance_cfg = config_dict["memory"].get("maintenance", {})
    security_cfg = config_dict["memory"].get("security", {})
    monitoring_cfg = config_dict["memory"].get("monitoring", {})
    connect_cfg = config_dict["memory"].setdefault("connect", {})

    long_term_resolved = _resolve_path(workspace_dir, long_term["path"])
    long_term["path"] = str(_prefer_migrated_path(workspace_dir, long_term_resolved, "memory_db"))
    keyword_resolved = _resolve_path(workspace_dir, keyword["index_dir"])
    keyword["index_dir"] = str(_prefer_migrated_path(workspace_dir, keyword_resolved, "whoosh_index"))
    git_cfg["repo_path"] = str(_resolve_path(workspace_dir, git_cfg["repo_path"]))
    if kg_cfg.get("db_path"):
        kg_resolved = _resolve_path(workspace_dir, kg_cfg["db_path"])
        kg_cfg["db_path"] = str(_prefer_migrated_path(workspace_dir, kg_resolved, "kg_db"))
    if auto_cfg.get("log_file"):
        auto_cfg["log_file"] = str(_resolve_path(workspace_dir, auto_cfg["log_file"]))
    if compression_cfg.get("report_dir"):
        compression_cfg["report_dir"] = str(_resolve_path(workspace_dir, compression_cfg["report_dir"]))
    if meta_cfg.get("report_dir"):
        meta_cfg["report_dir"] = str(_resolve_path(workspace_dir, meta_cfg["report_dir"]))
    if meta_cfg.get("task_log_file"):
        meta_cfg["task_log_file"] = str(_resolve_path(workspace_dir, meta_cfg["task_log_file"]))
    if sub_cfg.get("log_dir"):
        sub_cfg["log_dir"] = str(_resolve_path(workspace_dir, sub_cfg["log_dir"]))
    if learning_cfg.get("task_log_file"):
        learning_cfg["task_log_file"] = str(_resolve_path(workspace_dir, learning_cfg["task_log_file"]))
    if working_cfg.get("persistence_file"):
        working_cfg["persistence_file"] = str(_resolve_path(workspace_dir, working_cfg["persistence_file"]))
    if working_cfg.get("usage_log_file"):
        working_cfg["usage_log_file"] = str(_resolve_path(workspace_dir, working_cfg["usage_log_file"]))
    if maintenance_cfg.get("backup_dir"):
        maintenance_cfg["backup_dir"] = str(_resolve_path(workspace_dir, maintenance_cfg["backup_dir"]))
    if maintenance_cfg.get("state_file"):
        maintenance_cfg["state_file"] = str(_resolve_path(workspace_dir, maintenance_cfg["state_file"]))
    if maintenance_cfg.get("sync_audit_file"):
        maintenance_cfg["sync_audit_file"] = str(_resolve_path(workspace_dir, maintenance_cfg["sync_audit_file"]))
    if security_cfg.get("security_dir"):
        security_cfg["security_dir"] = str(_resolve_path(workspace_dir, security_cfg["security_dir"]))
    if security_cfg.get("state_file"):
        security_cfg["state_file"] = str(_resolve_path(workspace_dir, security_cfg["state_file"]))
    if security_cfg.get("key_material_file"):
        security_cfg["key_material_file"] = str(_resolve_path(workspace_dir, security_cfg["key_material_file"]))
    if security_cfg.get("recovery_material_file"):
        security_cfg["recovery_material_file"] = str(
            _resolve_path(workspace_dir, security_cfg["recovery_material_file"])
        )
    shadow_cfg = security_cfg.get("shadow", {})
    if isinstance(shadow_cfg, dict) and shadow_cfg.get("shadow_dir"):
        shadow_cfg["shadow_dir"] = str(_resolve_path(workspace_dir, str(shadow_cfg["shadow_dir"])))
    if isinstance(shadow_cfg, dict) and shadow_cfg.get("backup_dir"):
        backup_raw = str(shadow_cfg.get("backup_dir", "") or "").strip()
        if backup_raw:
            legacy_home_backup = str((Path.home() / ".shadow_backup").expanduser())
            if backup_raw in {"~/.shadow_backup", legacy_home_backup}:
                backup_raw = "memory/security/shadow_backup"
            shadow_cfg["backup_dir"] = str(_resolve_path(workspace_dir, backup_raw))
    if security_cfg.get("encrypted_targets"):
        resolved_targets = []
        for raw in security_cfg.get("encrypted_targets", []):
            rp = Path(str(raw)).expanduser()
            if rp.is_absolute():
                resolved_targets.append(str(rp))
            else:
                resolved_targets.append(str(workspace_dir / rp))
        security_cfg["encrypted_targets"] = resolved_targets
    if monitoring_cfg.get("daily_report_file"):
        monitoring_cfg["daily_report_file"] = str(_resolve_path(workspace_dir, monitoring_cfg["daily_report_file"]))
    if monitoring_cfg.get("daily_report_markdown"):
        monitoring_cfg["daily_report_markdown"] = str(
            _resolve_path(workspace_dir, monitoring_cfg["daily_report_markdown"])
        )
    alerts_cfg = monitoring_cfg.get("alerts", {})
    if alerts_cfg.get("alert_log_file"):
        alerts_cfg["alert_log_file"] = str(_resolve_path(workspace_dir, alerts_cfg["alert_log_file"]))
    connect_cfg["root"] = str(_resolve_path(workspace_dir, str(connect_cfg.get("root", "connect"))))

    return {
        "workspace_dir": workspace_dir,
        "memory_dir": memory_dir,
        "daily_dir": daily_dir,
        "memory_md": memory_md,
        "settings": config_dict,
        "config_report": config_report,
    }
