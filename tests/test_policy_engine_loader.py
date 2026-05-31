from __future__ import annotations

import sys
import types

from ms8.engine_core import policy_engine_loader as loader


def test_loader_open_backend(monkeypatch) -> None:
    monkeypatch.setenv("MS8_POLICY_BACKEND", "open")
    loader.reset_policy_engine_for_tests()
    engine = loader.get_policy_engine()
    assert engine.backend_name == "open"
    status = loader.get_policy_backend_status()
    assert status["policy_backend"] == "open"
    assert status["policy_fallback_reason"] == ""


def test_loader_closed_fallback_to_open(monkeypatch) -> None:
    monkeypatch.setenv("MS8_POLICY_BACKEND", "closed")
    monkeypatch.setenv("MS8_POLICY_MODULE", "module_that_does_not_exist_for_test")
    loader.reset_policy_engine_for_tests()
    engine = loader.get_policy_engine()
    assert engine.backend_name == "open"
    status = loader.get_policy_backend_status()
    assert status["policy_backend"] == "open"
    assert "closed_load_failed" in status["policy_fallback_reason"]


def test_loader_auto_invalid_env_defaults(monkeypatch) -> None:
    monkeypatch.setenv("MS8_POLICY_BACKEND", "invalid")
    monkeypatch.setenv("MS8_POLICY_MODULE", "module_that_does_not_exist_for_test")
    loader.reset_policy_engine_for_tests()
    engine = loader.get_policy_engine()
    assert engine.backend_name == "open"
    status = loader.get_policy_backend_status()
    assert status["policy_backend"] == "open"
    assert "auto_closed_unavailable" in status["policy_fallback_reason"]


def test_loader_closed_backend_success_from_custom_module(monkeypatch) -> None:
    mod = types.ModuleType("fake_policy_mod")

    class _Closed:
        backend_name = "closed"
        backend_version = "9.9"

        def evaluate_admission(self, payload):
            return {"ok": True, "code": "OK", "reason": "x", "trace_id": "1", "data": {"route": "accepted"}}

        def rank_retrieval(self, payload):
            return {"ok": True, "code": "OK", "reason": "x", "trace_id": "1", "data": {"items": []}}

        def run_self_check_specs(self, payload):
            return {"ok": True, "code": "OK", "reason": "x", "trace_id": "1", "data": {}}

        def plan_self_repair(self, payload):
            return {"ok": True, "code": "OK", "reason": "x", "trace_id": "1", "data": {}}

        def shadow_decide(self, payload):
            return {"ok": True, "code": "OK", "reason": "x", "trace_id": "1", "data": {}}

    mod.create_policy_engine = lambda: _Closed()
    monkeypatch.setitem(sys.modules, "fake_policy_mod", mod)
    monkeypatch.setenv("MS8_POLICY_BACKEND", "closed")
    monkeypatch.setenv("MS8_POLICY_MODULE", "fake_policy_mod")
    loader.reset_policy_engine_for_tests()
    engine = loader.get_policy_engine()
    assert engine.backend_name == "closed"
    status = loader.get_policy_backend_status()
    assert status["policy_backend"] == "closed"
    assert status["policy_engine_version"] == "9.9"
    assert status["policy_module"] == "fake_policy_mod"


def test_loader_closed_backend_contract_fail_fallback(monkeypatch) -> None:
    mod = types.ModuleType("fake_policy_bad")

    class _BadClosed:
        backend_name = "closed"
        backend_version = "0.0"

    mod.create_policy_engine = lambda: _BadClosed()
    monkeypatch.setitem(sys.modules, "fake_policy_bad", mod)
    monkeypatch.setenv("MS8_POLICY_BACKEND", "closed")
    monkeypatch.setenv("MS8_POLICY_MODULE", "fake_policy_bad")
    loader.reset_policy_engine_for_tests()
    engine = loader.get_policy_engine()
    assert engine.backend_name == "open"
    status = loader.get_policy_backend_status()
    assert "closed_load_failed:policy_engine_contract_missing" in status["policy_fallback_reason"]


def test_loader_strict_closed_raises_on_missing_module(monkeypatch) -> None:
    monkeypatch.setenv("MS8_POLICY_BACKEND", "closed")
    monkeypatch.setenv("MS8_POLICY_MODULE", "module_that_does_not_exist_for_test")
    monkeypatch.setenv("MS8_POLICY_STRICT", "1")
    loader.reset_policy_engine_for_tests()
    try:
        loader.get_policy_engine()
        raise AssertionError("expected strict mode to raise")
    except RuntimeError as exc:
        assert "strict policy backend load failed" in str(exc)


def test_loader_status_contains_strict_flag(monkeypatch) -> None:
    monkeypatch.setenv("MS8_POLICY_BACKEND", "open")
    monkeypatch.setenv("MS8_POLICY_STRICT", "true")
    loader.reset_policy_engine_for_tests()
    _ = loader.get_policy_engine()
    status = loader.get_policy_backend_status()
    assert status["policy_strict_mode"] is True
