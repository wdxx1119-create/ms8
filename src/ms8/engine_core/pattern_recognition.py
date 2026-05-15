"""
Pattern Recognition System - 模式识别系统
Based on Letta's pattern detection architecture

实现功能:
1. LLM 驱动的模式识别
2. 情感分析
3. 行为模式识别
4. 决策模式分析
5. 时间模式识别
6. 跨对话关联
"""

import hashlib
import json
from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import Enum
from typing import Any

from .config import get_config
from .local_llm import LLMConfig, LocalLLM


class PatternType(Enum):
    """模式类型"""

    PREFERENCE = "preference"  # 偏好模式
    EMOTION = "emotion"  # 情感模式
    BEHAVIOR = "behavior"  # 行为模式
    DECISION = "decision"  # 决策模式
    TIME = "time"  # 时间模式
    COMMUNICATION = "communication"  # 沟通模式


class EmotionType(Enum):
    """情感类型"""

    POSITIVE = "positive"  # 正面
    NEGATIVE = "negative"  # 负面
    NEUTRAL = "neutral"  # 中性
    MIXED = "mixed"  # 混合


@dataclass
class Pattern:
    """模式数据结构"""

    id: str
    pattern_type: PatternType
    name: str
    description: str
    evidence: list[str]  # 支持证据 (对话片段)
    confidence: float  # 置信度 (0-1)
    frequency: int  # 出现频率
    first_seen: datetime  # 首次出现
    last_seen: datetime  # 最后出现
    metadata: dict[str, Any]  # 额外元数据

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "pattern_type": self.pattern_type.value,
            "name": self.name,
            "description": self.description,
            "evidence": self.evidence,
            "confidence": self.confidence,
            "frequency": self.frequency,
            "first_seen": self.first_seen.isoformat(),
            "last_seen": self.last_seen.isoformat(),
            "metadata": self.metadata,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "Pattern":
        return cls(
            id=data["id"],
            pattern_type=PatternType(data["pattern_type"]),
            name=data["name"],
            description=data["description"],
            evidence=data["evidence"],
            confidence=data["confidence"],
            frequency=data["frequency"],
            first_seen=datetime.fromisoformat(data["first_seen"]),
            last_seen=datetime.fromisoformat(data["last_seen"]),
            metadata=data.get("metadata", {}),
        )


@dataclass
class EmotionAnalysis:
    """情感分析结果"""

    emotion_type: EmotionType
    intensity: float  # 强度 (0-1)
    keywords: list[str]  # 情感关键词
    context: str  # 上下文
    confidence: float  # 置信度


