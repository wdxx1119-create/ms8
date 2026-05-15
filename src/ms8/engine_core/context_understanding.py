"""
Context Understanding System - 上下文理解系统
Based on Letta's context understanding architecture

实现功能:
1. 深度上下文理解
2. 指代解析
3. 隐含意思理解
4. 跨时间关联
5. 用户意图推理
"""

import hashlib
import json
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from typing import Any

from .config import get_config
from .local_llm import LLMConfig, LocalLLM


class IntentType(Enum):
    """意图类型"""

    QUESTION = "question"  # 询问
    REQUEST = "request"  # 请求
    STATEMENT = "statement"  # 陈述
    COMMAND = "command"  # 命令
    FEEDBACK = "feedback"  # 反馈
    EMOTION = "emotion"  # 情感表达


@dataclass
class ContextUnderstanding:
    """上下文理解结果"""

    id: str
    conversation_id: str
    current_topic: str  # 当前话题
    topic_evolution: list[str]  # 话题演进
    user_intent: IntentType  # 用户意图
    implicit_needs: list[str]  # 未明说的需求
    references: list[dict]  # 指代内容
    emotional_state: str | None  # 情感状态
    cross_time_links: list[dict]  # 跨时间关联
    confidence: float  # 置信度
    timestamp: datetime

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "conversation_id": self.conversation_id,
            "current_topic": self.current_topic,
            "topic_evolution": self.topic_evolution,
            "user_intent": self.user_intent.value,
            "implicit_needs": self.implicit_needs,
            "references": self.references,
            "emotional_state": self.emotional_state,
            "cross_time_links": self.cross_time_links,
            "confidence": self.confidence,
            "timestamp": self.timestamp.isoformat(),
        }

    @classmethod
    def from_dict(cls, data: dict) -> "ContextUnderstanding":
        return cls(
            id=data["id"],
            conversation_id=data["conversation_id"],
            current_topic=data["current_topic"],
            topic_evolution=data["topic_evolution"],
            user_intent=IntentType(data["user_intent"]),
            implicit_needs=data["implicit_needs"],
            references=data.get("references", []),
            emotional_state=data.get("emotional_state"),
            cross_time_links=data.get("cross_time_links", []),
            confidence=data["confidence"],
            timestamp=datetime.fromisoformat(data["timestamp"]),
        )


@dataclass
class Reference:
    """指代内容"""

    text: str  # 指代词 (如"这个"、"那个")
    referent: str  # 指代对象
    confidence: float  # 置信度
    context: str  # 上下文


class ContextUnderstandingSystem:
    """
    上下文理解系统

    基于 Letta 的上下文理解架构，使用 LLM 进行深度理解
    """

    def __init__(self, llm: LocalLLM | None = None, config: LLMConfig | None = None):
        """
        初始化上下文理解系统

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
            except (ImportError, OSError, RuntimeError, TypeError, ValueError):
                # Keep module available with heuristic fallbacks when local LLM runtime is unavailable.
                self.llm = None
        self.understandings: dict[str, ContextUnderstanding] = {}
        self.conversation_history: list[dict[str, Any]] = []

        # 存储路径
        config_data = get_config()
        self.context_file = config_data["memory_dir"] / "context_understandings.json"
        self._load_contexts()

    async def _chat(self, prompt: str, *, temperature: float, max_tokens: int) -> str:
        if self.llm is None:
            raise RuntimeError("llm unavailable")
        messages = [{"role": "user", "content": prompt}]
        return await self.llm.chat(messages, temperature=temperature, max_tokens=max_tokens)

    def _load_contexts(self) -> None:
        """从文件加载上下文理解"""
        if self.context_file.exists():
            try:
                with open(self.context_file, encoding="utf-8") as f:
                    data = json.load(f)
                    self.understandings = {k: ContextUnderstanding.from_dict(v) for k, v in data.items()}
            except (OSError, TypeError, ValueError, json.JSONDecodeError) as e:
                print(f"[ContextUnderstanding] Error loading contexts: {e}")

    def _save_contexts(self) -> None:
        """保存上下文理解到文件"""
        self.context_file.parent.mkdir(parents=True, exist_ok=True)
        with open(self.context_file, "w", encoding="utf-8") as f:
            json.dump(
                {k: v.to_dict() for k, v in self.understandings.items()},
                f,
                indent=2,
                ensure_ascii=False,
            )

    def add_conversation(self, role: str, content: str) -> None:
        """
        添加对话到历史

        Args:
            role: 角色 (user/assistant)
            content: 内容
        """
        self.conversation_history.append({"role": role, "content": content, "timestamp": datetime.now()})

        # 限制历史长度
        if len(self.conversation_history) > 100:
            self.conversation_history = self.conversation_history[-100:]

    async def understand_context(
        self, conversations: list[dict[str, Any]] | None = None
    ) -> ContextUnderstanding:
        """
        理解当前上下文

        Args:
            conversations: 对话历史 (可选，默认使用内部历史)

        Returns:
            上下文理解结果
        """
        if conversations is None:
            conversations = self.conversation_history

        # 1. 识别当前话题
        current_topic = await self._identify_current_topic(conversations)

        # 2. 分析话题演进
        topic_evolution = await self._analyze_topic_evolution(conversations)

        # 3. 识别用户意图
        user_intent = await self._identify_user_intent(conversations)

        # 4. 推断未明说的需求
        implicit_needs = await self._infer_implicit_needs(conversations)

        # 5. 解析指代内容
        references = await self._resolve_references(conversations)

        # 6. 识别情感状态
        emotional_state = await self._identify_emotional_state(conversations)

        # 7. 查找跨时间关联
        cross_time_links = self._find_cross_time_links(conversations)

        # 8. 计算置信度
        confidence = self._calculate_confidence(current_topic, user_intent, references)

        # 创建理解结果
        understanding = ContextUnderstanding(
            id=hashlib.md5(f"{datetime.now()}".encode()).hexdigest()[:12],
            conversation_id=self._get_conversation_id(conversations),
            current_topic=current_topic,
            topic_evolution=topic_evolution,
            user_intent=user_intent,
            implicit_needs=implicit_needs,
            references=references,
            emotional_state=emotional_state,
            cross_time_links=cross_time_links,
            confidence=confidence,
            timestamp=datetime.now(),
        )

        # 保存
        self.understandings[understanding.id] = understanding
        self._save_contexts()

        return understanding

    async def _identify_current_topic(self, conversations: list[dict]) -> str:
        """识别当前话题"""
        if not conversations:
            return "未知话题"

        # 获取最近的对话
        recent = conversations[-10:]
        formatted = self._format_conversations(recent)

        # 使用 LLM 识别话题
        prompt = f"""分析以下对话，识别当前讨论的核心话题:

