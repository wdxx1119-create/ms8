from __future__ import annotations

import pytest

ClosedPolicyEngine = pytest.importorskip("ms8_policy_core.engine").ClosedPolicyEngine


def _closed_engine():
    return ClosedPolicyEngine()


@pytest.mark.parametrize(
    ("text", "expected_route"),
    [
        ("ok", {"rejected", "short_term_only"}),
        ("   ", {"rejected"}),
        ("!!!!!", {"rejected"}),
        ("😀😀😀😀", {"rejected"}),
        ("这条配置在生产环境要启用，测试环境要禁用", {"accepted", "redacted_accept", "pending_review"}),
    ],
)
def test_noise_and_scope_samples(text: str, expected_route: set[str]) -> None:
    eng = _closed_engine()
    out = eng.evaluate_admission({"text": text})
    assert out["data"]["route"] in expected_route


@pytest.mark.parametrize(
    ("text", "must_flag", "expected_route"),
    [
        ("contact me a@b.com", "email", {"redacted_accept", "pending_review"}),
        ("phone=13800138000", "phone", {"redacted_accept", "pending_review"}),
        ("Authorization: Bearer abcdefghijklmnop", "bearer_token", {"redacted_accept", "pending_review"}),
        ("-----BEGIN OPENSSH PRIVATE KEY-----abc-----END OPENSSH PRIVATE KEY-----", "ssh_private_key", {"pending_review"}),
        ("-----BEGIN ENCRYPTED PRIVATE KEY-----abc-----END ENCRYPTED PRIVATE KEY-----", "ssh_private_key", {"pending_review"}),
        ("密码: abc123456", "password_cn", {"pending_review"}),
    ],
)
def test_privacy_attack_samples(text: str, must_flag: str, expected_route: set[str]) -> None:
    eng = _closed_engine()
    out = eng.evaluate_admission({"text": text})
    flags = set(out["data"].get("privacy_flags", []))
    assert must_flag in flags
    assert out["data"]["route"] in expected_route


def test_injection_bypass_sample_blocked() -> None:
    eng = _closed_engine()
    payload = {
        "candidates": [
            {"id": "debug-primary", "scope": "system_debug", "status": "accepted", "usage_permission": {"inject": "weak", "speak": "hint"}},
            {"id": "revoked-primary", "status": "revoked", "usage_permission": {"inject": "primary", "speak": "primary"}},
            {"id": "good", "status": "accepted", "usage_permission": {"inject": "primary", "speak": "primary"}, "trust_level": "hard_trust"},
        ],
        "budget": {"budget_top_k": 3},
    }
    out = eng.rank_retrieval(payload)
    ids = [row["id"] for row in out["data"]["items"]]
    assert ids == ["good"]
    blocked = {row["id"]: row["reason"] for row in out["data"]["blocked"]}
    assert "debug-primary" in blocked
    assert "revoked-primary" in blocked
