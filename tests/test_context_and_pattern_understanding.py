from __future__ import annotations

import asyncio
from datetime import datetime

from ms8.engine_core import context_understanding as cu
from ms8.engine_core import pattern_recognition as pr


def _mock_cfg(tmp_path):
    return {"memory_dir": tmp_path}


def test_context_understanding_roundtrip():
    item = cu.ContextUnderstanding(
        id="u1",
        conversation_id="c1",
        current_topic="测试话题",
        topic_evolution=["a", "b"],
        user_intent=cu.IntentType.REQUEST,
        implicit_needs=["need1"],
        references=[{"text": "这个", "referent": "系统"}],
        emotional_state="平静",
        cross_time_links=[{"entity": "ms8"}],
        confidence=0.9,
        timestamp=datetime.now(),
    )
    restored = cu.ContextUnderstanding.from_dict(item.to_dict())
    assert restored.id == "u1"
    assert restored.user_intent == cu.IntentType.REQUEST
    assert restored.current_topic == "测试话题"


def test_context_helpers_and_confidence(monkeypatch, tmp_path):
    monkeypatch.setattr(cu, "get_config", lambda: _mock_cfg(tmp_path))
    sys = cu.ContextUnderstandingSystem(llm=None)
    conversations = [
        {"role": "user", "content": "beta zeta"},
        {"role": "assistant", "content": "ok"},
        {"role": "user", "content": "beta go"},
    ]
    formatted = sys._format_conversations(conversations)
    assert "用户:" in formatted
    assert sys._get_conversation_id(conversations)
    entities = sys._extract_entities(conversations)
    assert "beta" in entities
    links = sys._find_cross_time_links(conversations)
    assert links and links[0]["entity"] == "beta"
    assert sys._calculate_confidence("topic", cu.IntentType.REQUEST, [{"r": 1}]) == 1.0
    assert sys._calculate_confidence("", cu.IntentType.STATEMENT, []) == 0.5


def test_context_async_fallback_paths(monkeypatch, tmp_path):
    monkeypatch.setattr(cu, "get_config", lambda: _mock_cfg(tmp_path))
    sys = cu.ContextUnderstandingSystem(llm=None)
    sys.llm = None
    conversations = [
        {"role": "assistant", "content": "收到"},
        {"role": "user", "content": "这是一个很长的最后消息用于话题回退"},
    ]
    topic = asyncio.run(sys._identify_current_topic(conversations))
    assert "这是一个很长的最后消息" in topic
    intent = asyncio.run(sys._identify_user_intent(conversations))
    assert intent == cu.IntentType.STATEMENT
    needs = asyncio.run(sys._infer_implicit_needs(conversations))
    assert needs == []
    ref = asyncio.run(sys._resolve_single_reference("这个", "这个怎么做", conversations))
    assert ref is None
    emo = asyncio.run(sys._identify_emotional_state(conversations))
    assert emo is None


def test_understand_context_end_to_end(monkeypatch, tmp_path):
    monkeypatch.setattr(cu, "get_config", lambda: _mock_cfg(tmp_path))
    sys = cu.ContextUnderstandingSystem(llm=None)
    sys.add_conversation("user", "我们先聊系统配置")
    sys.add_conversation("assistant", "好的")
    sys.add_conversation("user", "这个策略怎么改")
    item = asyncio.run(sys.understand_context())
    assert item.id
    assert item.conversation_id
    assert item.current_topic
    assert item.user_intent in {cu.IntentType.STATEMENT, cu.IntentType.REQUEST}
    history = sys.get_understanding_history()
    assert history and history[0].id == item.id
    timeline = sys.get_topic_timeline()
    assert timeline and timeline[0]["topic"] == item.current_topic


