from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from ms8.engine_core.maintenance.self_check import check_specs as cs


class _CoreReady:
    def __init__(self, memory_dir: Path) -> None:
        self.config = {"memory_dir": str(memory_dir)}
        self.auto_memory = SimpleNamespace()
        self.whoosh_search = object()
        self.monitoring = object()
        self.shadow = object()


def test_l2_pipeline_stages_ready(tmp_path: Path) -> None:
    out = cs._check_l2_pipeline_stages(_CoreReady(tmp_path), {})
    assert out["status"] == "pass"


def test_c15_agent_template_semantics_warn_when_missing_file(monkeypatch) -> None:
    original_exists = cs.Path.exists

    def _fake_exists(self: Path) -> bool:
        if str(self).endswith("agent_native/task_templates.py"):
            return False
        return original_exists(self)

    monkeypatch.setattr(cs.Path, "exists", _fake_exists)
    out = cs._check_c15_agent_native_template_semantics(None, {})
    assert out["status"] == "warn"
    assert "missing" in out["message"]


def test_c15_agent_template_semantics_warn_when_tokens_missing(monkeypatch) -> None:
    original_read_text = cs.Path.read_text

    def _fake_read_text(self: Path, encoding: str = "utf-8", errors: str = "ignore") -> str:
        if str(self).endswith("agent_native/task_templates.py"):
            return "ASK_USER:\nALLOWED_COMMANDS\n"
        return original_read_text(self, encoding=encoding, errors=errors)

    monkeypatch.setattr(cs.Path, "read_text", _fake_read_text)
    out = cs._check_c15_agent_native_template_semantics(None, {})
    assert out["status"] == "warn"
    assert "STOP NEEDS_CONFIRM" in out["details"]["missing_tokens"]


def test_c15_agent_template_semantics_pass_when_tokens_present(monkeypatch) -> None:
    original_read_text = cs.Path.read_text

    def _fake_read_text(self: Path, encoding: str = "utf-8", errors: str = "ignore") -> str:
        if str(self).endswith("agent_native/task_templates.py"):
            return "ASK_USER:\nSTOP NEEDS_CONFIRM\nALLOWED_COMMANDS\nMS8_FIRST_INSTALL_REPORT\n"
        return original_read_text(self, encoding=encoding, errors=errors)

    monkeypatch.setattr(cs.Path, "read_text", _fake_read_text)
    out = cs._check_c15_agent_native_template_semantics(None, {})
    assert out["status"] == "pass"


def test_m10_product_decision_policy_warn_when_engine_missing(monkeypatch) -> None:
    original_exists = cs.Path.exists

    def _fake_exists(self: Path) -> bool:
        if str(self).endswith("/engine.py"):
            return False
        return original_exists(self)

    monkeypatch.setattr(cs.Path, "exists", _fake_exists)
    out = cs._check_m10_product_decision_injection_policy(None, {})
    assert out["status"] == "warn"


def test_m10_product_decision_policy_warn_when_tokens_missing(monkeypatch) -> None:
    original_read_text = cs.Path.read_text

    def _fake_read_text(self: Path, encoding: str = "utf-8", errors: str = "ignore") -> str:
        if str(self).endswith("/engine.py"):
            return "def x():\n    return 1\n"
        return original_read_text(self, encoding=encoding, errors=errors)

    monkeypatch.setattr(cs.Path, "read_text", _fake_read_text)
    out = cs._check_m10_product_decision_injection_policy(None, {})
    assert out["status"] == "warn"
    assert out["details"]["missing_tokens"]


def test_m10_product_decision_policy_pass_when_tokens_present(monkeypatch) -> None:
    original_read_text = cs.Path.read_text

    def _fake_read_text(self: Path, encoding: str = "utf-8", errors: str = "ignore") -> str:
        if str(self).endswith("/engine.py"):
            return (
                'if category == "product_decision":\n'
                "    decision_hints = ['choose']\n"
                "    if not any(h in q for h in decision_hints):\n"
                "        return []\n"
            )
        return original_read_text(self, encoding=encoding, errors=errors)

    monkeypatch.setattr(cs.Path, "read_text", _fake_read_text)
    out = cs._check_m10_product_decision_injection_policy(None, {})
    assert out["status"] == "pass"
