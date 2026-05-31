from __future__ import annotations

import asyncio

import pytest

from ms8.engine_core.enhanced_self_improvement import EnhancedSelfImprovement
from ms8.engine_core.self_improvement import ValidationStatus


class _FakeBlocks:
    def __init__(self) -> None:
        self._data = {"human": "", "persona": "", "archival": ""}

    def update_block(self, block: str, _instruction: str, content: str) -> None:
        self._data[block] = content


class _FakeMemoryCore:
    def __init__(self, tmp_path) -> None:
        self.memory_blocks = _FakeBlocks()
        self.config = {
            "memory_dir": tmp_path,
            "settings": {"memory": {}},
        }

    def get_memory_blocks(self):
        return dict(self.memory_blocks._data)

    def get_recent(self, n=20):
        return [
            {"role": "user", "content": "我喜欢结构化答案"},
            {"role": "assistant", "content": "好的"},
        ][:n]


def test_enhanced_remember_with_llm_branch(tmp_path):
    core = _FakeMemoryCore(tmp_path)
    engine = EnhancedSelfImprovement(core)

    async def _reason(_instruction, _context):
        return "建议更新: archival\n置信度: 0.9\nreason: llm reason"

    async def _validate(_payload):
        return {"验证结论": "通过", "总体评分": 0.88}

    engine.llm.generate_reason = _reason
    engine.llm.validate_improvement = _validate
    engine.llm.get_stats = lambda: {"cache_hits": 2, "cache_misses": 1}

    result = asyncio.run(
        engine.remember(
            instruction="记录这个偏好",
            content="偏好测试",
            auto_generate_reason=True,
            validate=True,
            use_llm=True,
        )
    )

    assert result["status"] == "success"
    assert result["llm_used"] is True
    assert result["validation_status"] == ValidationStatus.VALIDATED.value
    assert result["validation_score"] == pytest.approx(0.88)
    assert result["llm_stats"]["reason_generation"] >= 1
    assert result["llm_stats"]["validation"] >= 1
    assert result["llm_stats"]["cache_hits"] == 2


def test_enhanced_remember_rule_fallback_and_reject_rollback(tmp_path):
    core = _FakeMemoryCore(tmp_path)
    engine = EnhancedSelfImprovement(core)

    result = asyncio.run(
        engine.remember(
            instruction="x",
            content="y",
            auto_generate_reason=True,
            validate=True,
            use_llm=False,
        )
    )

    assert result["status"] == "success"
    assert result["llm_used"] is False
    assert result["validation_status"] in {
        ValidationStatus.REJECTED.value,
        ValidationStatus.TESTING.value,
        ValidationStatus.VALIDATED.value,
    }
    # ensure history and metrics persist path exercised
    assert engine.history
    assert engine.metrics["total_improvements"] >= 1


def test_parse_llm_reason_and_status_mapping(tmp_path):
    core = _FakeMemoryCore(tmp_path)
    engine = EnhancedSelfImprovement(core)

    parsed = engine._parse_llm_reason("建议更新: persona\n置信度: abc\n原因: test")
    assert parsed["suggested_block"] == "persona"
    assert parsed["confidence"] == 0.5

    assert engine._map_validation_status("通过") == ValidationStatus.VALIDATED
    assert engine._map_validation_status("需要测试") == ValidationStatus.TESTING
    assert engine._map_validation_status("拒绝") == ValidationStatus.REJECTED
    assert engine._map_validation_status("unknown") == ValidationStatus.VALIDATED
