from __future__ import annotations

from ms8.app.pipeline.memory_admission_engine import evaluate_candidate
from ms8.app.rules.block_rules import evaluate_block
from ms8.app.rules.conflict_rules import evaluate_conflict
from ms8.app.rules.privacy_rules import redact_sensitive_text


def test_block_rules_reject_placeholder() -> None:
    out = evaluate_block("[图片]")
    assert out["blocked"] is True
    assert out["reason"] == "system_placeholder"
    assert out["suggested_route"] == "rejected"


def test_block_rules_short_term_for_low_value_command() -> None:
    out = evaluate_block("继续")
    assert out["blocked"] is True
    assert out["reason"] == "short_low_value_command"
    assert out["suggested_route"] == "short_term_only"


def test_privacy_rules_redact_password() -> None:
    out = redact_sensitive_text("数据库密码=abc123")
    assert out["has_sensitive"] is True
    assert "password_cn" in out["flags"]
    assert "[REDACTED_PASSWORD]" in out["redacted_text"]


def test_privacy_rules_redact_github_token() -> None:
    token = "github_pat_" + "A" * 24
    out = redact_sensitive_text(f"token: {token}")
    assert out["has_sensitive"] is True
    assert "github_token" in out["flags"]
    assert "[REDACTED_TOKEN]" in out["redacted_text"]


def test_conflict_rules_detect_state_conflict() -> None:
    out = evaluate_conflict("这里必须启用缓存，同时又禁用缓存")
    assert out["has_conflict"] is True
    assert out["resolution"] == "pending_review"
    assert "state_conflict" in out["conflict_flags"]


def test_conflict_rules_detect_temporal_update() -> None:
    out = evaluate_conflict("原来阈值是0.6，现在改成0.8")
    assert out["has_conflict"] is True
    assert out["resolution"] == "replace_old"
    assert "temporal_evolution" in out["conflict_flags"]


def test_admission_rejects_empty_or_ack() -> None:
    out = evaluate_candidate("ok")
    assert out.route == "rejected"
    assert out.should_persist_main is False
    assert out.should_index is False


def test_admission_pending_review_on_secret() -> None:
    out = evaluate_candidate("password=supersecret")
    assert out.route == "pending_review"
    assert out.should_persist_main is True
    assert out.should_index is False
    assert out.should_write_memory_md is False


def test_admission_redacted_accept_for_email() -> None:
    out = evaluate_candidate("联系我：demo@example.com")
    assert out.route == "redacted_accept"
    assert out.redacted is True
    assert "[REDACTED_EMAIL]" in out.normalized_text


def test_admission_conflict_goes_pending_review() -> None:
    out = evaluate_candidate("这个功能应该使用A，但不要使用A")
    assert out.route == "pending_review"
    assert "negation_conflict" in out.conflict_flags
