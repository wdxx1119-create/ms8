from __future__ import annotations

import collections
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ms8.engine_core.core import MemoryCore


def _core_stub(tmp_path: Path) -> MemoryCore:
    core = MemoryCore.__new__(MemoryCore)
    core._recent_query_tokens = collections.deque(maxlen=24)
    core.config = {
        "workspace_dir": tmp_path,
        "settings": {
            "memory": {
                "security": {"use_keychain": False, "keychain_service": "ms8-memory"},
                "maintenance_policy": {
                    "threshold_suggestion_pending_max": 2,
                    "threshold_suggestion_allowed_keys": ["working_memory.dynamic_injection_budget."],
                },
            }
        },
    }
    core._threshold_pending_key_file = tmp_path / "memory" / "threshold_pending_hmac.key"
    core._threshold_pending_file = tmp_path / "memory" / "threshold_pending.json"
    core._threshold_approval_log_file = tmp_path / "memory" / "threshold_approval.log.jsonl"
    core._utc_now = lambda: datetime(2026, 5, 20, tzinfo=timezone.utc)  # type: ignore[method-assign]
    return core


def test_threshold_signature_and_verify_roundtrip(tmp_path: Path) -> None:
    c = _core_stub(tmp_path)
    payload: dict[str, Any] = {"items": [{"x": 1}], "last_generated_at": None}
    c._save_threshold_pending(payload)
    saved = json.loads(c._threshold_pending_file.read_text(encoding="utf-8"))
    assert "_integrity" in saved
    assert c._verify_threshold_pending_signature(saved) is True
    saved["_integrity"]["signature"] = "deadbeef"
    assert c._verify_threshold_pending_signature(saved) is False


def test_load_threshold_pending_hmac_key_file_and_keychain_paths(monkeypatch, tmp_path: Path) -> None:
    c = _core_stub(tmp_path)
    key = c._load_threshold_pending_hmac_key()
    assert isinstance(key, bytes)
    assert c._threshold_pending_key_source == "file"
    assert c._threshold_pending_key_file.exists()

    # keychain enabled + keychain load success
    c.config["settings"]["memory"]["security"]["use_keychain"] = True
    monkeypatch.setattr(c, "_load_threshold_pending_key_from_keychain", lambda: b"\x01" * 32)
    key2 = c._load_threshold_pending_hmac_key()
    assert key2 == b"\x01" * 32
    assert c._threshold_pending_key_source == "keychain"

    # keychain enabled + no keychain + file exists -> migrate save success
    c._threshold_pending_key_file.parent.mkdir(parents=True, exist_ok=True)
    c._threshold_pending_key_file.write_bytes(b"\x02" * 32)
    monkeypatch.setattr(c, "_load_threshold_pending_key_from_keychain", lambda: None)
    monkeypatch.setattr(c, "_save_threshold_pending_key_to_keychain", lambda _k: True)
    key3 = c._load_threshold_pending_hmac_key()
    assert key3 == b"\x02" * 32
    assert c._threshold_pending_key_source == "keychain"


def test_queue_threshold_suggestions_caps_pending(tmp_path: Path) -> None:
    c = _core_stub(tmp_path)
    report = {"window": 7, "stats": {"a": 1}, "suggestions": [{"key": "working_memory.dynamic_injection_budget.max"}]}
    c._queue_threshold_suggestions(report, source="manual")
    c._queue_threshold_suggestions(report, source="manual")
    c._queue_threshold_suggestions(report, source="manual")
    payload = json.loads(c._threshold_pending_file.read_text(encoding="utf-8"))
    assert len(payload["items"]) == 2
    assert payload["items"][-1]["source"] == "manual"


def test_apply_threshold_suggestions_allowlist_and_missing_key(tmp_path: Path) -> None:
    c = _core_stub(tmp_path)
    cfg = tmp_path / "config.yaml"
    cfg.write_text(
        "memory:\n"
        "  working_memory:\n"
        "    dynamic_injection_budget:\n"
        "      max: 0.6\n"
        "      min: 0.2\n",
        encoding="utf-8",
    )
    out = c._apply_threshold_suggestions_to_workspace_config(
        [
            {"key": "working_memory.dynamic_injection_budget.max", "delta": 0.1, "direction": "increase"},
            {"key": "working_memory.dynamic_injection_budget.unknown", "delta": 0.1, "direction": "increase"},
            {"key": "knowledge_control.retrieval_mix_balancer.k", "delta": 0.2, "direction": "increase"},
            {"key": "bad key", "delta": 0.1},
        ]
    )
    assert out["status"] == "success"
    assert any(x["key"] == "working_memory.dynamic_injection_budget.max" for x in out["applied"])
    rejected = {x["reason"] for x in out["rejected"]}
    assert "missing_existing_config_key" in rejected
    assert "key_not_allowlisted" in rejected
    assert "invalid_key_format" in rejected


def test_apply_threshold_suggestions_skip_when_no_changes(tmp_path: Path) -> None:
    c = _core_stub(tmp_path)
    cfg = tmp_path / "config.yaml"
    cfg.write_text(
        "memory:\n"
        "  working_memory:\n"
        "    dynamic_injection_budget:\n"
        "      max: 0.6\n",
        encoding="utf-8",
    )
    out = c._apply_threshold_suggestions_to_workspace_config(
        [{"key": "working_memory.dynamic_injection_budget.max", "delta": 0.0, "direction": "increase"}]
    )
    assert out["status"] == "skipped"
    assert out["reason"] == "no_effective_changes"