def test_pattern_roundtrip_and_basic_helpers(monkeypatch, tmp_path):
    monkeypatch.setattr(pr, "get_config", lambda: _mock_cfg(tmp_path))
    pattern = pr.Pattern(
        id="p1",
        pattern_type=pr.PatternType.BEHAVIOR,
        name="n",
        description="d",
        evidence=["e1"],
        confidence=0.7,
        frequency=2,
        first_seen=datetime.now(),
        last_seen=datetime.now(),
        metadata={"x": 1},
    )
    restored = pr.Pattern.from_dict(pattern.to_dict())
    assert restored.id == "p1"
    assert restored.pattern_type == pr.PatternType.BEHAVIOR
    rec = pr.PatternRecognition(llm=None)
    stats = rec._calculate_statistics(
        [{"role": "user", "content": "abc"}, {"role": "assistant", "content": "ok"}]
    )
    assert stats["total_conversations"] == 2
    assert stats["user_messages"] == 1
    assert rec._format_conversations([{"role": "user", "content": "hello"}]).startswith("用户:")


def test_pattern_parse_and_update(monkeypatch, tmp_path):
    monkeypatch.setattr(pr, "get_config", lambda: _mock_cfg(tmp_path))
    rec = pr.PatternRecognition(llm=None)
    parsed = rec._parse_patterns_response(
        '{"patterns":[{"name":"x","pattern_type":"behavior","description":"d","evidence":["a"],"confidence":0.8,"frequency":3}]}'
    )
    assert len(parsed) == 1
    assert parsed[0].pattern_type == pr.PatternType.BEHAVIOR
    bad = rec._parse_patterns_response("not-json")
    assert bad == []
    emotion = rec._parse_emotion_response(
        '{"emotion_type":"positive","intensity":0.8,"keywords":["k"],"confidence":0.9}', "ctx"
    )
    assert emotion is not None and emotion.emotion_type == pr.EmotionType.POSITIVE
    assert rec._parse_emotion_response("bad", "ctx") is None

    rec._update_patterns({"patterns": parsed})
    assert rec.patterns
    pid = parsed[0].id
    freq_before = rec.patterns[pid].frequency
    rec._update_patterns({"patterns": parsed})
    assert rec.patterns[pid].frequency >= freq_before


def test_pattern_filters_trend_and_links(monkeypatch, tmp_path):
    monkeypatch.setattr(pr, "get_config", lambda: _mock_cfg(tmp_path))
    rec = pr.PatternRecognition(llm=None)
    p1 = pr.Pattern(
        id="a1",
        pattern_type=pr.PatternType.BEHAVIOR,
        name="a",
        description="",
        evidence=[],
        confidence=0.6,
        frequency=2,
        first_seen=datetime.now(),
        last_seen=datetime.now(),
        metadata={},
    )
    p2 = pr.Pattern(
        id="a2",
        pattern_type=pr.PatternType.BEHAVIOR,
        name="b",
        description="",
        evidence=[],
        confidence=0.9,
        frequency=1,
        first_seen=datetime.now(),
        last_seen=datetime.now(),
        metadata={},
    )
    rec.patterns = {"a1": p1, "a2": p2}
    items = rec.get_patterns(pattern_type=pr.PatternType.BEHAVIOR, min_confidence=0.5, limit=10)
    assert items[0].id == "a2"

    trend = rec.get_emotion_trend()
    assert trend["dominant_emotion"] == "neutral"
    rec.emotion_history.append(
        pr.EmotionAnalysis(
            emotion_type=pr.EmotionType.NEGATIVE,
            intensity=0.7,
            keywords=["x"],
            context="ctx",
            confidence=0.8,
        )
    )
    trend2 = rec.get_emotion_trend()
    assert trend2["total_analyzed"] >= 1

    conversations = [
        {"content": "alpha beta gamma delta"},
        {"content": "none words"},
        {"content": "alpha beta gamma delta extra"},
    ]
    links = rec.detect_cross_conversation_links(conversations)
    assert links


def test_pattern_analyze_conversations_fallback(monkeypatch, tmp_path):
    monkeypatch.setattr(pr, "get_config", lambda: _mock_cfg(tmp_path))
    rec = pr.PatternRecognition(llm=None)
    conversations = [
        {"role": "user", "content": "我们开始讨论配置"},
        {"role": "user", "content": "我决定修改配置"},
        {"role": "user", "content": "配置相关决策要记录"},
    ]
    result = asyncio.run(rec.analyze_conversations(conversations))
    assert "patterns" in result
    assert "statistics" in result