class PatternRecognition:
    """
    模式识别系统

    基于 Letta 的模式识别架构，使用 LLM 进行深度分析
    """

    def __init__(self, llm: LocalLLM | None = None, config: LLMConfig | None = None):
        """
        初始化模式识别系统

        Args:
            llm: LocalLLM 实例
            config: LLM 配置
        """
        self.llm: LocalLLM | None
        if llm is not None:
            self.llm = llm
        else:
            try:
                self.llm = LocalLLM(config or LLMConfig())
            except (RuntimeError, OSError, ImportError, ValueError):
                # Keep module available with rule/statistics-only analysis when local LLM runtime is unavailable.
                self.llm = None
        self.patterns: dict[str, Pattern] = {}
        self.emotion_history: list[EmotionAnalysis] = []

        # 模式存储路径
        config_data = get_config()
        self.patterns_file = config_data["memory_dir"] / "patterns.json"
        self._load_patterns()

    def _load_patterns(self) -> None:
        """从文件加载模式"""
        if self.patterns_file.exists():
            try:
                with open(self.patterns_file, encoding="utf-8") as f:
                    data = json.load(f)
                    self.patterns = {k: Pattern.from_dict(v) for k, v in data.items()}
            except (OSError, TypeError, ValueError, json.JSONDecodeError) as e:
                print(f"[PatternRecognition] Error loading patterns: {e}")

    def _save_patterns(self) -> None:
        """保存模式到文件"""
        self.patterns_file.parent.mkdir(parents=True, exist_ok=True)
        with open(self.patterns_file, "w", encoding="utf-8") as f:
            json.dump({k: v.to_dict() for k, v in self.patterns.items()}, f, indent=2, ensure_ascii=False)

    async def analyze_conversations(
        self, conversations: list[dict], analyze_emotion: bool = True, analyze_patterns: bool = True
    ) -> dict:
        """
        分析对话，识别模式

        Args:
            conversations: 对话历史列表
            analyze_emotion: 是否分析情感
            analyze_patterns: 是否分析模式

        Returns:
            分析结果字典
        """
        results: dict[str, Any] = {"patterns": [], "emotions": [], "statistics": {}}

        # 1. LLM 驱动的模式识别
        if analyze_patterns:
            patterns = await self._llm_detect_patterns(conversations)
            results["patterns"] = patterns

        # 2. 情感分析
        if analyze_emotion:
            emotions = await self._llm_analyze_emotions(conversations)
            results["emotions"] = emotions

        # 3. 统计分析
        results["statistics"] = self._calculate_statistics(conversations)

        # 4. 更新和保存模式
        self._update_patterns(results)
        self._save_patterns()

        return results

    async def _llm_detect_patterns(self, conversations: list[dict]) -> list[Pattern]:
        """
        使用 LLM 检测模式

        Args:
            conversations: 对话历史

        Returns:
            识别的模式列表
        """
        if self.llm is None:
            # Heuristic fallback to keep pattern module useful without local LLM runtime.
            user_messages = [
                c.get("content", "") for c in conversations if c.get("role") == "user" and c.get("content")
            ]
            if len(user_messages) < 3:
                return []
            combined = " ".join(user_messages).lower()
            pattern_type = PatternType.BEHAVIOR
            name = "high_interaction_frequency"
            description = "用户在连续会话中保持高频互动，适合保留连续上下文。"
            if ("配置" in combined or "config" in combined) and ("决定" in combined or "decision" in combined):
                pattern_type = PatternType.DECISION
                name = "config_decision_coupling"
                description = "配置变更与决策讨论在会话中频繁共现。"
            pid = hashlib.md5(name.encode("utf-8")).hexdigest()[:12]
            return [
                Pattern(
                    id=pid,
                    pattern_type=pattern_type,
                    name=name,
                    description=description,
                    evidence=user_messages[-3:],
                    confidence=0.55,
                    frequency=len(user_messages),
                    first_seen=datetime.now(),
                    last_seen=datetime.now(),
                    metadata={"fallback": True},
                )
            ]

        # 格式化对话
        formatted_conversations = self._format_conversations(conversations)

        # 构建提示词
        prompt = f"""你是一个专业的用户行为分析师。请分析以下对话历史，识别用户的行为模式。

对话历史 (最近{len(conversations)}条):
{formatted_conversations}

请识别以下模式:

1. **偏好模式**: 用户反复提到的偏好 (如代码风格、工具偏好、语言偏好等)
2. **行为模式**: 用户的典型行为 (如提问方式、决策风格、工作习惯等)
3. **决策模式**: 用户如何做决定 (如快速决策、谨慎分析、依赖他人意见等)
4. **时间模式**: 用户的时间相关习惯 (如工作时间、响应速度、截止日期偏好等)
5. **沟通模式**: 用户的沟通风格 (如直接、委婉、详细、简洁等)

对于每个识别的模式，请提供:
- 模式名称
- 模式类型 (preference/behavior/decision/time/communication)
- 详细描述
- 支持证据 (引用具体对话)
- 置信度 (0-1)
- 出现频率

请以 JSON 格式返回:
{{
  "patterns": [
    {{
      "name": "模式名称",
      "pattern_type": "模式类型",
      "description": "详细描述",
      "evidence": ["证据 1", "证据 2"],
      "confidence": 0.9,
      "frequency": 5
    }}
  ]
}}
"""

        try:
            # 调用 LLM
            messages = [{"role": "user", "content": prompt}]
            response = await self.llm.chat(messages, temperature=0.5, max_tokens=2000)

            # 解析响应
            patterns = self._parse_patterns_response(response)
            return patterns

        except (RuntimeError, TypeError, ValueError, OSError) as e:
            print(f"[PatternRecognition] LLM pattern detection error: {e}")
            return []

    async def _llm_analyze_emotions(self, conversations: list[dict]) -> list[EmotionAnalysis]:
        """
        使用 LLM 分析情感

        Args:
            conversations: 对话历史

        Returns:
            情感分析结果列表
        """
        emotions: list[EmotionAnalysis] = []
        if self.llm is None:
            return emotions

        # 分析最近的对话 (最多 20 条)
        recent_conversations = conversations[-20:]

        for conv in recent_conversations:
            content = conv.get("content", "")
            if not content or conv.get("role") != "user":
                continue

            # 构建提示词
            prompt = f"""分析以下文本的情感倾向:

"{content}"

请判断:
1. 情感类型 (positive/negative/neutral/mixed)
2. 情感强度 (0-1, 1 为最强烈)
3. 情感关键词 (提取表达情感的词语)
4. 置信度 (0-1)

请以 JSON 格式返回:
{{
  "emotion_type": "positive",
  "intensity": 0.8,
  "keywords": ["关键词 1", "关键词 2"],
  "confidence": 0.9
}}
"""

            try:
                messages = [{"role": "user", "content": prompt}]
                response = await self.llm.chat(messages, temperature=0.3, max_tokens=500)

                # 解析响应
                emotion_data = self._parse_emotion_response(response, content)
                if emotion_data:
                    emotions.append(emotion_data)
                    self.emotion_history.append(emotion_data)

            except (RuntimeError, TypeError, ValueError, OSError) as e:
                print(f"[PatternRecognition] Emotion analysis error: {e}")

        return emotions

    def _parse_patterns_response(self, response: str) -> list[Pattern]:
        """解析 LLM 模式识别响应"""
        patterns = []

        try:
            # 尝试提取 JSON
            import re

            json_match = re.search(r"\{[\s\S]*\}", response)
            if json_match:
                data = json.loads(json_match.group())

                for p in data.get("patterns", []):
                    pattern = Pattern(
                        id=hashlib.md5(f"{p['name']}{datetime.now()}".encode()).hexdigest()[:12],
                        pattern_type=PatternType(p.get("pattern_type", "preference")),
                        name=p["name"],
                        description=p["description"],
                        evidence=p.get("evidence", []),
                        confidence=p.get("confidence", 0.5),
                        frequency=p.get("frequency", 1),
                        first_seen=datetime.now(),
                        last_seen=datetime.now(),
                        metadata={},
                    )
                    patterns.append(pattern)
        except (TypeError, ValueError, KeyError, json.JSONDecodeError) as e:
            print(f"[PatternRecognition] Parse patterns error: {e}")

        return patterns

    def _parse_emotion_response(self, response: str, context: str) -> EmotionAnalysis | None:
        """解析 LLM 情感分析响应"""
        try:
            import re

            json_match = re.search(r"\{[\s\S]*\}", response)
            if json_match:
                data = json.loads(json_match.group())

                return EmotionAnalysis(
                    emotion_type=EmotionType(data.get("emotion_type", "neutral")),
                    intensity=data.get("intensity", 0.5),
                    keywords=data.get("keywords", []),
                    context=context,
                    confidence=data.get("confidence", 0.5),
                )
        except (TypeError, ValueError, KeyError, json.JSONDecodeError) as e:
            print(f"[PatternRecognition] Parse emotion error: {e}")

        return None

    def _format_conversations(self, conversations: list[dict]) -> str:
        """格式化对话用于分析"""
        lines = []
        for conv in conversations[-50:]:  # 最近 50 条
            role = "用户" if conv.get("role") == "user" else "AI"
            content = conv.get("content", "")[:200]  # 限制长度
            if content:
                lines.append(f"{role}: {content}")
        return "\n".join(lines)

    def _calculate_statistics(self, conversations: list[dict[str, Any]]) -> dict[str, Any]:
        """计算统计信息"""
        stats: dict[str, Any] = {
            "total_conversations": len(conversations),
            "user_messages": 0,
            "ai_messages": 0,
            "avg_user_message_length": 0,
            "time_range": {"start": None, "end": None},
        }

        total_length = 0
        for conv in conversations:
            if conv.get("role") == "user":
                stats["user_messages"] += 1
                total_length += len(conv.get("content", ""))
            else:
                stats["ai_messages"] += 1

        if stats["user_messages"] > 0:
            stats["avg_user_message_length"] = total_length / stats["user_messages"]

        return stats

    def _update_patterns(self, results: dict) -> None:
        """更新模式库"""
        for pattern in results.get("patterns", []):
            if pattern.id in self.patterns:
                # 更新现有模式
                existing = self.patterns[pattern.id]
                existing.frequency += pattern.frequency
                existing.last_seen = datetime.now()
                existing.confidence = (existing.confidence + pattern.confidence) / 2
                existing.evidence.extend(pattern.evidence[:3])  # 限制证据数量
            else:
                # 添加新模式
                self.patterns[pattern.id] = pattern

    def get_patterns(
        self, pattern_type: PatternType | None = None, min_confidence: float = 0.5, limit: int = 10
    ) -> list[Pattern]:
        """
        获取模式

        Args:
            pattern_type: 模式类型过滤
            min_confidence: 最小置信度
            limit: 返回数量限制

        Returns:
            模式列表
        """
        patterns = list(self.patterns.values())

        # 过滤
        if pattern_type:
            patterns = [p for p in patterns if p.pattern_type == pattern_type]

        patterns = [p for p in patterns if p.confidence >= min_confidence]

        # 排序 (按置信度和频率)
        patterns.sort(key=lambda x: (x.confidence, x.frequency), reverse=True)

        return patterns[:limit]

    def get_emotion_trend(self, days: int = 7) -> dict:
        """
        获取情感趋势

        Args:
            days: 天数

        Returns:
            情感趋势统计
        """
        datetime.now() - timedelta(days=days)
        recent_emotions = [
            e
            for e in self.emotion_history
            if e.context  # 简单过滤，实际应该有时间戳
        ]

        if not recent_emotions:
            return {"dominant_emotion": "neutral", "average_intensity": 0, "trend": "stable"}

        # 统计情感类型
        emotion_counts = Counter(e.emotion_type.value for e in recent_emotions)
        dominant = emotion_counts.most_common(1)[0][0] if emotion_counts else "neutral"

        # 平均强度
        avg_intensity = sum(e.intensity for e in recent_emotions) / len(recent_emotions)

        return {
            "dominant_emotion": dominant,
            "average_intensity": avg_intensity,
            "trend": "stable",  # 简化，实际应该分析趋势
            "total_analyzed": len(recent_emotions),
        }

    def detect_cross_conversation_links(self, conversations: list[dict]) -> list[dict]:
        """
        检测跨对话关联

        Args:
            conversations: 对话历史

        Returns:
            关联列表
        """
        links = []

        # 提取主题和实体
        self._extract_topics(conversations)

        # 查找相关对话
        for i, conv in enumerate(conversations):
            content = conv.get("content", "")
            related = self._find_related_conversations(content, conversations[:i])

            if related:
                links.append(
                    {
                        "conversation_index": i,
                        "content": content[:100],
                        "related_to": [r["index"] for r in related],
                        "similarities": [r["similarity"] for r in related],
                    }
                )

        return links

    def _extract_topics(self, conversations: list[dict]) -> list[str]:
        """提取对话主题"""
        # 简化实现，实际应该使用 NLP 或 LLM
        topics = []
        for conv in conversations:
            content = conv.get("content", "")
            if content and len(content) > 20:
                topics.append(content[:50])
        return topics

    def _find_related_conversations(
        self, content: str, previous_conversations: list[dict], limit: int = 3
    ) -> list[dict]:
        """查找相关对话"""
        # 简化实现，使用关键词匹配
        related = []
        content_words = set(content.lower().split())

        for i, conv in enumerate(previous_conversations):
            conv_content = conv.get("content", "")
            conv_words = set(conv_content.lower().split())

            # 计算重叠度
            overlap = len(content_words & conv_words)
            if overlap > 3:  # 至少 3 个共同词
                related.append(
                    {
                        "index": i,
                        "content": conv_content[:100],
                        "similarity": overlap / max(len(content_words), len(conv_words)),
                    }
                )

        related.sort(key=lambda x: x["similarity"], reverse=True)
        return related[:limit]
