#!/usr/bin/env python3
from __future__ import annotations

import os
import re
import subprocess
import sys
from dataclasses import dataclass

LAYER_RE = re.compile(r"^(runtime_health|memory_quality_health|security_governance_health):\s+([a-zA-Z_]+)\s*$")
OVERALL_RE = re.compile(r"^Overall:\s+([a-zA-Z_]+)\s*$")
POLICY_ATTACK_RE = re.compile(
    r"^[✅\-\s]*policy-attack-samples:\s+present=(True|False)\s+ok=(True|False)\s+failed=(\d+)/(\d+)\s+age=([^\s]+)\s*$"
)


@dataclass
class GateConfig:
    mode: str  # off | warn | strict
    fail_on_overall_warn: bool
    fail_on_overall_fail: bool
    fail_on_security_warn: bool
    fail_on_any_layer_warn: bool
    fail_on_policy_attack_fail: bool
    fail_on_policy_attack_missing: bool


def _env_bool(name: str, default: bool) -> bool:
    raw = str(os.getenv(name, str(default))).strip().lower()
    if raw in {"1", "true", "yes", "on"}:
        return True
    if raw in {"0", "false", "no", "off"}:
        return False
    return default


def _config() -> GateConfig:
    mode = str(os.getenv("MS8_GOV_GATE_MODE", "warn")).strip().lower()
    if mode not in {"off", "warn", "strict"}:
        mode = "warn"
    return GateConfig(
        mode=mode,
        fail_on_overall_warn=_env_bool("MS8_GOV_FAIL_ON_OVERALL_WARN", False),
        fail_on_overall_fail=_env_bool("MS8_GOV_FAIL_ON_OVERALL_FAIL", True),
        fail_on_security_warn=_env_bool("MS8_GOV_FAIL_ON_SECURITY_WARN", True),
        fail_on_any_layer_warn=_env_bool("MS8_GOV_FAIL_ON_ANY_LAYER_WARN", False),
        fail_on_policy_attack_fail=_env_bool("MS8_GOV_FAIL_ON_POLICY_ATTACK_FAIL", True),
        fail_on_policy_attack_missing=_env_bool("MS8_GOV_FAIL_ON_POLICY_ATTACK_MISSING", False),
    )


def _run_doctor() -> str:
    cmd_primary = [sys.executable, "-m", "ms8", "doctor"]
    cmd_fallback = [sys.executable, "-m", "src.ms8", "doctor"]
    for cmd in (cmd_primary, cmd_fallback):
        proc = subprocess.run(cmd, capture_output=True, text=True, check=False)
        if proc.returncode == 0:
            return str(proc.stdout or "")
    # Return last stderr/stdout for diagnosis and fail closed at caller.
    return str(proc.stdout or "") + "\n" + str(proc.stderr or "")


def main() -> int:
    cfg = _config()
    if cfg.mode == "off":
        print('{"ok": true, "gate": "off", "reason": "disabled"}')
        return 0

    text = _run_doctor()
    layers: dict[str, str] = {}
    overall = ""
    policy_attack: dict[str, object] = {}
    for line in text.splitlines():
        s = line.strip()
        m = LAYER_RE.match(s)
        if m:
            layers[m.group(1)] = m.group(2).lower()
            continue
        m2 = OVERALL_RE.match(s)
        if m2:
            overall = m2.group(1).lower()
            continue
        m3 = POLICY_ATTACK_RE.match(s)
        if m3:
            present = m3.group(1) == "True"
            ok = m3.group(2) == "True"
            failed = int(m3.group(3))
            total = int(m3.group(4))
            policy_attack = {
                "present": present,
                "ok": ok,
                "failed": failed,
                "total": total,
                "age": m3.group(5),
            }

    if not layers or not overall:
        print('{"ok": false, "error": "doctor_output_unparseable"}')
        return 2

    problems: list[str] = []
    if cfg.fail_on_overall_fail and overall in {"failed", "fail", "degraded", "error"}:
        problems.append(f"overall={overall}")
    if cfg.fail_on_overall_warn and overall in {"warn", "warning"}:
        problems.append(f"overall_warn={overall}")
    if cfg.fail_on_security_warn and layers.get("security_governance_health", "") in {"warn", "warning"}:
        problems.append("security_governance_health=warn")
    if cfg.mode == "strict":
        warn_layers = [k for k, v in layers.items() if v in {"warn", "warning"}]
        if warn_layers:
            problems.append(f"strict_warn_layers={','.join(sorted(warn_layers))}")
        if not policy_attack:
            problems.append("policy_attack_samples_missing_in_strict_mode")
    elif cfg.fail_on_any_layer_warn:
        warn_layers = [k for k, v in layers.items() if v in {"warn", "warning"}]
        if warn_layers:
            problems.append(f"warn_layers={','.join(sorted(warn_layers))}")

    if cfg.fail_on_policy_attack_missing and not policy_attack:
        problems.append("policy_attack_samples_missing")
    if cfg.fail_on_policy_attack_fail and policy_attack:
        if bool(policy_attack.get("present")) and (not bool(policy_attack.get("ok")) or int(policy_attack.get("failed", 0)) > 0):
            problems.append(
                f"policy_attack_samples_failed={policy_attack.get('failed', 0)}/{policy_attack.get('total', 0)}"
            )

    if problems:
        print(
            f'{{"ok": false, "gate": "{cfg.mode}", "overall": "{overall}", '
            f'"layers": {repr(layers)}, "policy_attack": {repr(policy_attack)}, "problems": {repr(problems)}}}'
        )
        return 1

    print(
        f'{{"ok": true, "gate": "{cfg.mode}", "overall": "{overall}", '
        f'"layers": {repr(layers)}, "policy_attack": {repr(policy_attack)}}}'
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
