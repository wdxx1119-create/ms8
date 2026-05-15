from __future__ import annotations

from datetime import datetime, timezone
import json

from memory.core import MemoryCore


def _build_core(tmp_path):
    core = MemoryCore.__new__(MemoryCore)
    core.config = {
        "workspace_dir": tmp_path,
        "memory_dir": tmp_path / "memory",
        "settings": {
            "memory": {
                "security": {
                    "use_keychain": False,
                    "keychain_service": "openclaw-memory",
                },
                "maintenance_policy": {
                    "threshold_suggestion_allowed_keys": [
                        "working_memory.dynamic_injection_budget.",
                        "knowledge_control.retrieval_mix_balancer.",
                    ]
                }
            }
        },
    }
    core._threshold_pending_file = core.config["memory_dir"] / "threshold_suggestions_pending.json"
    core._threshold_pending_key_file = core.config["memory_dir"] / "threshold_suggestions_pending.key"
    core._utc_now = lambda: datetime.now(timezone.utc)
    return core


def test_threshold_pending_signature_detects_tamper(tmp_path):
    core = _build_core(tmp_path)
    core.config["memory_dir"].mkdir(parents=True, exist_ok=True)
    payload = {"items": [{"approval_id": "a1", "status": "pending"}], "last_generated_at": "x", "last_applied_at": None}
    core._save_threshold_pending(payload)
    loaded = core._load_threshold_pending()
    assert loaded.get("_integrity_valid") is True

    tampered = json.loads(core._threshold_pending_file.read_text(encoding="utf-8"))
    tampered["items"][0]["status"] = "approved"
    core._threshold_pending_file.write_text(json.dumps(tampered, ensure_ascii=False, indent=2), encoding="utf-8")
    loaded2 = core._load_threshold_pending()
    assert loaded2.get("_integrity_valid") is False


def test_apply_threshold_suggestions_allowlist_and_missing_key_guard(tmp_path):
    core = _build_core(tmp_path)
    cfg = {
        "memory": {
            "working_memory": {"dynamic_injection_budget": {"simple_top_k": 5}},
            "knowledge_control": {"retrieval_mix_balancer": {"hard_top_ratio": 0.22}},
        }
    }
    (tmp_path / "config.yaml").write_text(
        json.dumps(cfg, ensure_ascii=False),
        encoding="utf-8",
    )
    # Use yaml input format expected by reader.
    (tmp_path / "config.yaml").write_text(
        "memory:\n"
        "  working_memory:\n"
        "    dynamic_injection_budget:\n"
        "      simple_top_k: 5\n"
        "  knowledge_control:\n"
        "    retrieval_mix_balancer:\n"
        "      hard_top_ratio: 0.22\n",
        encoding="utf-8",
    )
    out = core._apply_threshold_suggestions_to_workspace_config(
        [
            {"key": "working_memory.dynamic_injection_budget.simple_top_k", "direction": "decrease", "delta": -1},
            {"key": "memory.untrusted.path", "direction": "increase", "delta": 1},
            {"key": "knowledge_control.retrieval_mix_balancer.unknown_ratio", "direction": "increase", "delta": 0.1},
        ]
    )
    assert out["status"] == "success"
    assert any(x["key"] == "working_memory.dynamic_injection_budget.simple_top_k" for x in out.get("applied", []))
    rejected_keys = {x["key"] for x in out.get("rejected", [])}
    assert "memory.untrusted.path" in rejected_keys
    assert "knowledge_control.retrieval_mix_balancer.unknown_ratio" in rejected_keys


def test_threshold_key_prefers_keychain_when_enabled(tmp_path):
    core = _build_core(tmp_path)
    core.config["settings"]["memory"]["security"]["use_keychain"] = True
    expected = b"\x11" * 32
    core._load_threshold_pending_key_from_keychain = lambda: expected
    core._save_threshold_pending_key_to_keychain = lambda key: True
    got = core._load_threshold_pending_hmac_key()
    assert got == expected
    assert core._threshold_pending_key_source == "keychain"


def test_threshold_key_fallbacks_to_file_when_keychain_unavailable(tmp_path):
    core = _build_core(tmp_path)
    core.config["settings"]["memory"]["security"]["use_keychain"] = True
    core._load_threshold_pending_key_from_keychain = lambda: None
    core._save_threshold_pending_key_to_keychain = lambda key: False
    key = core._load_threshold_pending_hmac_key()
    assert isinstance(key, bytes)
    assert len(key) == 32
    assert core._threshold_pending_key_source == "file"
    assert core._threshold_pending_key_file.exists()
