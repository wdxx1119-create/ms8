"""
Enhanced Self-Improvement System with Local LLM
完整集成本地 LLM 的自我改进系统
"""
import json
import hashlib
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Any
from dataclasses import dataclass, asdict
from enum import Enum

from .self_improvement import SelfImprovementEngine, ImprovementType, ValidationStatus, ImprovementRecord
from .local_llm import LocalLLM, LLMConfig

class EnhancedSelfImprovement(SelfImprovementEngine):
    """
    增强版自我改进引擎 - 集成本地 LLM
    
    新增功能:
    1. LLM 驱动的理由生成
    2. LLM 驱动的质量验证
    3. LLM 驱动的模式识别
    4. 智能模型路由
    5. 语义缓存优化
    6. 批处理优化
    """
    
    def __init__(self, memory_core, config: LLMConfig = None):
        """
        初始化增强版自我改进引擎
        
        Args:
            memory_core: MemoryCore 实例
            config: LLM 配置
        """
        # 调用父类初始化
        super().__init__(memory_core)
        
        # 初始化本地 LLM
        self.llm_config = config or LLMConfig()
        self.llm = LocalLLM(self.llm_config)
        self.llm_enabled = True
        
        # LLM 使用统计
        self.llm_stats = {
            'total_calls': 0,
            'reason_generation': 0,
            'validation': 0,
            'pattern_detection': 0,
            'cache_hits': 0,
            'cache_misses': 0
        }
    
    async def remember(self,
                      instruction: str,
                      content: str = None,
                      auto_generate_reason: bool = True,
                      validate: bool = True,
                      use_llm: bool = True) -> Dict:
        """
        增强版 /remember 命令 - 可选 LLM 增强
        
        Args:
            instruction: 要记住的内容
            content: 可选的具体内容
            auto_generate_reason: 是否自动生成理由
            validate: 是否验证改进
            use_llm: 是否使用 LLM (默认 True)
        
        Returns:
            Dict with status and details
        """
        self.llm_stats['total_calls'] += 1
        
        # 获取当前状态
        before_state = self.memory.get_memory_blocks()
        target_block = self._determine_target_block(instruction, content)
        
        # 生成理由
        if auto_generate_reason and use_llm and self.llm_enabled:
            self.llm_stats['reason_generation'] += 1
            context = self._normalize_context(self.memory.get_recent(n=20))
            reason = await self.llm.generate_reason(instruction, context)
            parsed_reason = self._parse_llm_reason(reason)
        elif auto_generate_reason:
            # 回退到规则生成
            context = self._normalize_context(self.memory.get_recent(n=20))
            reason = self._generate_edit_reason(instruction, context, target_block)
            parsed_reason = {'reason': reason}
        else:
            reason = "用户指令"
            parsed_reason = {'reason': reason}
        
        # 应用编辑
        self.memory.memory_blocks.update_block(target_block, instruction, content or instruction)
        
        # 获取编辑后状态
        after_state = self.memory.get_memory_blocks()
        
        # 验证
        validation_result = None
        if validate and use_llm and self.llm_enabled:
            self.llm_stats['validation'] += 1
            validation_result = await self.llm.validate_improvement({
                'description': instruction,
                'before_state': before_state,
                'after_state': after_state
            })
            validation_status = self._map_validation_status(validation_result.get('验证结论', '通过'))
            validation_score = validation_result.get('总体评分', 0.8)
            validation_details = validation_result
        elif validate:
            # 回退到规则验证
            validation_result = self._validate_improvement_simple(before_state, after_state, instruction)
            validation_status = validation_result['status']
            validation_score = validation_result.get('score', 0.7)
            validation_details = validation_result
        else:
            validation_status = ValidationStatus.VALIDATED
            validation_score = 1.0
            validation_details = {}
        
        # 创建改进记录
        improvement = ImprovementRecord(
            id=self._generate_improvement_id(),
            timestamp=datetime.now(),
            improvement_type=ImprovementType.MEMORY_EDIT,
            description=f"Updated {target_block} memory block",
            reason=parsed_reason.get('reason', reason),
            before_state={'block': target_block, 'content': before_state[target_block]},
            after_state={'block': target_block, 'content': after_state[target_block]},
            validation_status=validation_status,
            validation_score=validation_score,
            test_results=validation_details,
            rolled_back=False
        )
        
        # 如果验证失败且未启用 LLM，自动回滚
        if validation_status == ValidationStatus.REJECTED and not use_llm:
            self._rollback_improvement(improvement)
            improvement.rolled_back = True
        
        # 记录改进
        self.history.append(improvement.to_dict())
        self._update_metrics(improvement)
        self._save_history()
        self._save_metrics()
        
        # 更新 LLM 统计
        llm_stats = self.llm.get_stats()
        self.llm_stats['cache_hits'] = llm_stats.get('cache_hits', 0)
        self.llm_stats['cache_misses'] = llm_stats.get('cache_misses', 0)
        
        return {
            'status': 'success',
            'message': f'Remembered: {instruction}',
            'block_updated': target_block,
            'improvement_id': improvement.id,
            'validation_status': validation_status.value,
            'validation_score': validation_score,
            'reason': parsed_reason.get('reason', reason),
            'blocks': after_state,
            'llm_used': use_llm and self.llm_enabled,
            'llm_stats': self.llm_stats.copy()
        }
    
    def _parse_llm_reason(self, response: str) -> Dict:
        """解析 LLM 生成的理由"""
        lines = response.strip().split('\n')
        result = {}
        
        for line in lines:
            if ':' in line:
                key, value = line.split(':', 1)
                key = key.strip()
                value = value.strip()
                
                if key == '建议更新':
                    # 映射到英文块名
                    mapping = {
                        'human': 'human',
                        'persona': 'persona',
                        'archival': 'archival'
                    }
                    result['suggested_block'] = mapping.get(value.lower(), 'archival')
                elif key == '置信度':
                    try:
                        result['confidence'] = float(value)
                    except:
                        result['confidence'] = 0.5
                else:
                    result[key] = value
        
        return result
    
    def _validate_improvement_simple(self, before: Dict, after: Dict, instruction: str) -> Dict:
        """简单的规则验证 (LLM 不可用时使用)"""
        score = 0.0
        
        # 测试 1: 一致性
        before_content = str(before)
        after_content = str(after)
        if len(after_content) >= len(before_content) * 0.5:
            score += 0.3
        else:
            score += 0.1
        
        # 测试 2: 清晰度
        if len(instruction) > 10:
            score += 0.3
        elif len(instruction) > 5:
            score += 0.2
        
        # 测试 3: 相关性
        meaningful_keywords = [
            '偏好', '喜欢', '想要', '需要', '使用', '避免',
            '总是', '从不', '有时', '通常'
        ]
        matches = sum(1 for keyword in meaningful_keywords if keyword in instruction.lower())
        score += min(0.4, matches * 0.1)
        
        # 确定状态
        if score >= 0.7:
            status = ValidationStatus.VALIDATED
        elif score >= 0.4:
            status = ValidationStatus.TESTING
        else:
            status = ValidationStatus.REJECTED
        
        return {
            'status': status,
            'score': score,
            'test_results': {
                'consistency': 0.3,
                'clarity': 0.3,
                'relevance': 0.4,
                'overall': score
            }
        }
    
    def _map_validation_status(self, conclusion: str) -> ValidationStatus:
        """映射验证结论到状态"""
        mapping = {
            '通过': ValidationStatus.VALIDATED,
            '需要测试': ValidationStatus.TESTING,
            '拒绝': ValidationStatus.REJECTED
        }
        return mapping.get(conclusion, ValidationStatus.VALIDATED)
    
    async def detect_user_patterns(self, conversations: List[Dict], use_llm: bool = True) -> Dict:
        """
        检测用户行为模式
        
        Args:
            conversations: 对话历史
            use_llm: 是否使用 LLM
        
        Returns:
            识别的模式
        """
        if use_llm and self.llm_enabled:
            self.llm_stats['pattern_detection'] += 1
            return await self.llm.detect_patterns(conversations)
        else:
            # 回退到简单词频统计
            return self._detect_patterns_simple(conversations)
    
    def _detect_patterns_simple(self, conversations: List[Dict]) -> Dict:
        """简单的模式检测 (规则基础)"""
        word_freq = {}
        
        for conv in conversations:
            content = conv.get('content', '')
            words = content.split()
            for word in words:
                if len(word) > 1:
                    word_freq[word] = word_freq.get(word, 0) + 1
        
        # 返回高频词
        sorted_words = sorted(word_freq.items(), key=lambda x: x[1], reverse=True)
        
        return {
            'word_frequency': sorted_words[:20],
            'total_conversations': len(conversations)
        }
    
    def get_llm_stats(self) -> Dict:
        """获取 LLM 使用统计"""
        return {
            **self.llm_stats,
            'llm_details': self.llm.get_stats(),
            'model_info': self.llm.get_model_info()
        }
    
    def toggle_llm(self, enabled: bool) -> Dict:
        """
        切换 LLM 启用状态
        
        Args:
            enabled: True 启用，False 禁用
        
        Returns:
            状态信息
        """
        self.llm_enabled = enabled
        return {
            'status': 'success',
            'llm_enabled': enabled,
            'message': f'LLM {"enabled" if enabled else "disabled"}'
        }
    
    async def batch_remember(self, instructions: List[str], use_llm: bool = True) -> List[Dict]:
        """
        批量记忆 (自动使用批处理优化)
        
        Args:
            instructions: 指令列表
            use_llm: 是否使用 LLM
        
        Returns:
            结果列表
        """
        results = []
        
        for instruction in instructions:
            result = await self.remember(instruction, use_llm=use_llm)
            results.append(result)
        
        return results
    
    def get_improvement_summary(self, limit: int = 10) -> Dict:
        """
        获取改进摘要
        
        Args:
            limit: 返回的记录数
        
        Returns:
            摘要信息
        """
        history = self.get_improvement_history(limit=limit)
        
        summary = {
            'total': len(self.history),
            'recent': history,
            'by_type': {},
            'by_status': {},
            'average_score': 0.0,
            'llm_usage': self.get_llm_stats()
        }
        
        # 按类型统计
        for record in self.history:
            type_key = record['improvement_type']
            summary['by_type'][type_key] = summary['by_type'].get(type_key, 0) + 1
            
            status_key = record['validation_status']
            summary['by_status'][status_key] = summary['by_status'].get(status_key, 0) + 1
        
        # 计算平均分
        scores = [r.get('validation_score', 0) for r in self.history if r.get('validation_score')]
        if scores:
            summary['average_score'] = sum(scores) / len(scores)
        
        return summary
