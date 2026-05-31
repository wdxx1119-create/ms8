from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from ms8.engine_core import core as core_mod


class _Noop:
    def __init__(self, *args, **kwargs):  # noqa: D401, ANN002, ANN003
        pass


def _cfg(tmp_path: Path) -> dict:
    memory_dir = tmp_path / "memory"
    memory_dir.mkdir(parents=True, exist_ok=True)
    return {
        "workspace_dir": tmp_path,
        "memory_dir": memory_dir,
        "settings": {
            "memory": {
                "short_term": {"max_size": 32},
                "skills_system": {"github_enabled": False},
                "meta_cognition": {"enabled": False},
                "advanced_insight": {"enabled": False},
                "llm": {},
            }
        },
    }


def _patch_core_init_dependencies(monkeypatch: pytest.MonkeyPatch) -> None:
    # Imported symbols in core.py
    monkeypatch.setattr(core_mod, "FileMemoryStore", _Noop)
    monkeypatch.setattr(core_mod, "SQLiteMemoryStore", _Noop)
    monkeypatch.setattr(core_mod, "WhooshSearch", _Noop)
    monkeypatch.setattr(core_mod, "SemanticMemorySearch", _Noop)
    monkeypatch.setattr(core_mod, "GitMemoryManager", _Noop)
    monkeypatch.setattr(core_mod, "MemoryBlocks", _Noop)
    monkeypatch.setattr(core_mod, "MemoryGovernance", _Noop)
    monkeypatch.setattr(core_mod, "EnhancedSubAgentManager", lambda *_a, **_k: _Noop())
    monkeypatch.setattr(core_mod, "SkillManager", _Noop)
    monkeypatch.setattr(core_mod, "SkillInstaller", lambda *_a, **_k: _Noop())
    monkeypatch.setattr(core_mod, "SkillRegistry", _Noop)
    monkeypatch.setattr(core_mod, "BuiltInSkills", _Noop)
    monkeypatch.setattr(core_mod, "SkillDiscovery", lambda *_a, **_k: _Noop())
    monkeypatch.setattr(core_mod, "GitHubSkillDiscovery", _Noop)
    monkeypatch.setattr(core_mod, "SkillSearchIndex", _Noop)
    monkeypatch.setattr(core_mod, "SelfImprovementEngine", lambda *_a, **_k: SimpleNamespace())
    monkeypatch.setattr(core_mod, "KnowledgeGraph", lambda *_a, **_k: SimpleNamespace())
    monkeypatch.setattr(core_mod, "MemoryLearning", lambda *_a, **_k: _Noop())
    monkeypatch.setattr(core_mod, "WorkingMemoryManager", _Noop)
    monkeypatch.setattr(core_mod, "MaintenanceManager", _Noop)
    monkeypatch.setattr(core_mod, "MemoryMonitoring", _Noop)
    monkeypatch.setattr(core_mod, "KnowledgeArbitrator", _Noop)
    monkeypatch.setattr(core_mod, "KnowledgeFeedbackRecorder", _Noop)
    monkeypatch.setattr(core_mod, "AutoMemoryExtractor", lambda *_a, **_k: _Noop())
    monkeypatch.setattr(core_mod, "MemorySynthesizer", lambda *_a, **_k: _Noop())
    monkeypatch.setattr(core_mod, "MetaCognitionSystem", lambda *_a, **_k: _Noop())
    monkeypatch.setattr(core_mod, "ensure_memory_directories", lambda *_a, **_k: None)
    monkeypatch.setattr(core_mod, "get_crypto_manager", lambda *_a, **_k: _Noop())
    monkeypatch.setattr(core_mod, "get_shadow_system", lambda *_a, **_k: SimpleNamespace(record_data=lambda **_kw: None))
    monkeypatch.setattr(core_mod.MemoryCore, "_sync_github_skills_async", lambda self: None)
    monkeypatch.setattr(core_mod.MemoryCore, "_restore_short_term_memory", lambda self: None)
    monkeypatch.setattr(core_mod.MemoryCore, "_load_write_fail_state", lambda self: None)
    monkeypatch.setattr(core_mod.MemoryCore, "_startup_health_card_probe", lambda self: None)

    # Local import in __init__: from ms8.app.pipeline import MemoryAdmissionEngine
    import ms8.app.pipeline as app_pipeline

    monkeypatch.setattr(app_pipeline, "MemoryAdmissionEngine", lambda *_a, **_k: _Noop())


def test_core_init_fast_start_llm_disabled(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(core_mod, "get_config", lambda: _cfg(tmp_path))
    _patch_core_init_dependencies(monkeypatch)
    monkeypatch.setenv("OPENCLAW_MEMORY_FAST_START", "1")

    core = core_mod.MemoryCore(llm_enabled=False)
    assert core.llm_enabled is False
    assert core.meta_cognition is None
    assert hasattr(core, "short_term_memory")


def test_core_init_llm_fallback_to_basic_engine(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(core_mod, "get_config", lambda: _cfg(tmp_path))
    _patch_core_init_dependencies(monkeypatch)
    monkeypatch.setenv("OPENCLAW_MEMORY_FAST_START", "1")

    class _Boom:
        def __init__(self, *_a, **_k):
            raise RuntimeError("llm bootstrap failed")

    monkeypatch.setattr(core_mod, "EnhancedSelfImprovement", _Boom)
    core = core_mod.MemoryCore(llm_enabled=True)
    assert core.llm_enabled is False
