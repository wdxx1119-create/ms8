"""Configurable rules for knowledge tier/trust/admission/usage control."""

from __future__ import annotations

from typing import Any

DEFAULT_KNOWLEDGE_CONTROL: dict[str, Any] = {
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
}


def merge_control_cfg(raw: dict[str, Any] | None) -> dict[str, Any]:
    cfg = dict(DEFAULT_KNOWLEDGE_CONTROL)
    raw = raw or {}
    for key, val in raw.items():
        if isinstance(cfg.get(key), dict) and isinstance(val, dict):
            merged = dict(cfg[key])
            merged.update(val)
            cfg[key] = merged
        else:
            cfg[key] = val
    return cfg


def safe_usage_permission(cfg: dict[str, Any], trust_level: str) -> dict[str, Any]:
    mapping = cfg.get("usage_permission_by_trust", {})
    perm = mapping.get(trust_level, mapping.get("isolated", {"recall": False, "inject": "none", "speak": "deny"}))
    return {
        "recall": bool(perm.get("recall", False)),
        "inject": str(perm.get("inject", "none")),
        "speak": str(perm.get("speak", "deny")),
    }