{formatted}

请用一句话概括当前话题 (不超过 20 字):
"""

        try:
            response = await self._chat(prompt, temperature=0.3, max_tokens=100)
            return response.strip()
        except (RuntimeError, TypeError, ValueError):
            # 回退到简单提取
            last_message = conversations[-1].get("content", "")
            return last_message[:50] if last_message else "未知话题"

    async def _analyze_topic_evolution(self, conversations: list[dict]) -> list[str]:
        """分析话题演进"""
        if len(conversations) < 5:
            return []

        # 分段分析
        segments = [conversations[i : i + 10] for i in range(0, len(conversations), 10)]

        topics = []
        for segment in segments:
            formatted = self._format_conversations(segment)

            prompt = f"""分析以下对话片段，提取讨论的话题:

{formatted}

请用 3-5 个关键词概括话题:
"""

            try:
                response = await self._chat(prompt, temperature=0.3, max_tokens=100)
                topics.append(response.strip())
            except (RuntimeError, TypeError, ValueError) as exc:
                print(f"[ContextUnderstanding] Topic extraction failed for segment: {exc}")

        return topics

    async def _identify_user_intent(self, conversations: list[dict]) -> IntentType:
        """识别用户意图"""
        if not conversations:
            return IntentType.STATEMENT

        # 获取最近的用户消息
        user_messages = [c for c in conversations if c.get("role") == "user"][-5:]

        if not user_messages:
            return IntentType.STATEMENT

        # 合并最近用户消息
        combined = " ".join(m.get("content", "") for m in user_messages)

        # 使用 LLM 识别意图
        prompt = f"""分析以下用户消息，判断用户意图:

"{combined}"

意图类型:
- question: 询问问题
- request: 请求帮助
- statement: 陈述事实
- command: 发出命令
- feedback: 提供反馈
- emotion: 表达情感

请只返回意图类型 (question/request/statement/command/feedback/emotion):
"""

        try:
            response = await self._chat(prompt, temperature=0.3, max_tokens=50)
            intent_str = response.strip().lower()

            # 映射到枚举
            mapping = {
                "question": IntentType.QUESTION,
                "request": IntentType.REQUEST,
                "statement": IntentType.STATEMENT,
                "command": IntentType.COMMAND,
                "feedback": IntentType.FEEDBACK,
                "emotion": IntentType.EMOTION,
            }

            return mapping.get(intent_str, IntentType.STATEMENT)
        except (RuntimeError, TypeError, ValueError):
            return IntentType.STATEMENT

    async def _infer_implicit_needs(self, conversations: list[dict]) -> list[str]:
        """推断未明说的需求"""
        if not conversations:
            return []

        formatted = self._format_conversations(conversations[-20:])

        prompt = f"""分析以下对话，推断用户未明说的需求:

{formatted}

请列出用户可能想要但没有直接说出的需求 (最多 5 条):
1. ...
2. ...
3. ...

