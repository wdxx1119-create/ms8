from __future__ import annotations

import importlib.util
import sys
from pathlib import Path


def _load_gate_module():
    script = Path(__file__).resolve().parents[1] / "scripts" / "check_governance_gate.py"
    spec = importlib.util.spec_from_file_location("check_governance_gate", script)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


def test_gate_fails_when_policy_attack_samples_failed(monkeypatch):
    mod = _load_gate_module()
    monkeypatch.setenv("MS8_GOV_GATE_MODE", "warn")
    monkeypatch.setenv("MS8_GOV_FAIL_ON_SECURITY_WARN", "false")
    monkeypatch.setenv("MS8_GOV_FAIL_ON_POLICY_ATTACK_FAIL", "true")
    monkeypatch.setattr(
        mod,
        "_run_doctor",
        lambda: "\n".join(
            [
                "runtime_health: healthy",
                "memory_quality_health: healthy",
                "security_governance_health: healthy",
                "✅ policy-attack-samples: present=True ok=False failed=1/3 age=1.0h",
                "Overall: healthy",
            ]
        ),
    )
    assert mod.main() == 1


def test_gate_passes_when_policy_attack_samples_ok(monkeypatch):
    mod = _load_gate_module()
    monkeypatch.setenv("MS8_GOV_GATE_MODE", "warn")
    monkeypatch.setenv("MS8_GOV_FAIL_ON_SECURITY_WARN", "false")
    monkeypatch.setenv("MS8_GOV_FAIL_ON_POLICY_ATTACK_FAIL", "true")
    monkeypatch.setattr(
        mod,
        "_run_doctor",
        lambda: "\n".join(
            [
                "runtime_health: healthy",
                "memory_quality_health: healthy",
                "security_governance_health: healthy",
                "✅ policy-attack-samples: present=True ok=True failed=0/3 age=1.0h",
                "Overall: healthy",
            ]
        ),
    )
    assert mod.main() == 0
