"""Policy engine loader with closed/open fallback."""

from __future__ import annotations

import importlib
import os
from dataclasses import dataclass
from typing import Any, cast

from .license import PolicyLicenseStatus, validate_policy_license
from .policy_engine_iface import PolicyEngine
from .policy_engine_open import OpenPolicyEngine


@dataclass
class PolicyBackendStatus:
    backend: str
    version: str
    fallback_reason: str
    license: PolicyLicenseStatus | None = None


_ENGINE_SINGLETON: PolicyEngine | None = None
_STATUS = PolicyBackendStatus(
    backend="open",
    version=OpenPolicyEngine.backend_version,
    fallback_reason="not_initialized",
    license=None,
)


def _resolve_module_name() -> str:
    raw = str(os.getenv("MS8_POLICY_MODULE", "ms8_policy_engine")).strip()
    return raw or "ms8_policy_engine"


def _validate_engine_contract(engine: Any) -> PolicyEngine:
    required = [
        "evaluate_admission",
        "rank_retrieval",
        "run_self_check_specs",
        "plan_self_repair",
        "shadow_decide",
    ]
    for name in required:
        attr = getattr(engine, name, None)
        if not callable(attr):
            raise TypeError(f"policy_engine_contract_missing:{name}")
    return cast(PolicyEngine, engine)


def _load_closed_backend() -> PolicyEngine:
    module = importlib.import_module(_resolve_module_name())
    create = getattr(module, "create_policy_engine", None)
    if callable(create):
        engine = create()
        return _validate_engine_contract(engine)
    engine_cls = getattr(module, "ClosedPolicyEngine", None)
    if engine_cls is None:
        raise ImportError("policy_module missing create_policy_engine/ClosedPolicyEngine")
    return _validate_engine_contract(engine_cls())


def _resolve_backend_name() -> str:
    val = str(os.getenv("MS8_POLICY_BACKEND", "auto")).strip().lower()
    if val in {"closed", "open", "auto"}:
        return val
    return "auto"


def _is_strict_mode() -> bool:
    val = str(os.getenv("MS8_POLICY_STRICT", "")).strip().lower()
    return val in {"1", "true", "yes", "on"}


def _build_engine() -> PolicyEngine:
    global _STATUS
    lic = validate_policy_license()

    def _license_allows_closed() -> bool:
        if not lic.enabled:
            return True
        return lic.status in {"valid", "grace", "warn"}

    backend = _resolve_backend_name()
    if backend == "open":
        _STATUS = PolicyBackendStatus("open", OpenPolicyEngine.backend_version, "", lic)
        return OpenPolicyEngine()
    if backend == "closed":
        if not _license_allows_closed():
            _STATUS = PolicyBackendStatus(
                "open",
                OpenPolicyEngine.backend_version,
                f"license_denied:{lic.reason_code}",
                lic,
            )
            return OpenPolicyEngine()
        try:
            engine = _load_closed_backend()
            _STATUS = PolicyBackendStatus(
                getattr(engine, "backend_name", "closed"),
                getattr(engine, "backend_version", "unknown"),
                "",
                lic,
            )
            return engine
        except (ImportError, AttributeError, TypeError, ValueError) as exc:
            if _is_strict_mode():
                _STATUS = PolicyBackendStatus(
                    "error",
                    "unknown",
                    f"strict_closed_load_failed:{exc}",
                    lic,
                )
                raise RuntimeError(f"strict policy backend load failed: {exc}") from exc
            _STATUS = PolicyBackendStatus(
                "open",
                OpenPolicyEngine.backend_version,
                f"closed_load_failed:{exc}",
                lic,
            )
            return OpenPolicyEngine()
    # auto
    if not _license_allows_closed():
        _STATUS = PolicyBackendStatus(
            "open",
            OpenPolicyEngine.backend_version,
            f"license_denied:{lic.reason_code}",
            lic,
        )
        return OpenPolicyEngine()
    try:
        engine = _load_closed_backend()
        _STATUS = PolicyBackendStatus(
            getattr(engine, "backend_name", "closed"),
            getattr(engine, "backend_version", "unknown"),
            "",
            lic,
        )
        return engine
    except (ImportError, AttributeError, TypeError, ValueError) as exc:
        if _is_strict_mode():
            _STATUS = PolicyBackendStatus(
                "error",
                "unknown",
                f"strict_auto_closed_unavailable:{exc}",
                lic,
            )
            raise RuntimeError(f"strict policy backend unavailable: {exc}") from exc
        _STATUS = PolicyBackendStatus(
            "open",
            OpenPolicyEngine.backend_version,
            f"auto_closed_unavailable:{exc}",
            lic,
        )
        return OpenPolicyEngine()


def get_policy_engine() -> PolicyEngine:
    global _ENGINE_SINGLETON
    if _ENGINE_SINGLETON is None:
        _ENGINE_SINGLETON = _build_engine()
    return _ENGINE_SINGLETON


def reset_policy_engine_for_tests() -> None:
    global _ENGINE_SINGLETON, _STATUS
    _ENGINE_SINGLETON = None
    _STATUS = PolicyBackendStatus(
        "open",
        OpenPolicyEngine.backend_version,
        "not_initialized",
        None,
    )


def get_policy_backend_status() -> dict[str, Any]:
    # ensure singleton initialized so status is meaningful
    _ = get_policy_engine()
    out: dict[str, Any] = {
        "policy_backend": _STATUS.backend,
        "policy_engine_version": _STATUS.version,
        "policy_fallback_reason": _STATUS.fallback_reason,
        "policy_module": _resolve_module_name(),
        "policy_strict_mode": _is_strict_mode(),
    }
    if _STATUS.license is not None:
        out["policy_license"] = _STATUS.license.to_dict()
    return out


def classify_intent_with_policy(text: str) -> str:
    engine = get_policy_engine()
    method = getattr(engine, "classify_intent", None)
    if callable(method):
        env = method({"text": text})
        data = env.get("data", {}) if isinstance(env.get("data", {}), dict) else {}
        intent = str(data.get("intent", "")).strip().lower()
        if intent:
            return intent
    return "statement"


def identify_topic_with_policy(text: str) -> str:
    engine = get_policy_engine()
    method = getattr(engine, "identify_topic", None)
    if callable(method):
        env = method({"text": text})
        data = env.get("data", {}) if isinstance(env.get("data", {}), dict) else {}
        topic = str(data.get("topic", "")).strip().lower()
        if topic:
            return topic
    return "general"