请以 JSON 数组格式返回:
["需求 1", "需求 2", "需求 3"]
"""

        try:
            response = await self._chat(prompt, temperature=0.5, max_tokens=500)

            # 解析 JSON
            import re

            json_match = re.search(r"\[[\s\S]*\]", response)
            if json_match:
                needs = json.loads(json_match.group())
                return needs[:5]
        except (RuntimeError, TypeError, ValueError, json.JSONDecodeError) as exc:
            print(f"[ContextUnderstanding] Memory needs parsing failed: {exc}")

        return []

    async def _resolve_references(self, conversations: list[dict]) -> list[dict]:
        """解析指代内容"""
        references = []

        # 指代词列表
        pronouns = [
            "这个",
            "那个",
            "这些",
            "那些",
            "它",
            "他",
            "她",
            "这样",
            "那样",
            "前者",
            "后者",
            "上述",
            "以下",
        ]

        # 查找包含指代词的消息
        for i, conv in enumerate(conversations):
            content = conv.get("content", "")

            for pronoun in pronouns:
                if pronoun in content:
                    # 使用 LLM 解析指代
                    referent = await self._resolve_single_reference(pronoun, content, conversations[:i])

                    if referent:
                        references.append(
                            {
                                "text": pronoun,
                                "referent": referent,
                                "confidence": 0.8,
                                "context": content[:100],
                                "conversation_index": i,
                            }
                        )

        return references

    async def _resolve_single_reference(
        self, pronoun: str, content: str, previous_conversations: list[dict]
    ) -> str | None:
        """解析单个指代词"""
        # 获取上下文
        context = self._format_conversations(previous_conversations[-10:])

        prompt = f"""在以下对话中，"{pronoun}"指代什么？

上下文:
{context}

包含指代词的句子:
{content}

请直接回答"{pronoun}"指代的内容 (不超过 20 字):
"""

        try:
            response = await self._chat(prompt, temperature=0.3, max_tokens=100)
            return response.strip()
        except (RuntimeError, TypeError, ValueError):
            return None

    async def _identify_emotional_state(self, conversations: list[dict]) -> str | None:
        """识别情感状态"""
        if not conversations:
            return None

        # 获取用户消息
        user_messages = [c.get("content", "") for c in conversations if c.get("role") == "user"][-10:]

        if not user_messages:
            return None

        combined = " ".join(user_messages)

        prompt = f"""分析以下用户消息的情感状态:

"{combined}"

情感状态可能是:
- 平静
- 开心
- 焦虑
- 困惑
- 沮丧
- 兴奋
- 生气
- 中性

请只返回一个情感状态词:
"""

        try:
            response = await self._chat(prompt, temperature=0.3, max_tokens=50)
            return response.strip()
        except (RuntimeError, TypeError, ValueError):
            return None

    def _find_cross_time_links(self, conversations: list[dict]) -> list[dict]:
        """查找跨时间关联"""
        links = []

        # 提取实体和主题
        entities = self._extract_entities(conversations)

        # 查找重复出现的实体
        for entity, occurrences in entities.items():
            if len(occurrences) > 1:
                links.append(
                    {
                        "entity": entity,
                        "occurrences": len(occurrences),
                        "first_mention": occurrences[0]["index"],
                        "last_mention": occurrences[-1]["index"],
                        "contexts": [o["context"] for o in occurrences[:3]],
                    }
                )

        return links

    def _extract_entities(self, conversations: list[dict]) -> dict[str, list[dict]]:
        """提取实体"""
        entities = defaultdict(list)

        # 简化实现：提取名词短语
        for i, conv in enumerate(conversations):
            content = conv.get("content", "")

            # 简单提取：提取 2-4 字的词组 (可能是专有名词)
            words = content.split()
            for word in words:
                if 2 <= len(word) <= 4 and word.isalpha():
                    entities[word].append({"index": i, "context": content[:50]})

        return entities

    def _format_conversations(self, conversations: list[dict]) -> str:
        """格式化对话"""
        lines = []
        for conv in conversations:
            role = "用户" if conv.get("role") == "user" else "AI"
            content = conv.get("content", "")[:200]
            if content:
                lines.append(f"{role}: {content}")
        return "\n".join(lines)

    def _get_conversation_id(self, conversations: list[dict]) -> str:
        """生成对话 ID"""
        content = "".join(c.get("content", "") for c in conversations[-10:])
        return hashlib.md5(content.encode()).hexdigest()[:12]

    def _calculate_confidence(self, topic: str, intent: IntentType, references: list[dict]) -> float:
        """计算置信度"""
        confidence = 0.5

        # 话题识别置信度
        if topic and len(topic) > 3:
            confidence += 0.2

        # 意图识别置信度
        if intent != IntentType.STATEMENT:
            confidence += 0.15

        # 指代解析置信度
        if references:
            confidence += 0.15

        return min(1.0, confidence)

    def get_understanding_history(self, limit: int = 10) -> list[ContextUnderstanding]:
        """获取理解历史"""
        understandings = list(self.understandings.values())
        understandings.sort(key=lambda x: x.timestamp, reverse=True)
        return understandings[:limit]

    def get_topic_timeline(self) -> list[dict]:
        """获取话题时间线"""
        timeline = []

        for understanding in sorted(self.understandings.values(), key=lambda x: x.timestamp):
            timeline.append(
                {
                    "timestamp": understanding.timestamp.isoformat(),
                    "topic": understanding.current_topic,
                    "intent": understanding.user_intent.value,
                }
            )

        return timeline
