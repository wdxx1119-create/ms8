"""
Meta-Cognition System - 元认知系统
Based on Letta's meta-cognition architecture

实现功能:
1. 自我监控
2. 弱点识别
3. 自我优化
4. 性能评估
5. 持续进化
"""
import json
import hashlib
import os
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, List, Optional, Any, Tuple
from dataclasses import dataclass, asdict, field
from enum import Enum

from .config import get_config
from .file_write_guard import atomic_write_json
from .local_llm import LocalLLM, LLMConfig


class PerformanceMetric(Enum):
    """性能指标"""
    RESPONSE_QUALITY = "response_quality"      # 响应质量
    RESPONSE_SPEED = "response_speed"          # 响应速度
    USER_SATISFACTION = "user_satisfaction"    # 用户满意度
    TASK_COMPLETION = "task_completion"        # 任务完成率
    LEARNING_EFFICIENCY = "learning_efficiency" # 学习效率


class ImprovementArea(Enum):
    """改进领域"""
    KNOWLEDGE = "knowledge"          # 知识
    SKILLS = "skills"                # 技能
    RESPONSE = "response"            # 响应
    MEMORY = "memory"                # 记忆
    EFFICIENCY = "efficiency"        # 效率


@dataclass
class PerformanceReport:
    """性能报告"""
    id: str
    timestamp: datetime
    period: str                       # 报告周期 (daily/weekly/monthly)
    metrics: Dict[str, float]         # 各项指标得分
    strengths: List[str]              # 优势
    weaknesses: List[str]             # 弱点
    recommendations: List[str]        # 改进建议
    overall_score: float              # 总体得分
    
    def to_dict(self) -> Dict:
        return {
            'id': self.id,
            'timestamp': self.timestamp.isoformat(),
            'period': self.period,
            'metrics': self.metrics,
            'strengths': self.strengths,
            'weaknesses': self.weaknesses,
            'recommendations': self.recommendations,
            'overall_score': self.overall_score
        }
    
    @classmethod
    def from_dict(cls, data: Dict) -> 'PerformanceReport':
        return cls(
            id=data['id'],
            timestamp=datetime.fromisoformat(data['timestamp']),
            period=data['period'],
            metrics=data['metrics'],
            strengths=data['strengths'],
            weaknesses=data['weaknesses'],
            recommendations=data['recommendations'],
            overall_score=data['overall_score']
        )


@dataclass
class ImprovementPlan:
    """改进计划"""
    id: str
    created: datetime
    area: ImprovementArea             # 改进领域
    description: str                   # 改进描述
    actions: List[str]                 # 具体行动
    priority: int                      # 优先级 (1-5)
    status: str                        # 状态 (pending/in_progress/completed)
    expected_improvement: float        # 预期提升
    actual_improvement: Optional[float] # 实际提升
    completed: Optional[datetime]      # 完成时间
    
    def to_dict(self) -> Dict:
        return {
            'id': self.id,
            'created': self.created.isoformat(),
            'area': self.area.value,
            'description': self.description,
            'actions': self.actions,
            'priority': self.priority,
            'status': self.status,
            'expected_improvement': self.expected_improvement,
            'actual_improvement': self.actual_improvement,
            'completed': self.completed.isoformat() if self.completed else None
        }
    
    @classmethod
    def from_dict(cls, data: Dict) -> 'ImprovementPlan':
        return cls(
            id=data['id'],
            created=datetime.fromisoformat(data['created']),
            area=ImprovementArea(data['area']),
            description=data['description'],
            actions=data['actions'],
            priority=data['priority'],
            status=data['status'],
            expected_improvement=data['expected_improvement'],
            actual_improvement=data.get('actual_improvement'),
            completed=datetime.fromisoformat(data['completed']) if data.get('completed') else None
        )


@dataclass
class SelfAssessment:
    """自我评估"""
    id: str
    timestamp: datetime
    assessment_type: str              # 评估类型
    scores: Dict[str, float]          # 各项得分
    insights: List[str]               # 洞察
    action_items: List[str]           # 行动项
    
    def to_dict(self) -> Dict:
        return {
            'id': self.id,
            'timestamp': self.timestamp.isoformat(),
            'assessment_type': self.assessment_type,
            'scores': self.scores,
            'insights': self.insights,
            'action_items': self.action_items
        }


class MetaCognitionSystem:
    """
    元认知系统
    
    基于 Letta 的元认知架构，实现自我监控、自我优化和持续进化
    """
    
    def __init__(self, llm: LocalLLM = None, config: LLMConfig = None):
        """
        初始化元认知系统
        
        Args:
            llm: LocalLLM 实例
            config: LLM 配置
        """
        try:
            self.llm = llm or LocalLLM(config or LLMConfig())
            self.llm_available = True
        except Exception:
            # Degrade to monitor-only rule-based mode when local LLM runtime is unavailable.
            self.llm = None
            self.llm_available = False
        
        # 性能报告历史
        self.reports: Dict[str, PerformanceReport] = {}
        
        # 改进计划
        self.improvement_plans: Dict[str, ImprovementPlan] = {}
        
        # 自我评估历史
        self.assessments: Dict[str, SelfAssessment] = {}
        
        # 性能指标历史
        self.metrics_history: List[Dict] = []
        self.last_run: Optional[str] = None
        
        # 存储路径与配置
        config_data = get_config()
        self.settings = config_data["settings"]["memory"].get("meta_cognition", {})
        if not self.llm_available:
            self.settings["llm_enabled"] = False
            self.settings["mode"] = "monitor_only"
        threshold_cfg = config_data["settings"]["memory"].get("meta_cognition_thresholds", {})
        self.strength_min_score = float(threshold_cfg.get("strength_min_score", 0.8))
        self.weakness_max_score = float(threshold_cfg.get("weakness_max_score", 0.6))
        self.trend_change_significant = float(threshold_cfg.get("trend_change_significant", 0.05))
        self.rule_based_quality_default = float(threshold_cfg.get("rule_based_quality_default", 0.6))
        self.rule_based_satisfaction_default = float(threshold_cfg.get("rule_based_satisfaction_default", 0.6))
        self.estimated_improvement_fallback = float(threshold_cfg.get("estimated_improvement_fallback", 0.2))
        self.meta_file = config_data['memory_dir'] / 'meta_cognition.json'
        report_dir = Path(self.settings.get("report_dir", config_data["memory_dir"] / "meta_reports"))
        if not report_dir.is_absolute():
            report_dir = config_data["workspace_dir"] / report_dir
        report_dir.mkdir(parents=True, exist_ok=True)
        self.report_dir = report_dir
        self.backup_keep = int(self.settings.get("backup_keep", 3))
        self.lock_file = self.meta_file.with_suffix('.lock')

        self._load_meta_data()
    
    def _acquire_lock(self, timeout: float = 5.0) -> bool:
        start = time.time()
        while time.time() - start < timeout:
            try:
                fd = os.open(self.lock_file, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
                os.write(fd, str(os.getpid()).encode('utf-8'))
                os.close(fd)
                return True
            except FileExistsError:
                time.sleep(0.05)
            except Exception:
                return False
        return False

    def _release_lock(self) -> None:
        try:
            if self.lock_file.exists():
                self.lock_file.unlink()
        except Exception:
            pass

    def _validate_payload(self, data: Dict) -> Dict:
        if not isinstance(data, dict):
            return {"reports": {}, "improvement_plans": {}, "assessments": {}, "metrics_history": []}
        for key in ("reports", "improvement_plans", "assessments", "metrics_history"):
            data.setdefault(key, {} if key != "metrics_history" else [])
        return data

    def _load_meta_data(self) -> None:
        """从文件加载元认知数据"""
        if not self.meta_file.exists():
            return
        if not self._acquire_lock():
            return
        try:
            data = json.loads(self.meta_file.read_text(encoding='utf-8'))
            data = self._validate_payload(data)
            self.reports = {
                k: PerformanceReport.from_dict(v) 
                for k, v in data.get('reports', {}).items()
            }
            self.improvement_plans = {
                k: ImprovementPlan.from_dict(v) 
                for k, v in data.get('improvement_plans', {}).items()
            }
            self.assessments = {
                k: SelfAssessment.from_dict(v) 
                for k, v in data.get('assessments', {}).items()
            }
            self.metrics_history = data.get('metrics_history', [])
            self.last_run = data.get('last_run')
        except Exception as e:
            print(f"[MetaCognition] Error loading data: {e}")
        finally:
            self._release_lock()
    
    def _save_meta_data(self) -> None:
        """保存元认知数据到文件"""
        self.meta_file.parent.mkdir(parents=True, exist_ok=True)
        if not self._acquire_lock():
            return
        try:
            payload = {
                'reports': {k: v.to_dict() for k, v in self.reports.items()},
                'improvement_plans': {k: v.to_dict() for k, v in self.improvement_plans.items()},
                'assessments': {k: v.to_dict() for k, v in self.assessments.items()},
                'metrics_history': self.metrics_history,
                'last_run': self.last_run,
            }
            tmp_path = self.meta_file.with_suffix(".json.tmp")
            tmp_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
            if self.meta_file.exists():
                backup_path = self.meta_file.with_suffix(f".{datetime.now().strftime('%Y%m%d_%H%M%S')}.bak")
                try:
                    self.meta_file.replace(backup_path)
                except Exception:
                    pass
            tmp_path.replace(self.meta_file)
            self._trim_backups()
        finally:
            self._release_lock()

    def _trim_backups(self) -> None:
        backups = sorted(self.meta_file.parent.glob("meta_cognition.*.bak"), reverse=True)
        for stale in backups[self.backup_keep:]:
            try:
                stale.unlink()
            except Exception:
                continue
    

    def _llm_allowed(self) -> bool:
        mode = self.settings.get("mode", "monitor_only")
        if mode in {"monitor", "monitor_only"}:
            return False
        if not self.llm_available or self.llm is None:
            return False
        return bool(self.settings.get("llm_enabled", True))

    async def self_monitor(self, 
                          conversations: List[Dict],
                          period: str = 'daily') -> PerformanceReport:
        """
        自我监控 - 评估当前性能
        
        Args:
            conversations: 对话历史
            period: 报告周期
        
        Returns:
            性能报告
        """
        # 1. 收集性能指标
        metrics = await self._collect_metrics(conversations)
        
        # 2. 识别优势
        strengths = await self._identify_strengths(metrics, conversations)
        
        # 3. 识别弱点
        weaknesses = await self._identify_weaknesses(metrics, conversations)
        
        # 4. 生成改进建议
        recommendations = []
        if self.settings.get("mode", "monitor_only") != "monitor_only":
            recommendations = await self._generate_recommendations(weaknesses)
        
        # 5. 计算总体得分
        overall_score = self._calculate_overall_score(metrics)
        
        # 创建报告
        report = PerformanceReport(
            id=hashlib.md5(f"{datetime.now()}{period}".encode()).hexdigest()[:12],
            timestamp=datetime.now(),
            period=period,
            metrics=metrics,
            strengths=strengths,
            weaknesses=weaknesses,
            recommendations=recommendations,
            overall_score=overall_score
        )
        
        # 保存
        self.reports[report.id] = report
        self.metrics_history.append({
            'timestamp': datetime.now().isoformat(),
            'metrics': metrics,
            'overall_score': overall_score
        })
        self.last_run = datetime.now().isoformat()
        
        self._save_meta_data()
        
        self._write_report_file(report)
        return report

    def _write_report_file(self, report: PerformanceReport) -> None:
        path = self.report_dir / f"meta_report_{report.timestamp.strftime('%Y%m%d_%H%M%S')}.json"
        atomic_write_json(path, report.to_dict(), ensure_ascii=False, indent=2)
    
    async def _collect_metrics(self, conversations: List[Dict]) -> Dict[str, float]:
        """收集性能指标"""
        metrics = {}
        
        # 1. 响应质量 (使用 LLM 评估)
        metrics['response_quality'] = await self._evaluate_response_quality(conversations)
        
        # 2. 响应速度 (如果有时间戳)
        metrics['response_speed'] = self._calculate_response_speed(conversations)
        
        # 3. 用户满意度 (基于情感分析)
        metrics['user_satisfaction'] = await self._estimate_user_satisfaction(conversations)
        
        # 4. 任务完成率
        metrics['task_completion'] = self._calculate_task_completion(conversations)
        
        # 5. 学习效率
        metrics['learning_efficiency'] = await self._evaluate_learning_efficiency(conversations)

        return self._smooth_metrics(metrics)

    def _smooth_metrics(self, metrics: Dict[str, float]) -> Dict[str, float]:
        window_size = int(self.settings.get("window_size", 50))
        decay = float(self.settings.get("time_decay", 0.85))
        outlier_z = float(self.settings.get("outlier_zscore", 2.5))
        recent = self.metrics_history[-window_size:]
        if not recent:
            return metrics
        smoothed: Dict[str, float] = {}
        for key, value in metrics.items():
            series = [entry.get("metrics", {}).get(key) for entry in recent]
            series = [s for s in series if s is not None]
            if series:
                mean = sum(series) / len(series)
                variance = sum((s - mean) ** 2 for s in series) / max(1, len(series))
                std = variance ** 0.5
                if std > 0:
                    series = [s for s in series if abs((s - mean) / std) <= outlier_z]
            weighted = value
            weight = 1.0
            multiplier = decay
            for past_value in reversed(series):
                weighted += past_value * multiplier
                weight += multiplier
                multiplier *= decay
            smoothed[key] = max(0.0, min(1.0, weighted / weight))
        return smoothed
        
        return metrics
    
    async def _evaluate_response_quality(self, conversations: List[Dict]) -> float:
        """评估响应质量"""
        if not conversations:
            return 0.5
        
        # 获取 AI 响应
        ai_responses = [
            c.get('content', '') 
            for c in conversations 
            if c.get('role') == 'assistant'
        ][-10:]
        
        if not ai_responses:
            return 0.5
        
        # 使用 LLM 评估质量
        formatted_responses = '\n\n'.join(ai_responses[:5])
        
        prompt = f"""评估以下 AI 响应的质量 (0-1 分):

{formatted_responses}

评估标准:
- 准确性：信息是否准确
- 完整性：是否完整回答问题
- 清晰度：表达是否清晰
- 有用性：是否对用户有帮助

请只返回一个 0-1 之间的分数:
"""
        
        if self._llm_allowed():
            try:
                messages = [{'role': 'user', 'content': prompt}]
                response = await self.llm.chat(messages, temperature=0.3, max_tokens=50)
                import re
                score_match = re.search(r'(\d\.?\d*)', response)
                if score_match:
                    return min(1.0, max(0.0, float(score_match.group(1))))
            except Exception:
                if not self.settings.get("llm_fallback_enabled", True):
                    return 0.5
        return self._rule_based_quality(ai_responses)
        
        return 0.7  # 默认分数
    
    def _calculate_response_speed(self, conversations: List[Dict]) -> float:
        """计算响应速度"""
        timestamps = [
            c.get('timestamp') for c in conversations
            if c.get('timestamp')
        ]
        if len(timestamps) < 2:
            return 0.8
        try:
            parsed = [datetime.fromisoformat(ts) for ts in timestamps]
            deltas = [(parsed[i] - parsed[i - 1]).total_seconds() for i in range(1, len(parsed))]
            if not deltas:
                return 0.8
            avg = sum(deltas) / len(deltas)
            if avg <= 2:
                return 0.95
            if avg <= 5:
                return 0.85
            if avg <= 10:
                return 0.7
            return 0.6
        except Exception:
            return 0.8
    
    async def _estimate_user_satisfaction(self, conversations: List[Dict]) -> float:
        """估计用户满意度"""
        if not conversations:
            return 0.5
        
        # 获取用户消息
        user_messages = [
            c.get('content', '') 
            for c in conversations 
            if c.get('role') == 'user'
        ][-10:]
        
        if not user_messages:
            return 0.5
        
        # 使用 LLM 估计满意度
        combined = ' '.join(user_messages)
        
        prompt = f"""基于以下用户消息，估计用户满意度 (0-1 分):

"{combined}"

请只返回一个 0-1 之间的分数:
"""
        
        if self._llm_allowed():
            try:
                messages = [{'role': 'user', 'content': prompt}]
                response = await self.llm.chat(messages, temperature=0.3, max_tokens=50)
                import re
                score_match = re.search(r'(\d\.?\d*)', response)
                if score_match:
                    return min(1.0, max(0.0, float(score_match.group(1))))
            except Exception:
                if not self.settings.get("llm_fallback_enabled", True):
                    return 0.5
        return self._rule_based_satisfaction(user_messages)
    
    def _calculate_task_completion(self, conversations: List[Dict]) -> float:
        """计算任务完成率"""
        if not conversations:
            return 0.5
        done_tokens = ('完成', '已解决', '解决了', '成功', 'done', 'fixed')
        total = len([c for c in conversations if c.get('role') == 'assistant'])
        if total == 0:
            return 0.5
        hits = sum(1 for c in conversations if c.get('role') == 'assistant' and any(t in c.get('content', '') for t in done_tokens))
        return min(1.0, 0.5 + hits / max(1, total))
    
    async def _evaluate_learning_efficiency(self, conversations: List[Dict]) -> float:
        """评估学习效率"""
        count = len(conversations)
        if count == 0:
            return 0.5
        return min(1.0, 0.5 + min(0.5, count / 200))
    
    async def _identify_strengths(self, 
                                  metrics: Dict[str, float],
                                  conversations: List[Dict]) -> List[str]:
        """识别优势"""
        strengths = []
        
        # 找出高分指标
        for metric, score in metrics.items():
            if score >= self.strength_min_score:
                strengths.append(f"{metric} 表现优秀 (得分：{score:.2f})")
        
        if strengths and self.settings.get("llm_enabled", True):
            prompt = f"""基于以下性能指标，总结系统的优势:

{json.dumps(metrics, indent=2)}

优势:
{chr(10).join(strengths)}

请用简洁的语言描述优势 (最多 3 条):
"""
            
            try:
                messages = [{'role': 'user', 'content': prompt}]
                response = await self.llm.chat(messages, temperature=0.5, max_tokens=300)
                return [line.strip() for line in response.split('\n') if line.strip()][:3]
            except Exception:
                if not self.settings.get("llm_fallback_enabled", True):
                    return strengths[:3]
        return strengths[:3]
    
    async def _identify_weaknesses(self, 
                                   metrics: Dict[str, float],
                                   conversations: List[Dict]) -> List[str]:
        """识别弱点"""
        weaknesses = []
        
        # 找出低分指标
        for metric, score in metrics.items():
            if score < self.weakness_max_score:
                weaknesses.append(f"{metric} 需要改进 (得分：{score:.2f})")
        
        formatted_conversations = self._format_conversations(conversations[-20:])
        if self._llm_allowed():
            prompt = f"""分析以下对话，识别系统的弱点和不足:

{formatted_conversations}

性能指标:
{json.dumps(metrics, indent=2)}

请列出需要改进的方面 (最多 5 条):
"""
            try:
                messages = [{'role': 'user', 'content': prompt}]
                response = await self.llm.chat(messages, temperature=0.5, max_tokens=500)
                llm_weaknesses = [
                    line.strip() 
                    for line in response.split('\n') 
                    if line.strip() and not line.startswith('性能指标')
                ][:5]
                weaknesses.extend(llm_weaknesses)
            except Exception:
                if not self.settings.get("llm_fallback_enabled", True):
                    return weaknesses[:5]
        return weaknesses[:5]
    
    async def _generate_recommendations(self, weaknesses: List[str]) -> List[str]:
        """生成改进建议"""
        if not weaknesses:
            return []
        
        prompt = f"""基于以下弱点，生成具体的改进建议:

弱点:
{chr(10).join(weaknesses)}

请为每个弱点提供具体的改进行动 (最多 5 条):
"""
        
        if self._llm_allowed():
            try:
                messages = [{'role': 'user', 'content': prompt}]
                response = await self.llm.chat(messages, temperature=0.5, max_tokens=500)
                return [
                    line.strip() 
                    for line in response.split('\n') 
                    if line.strip()
                ][:5]
            except Exception:
                if not self.settings.get("llm_fallback_enabled", True):
                    return []
        return [f"Review and improve {item}" for item in weaknesses][:5]

    def _rule_based_quality(self, responses: List[str]) -> float:
        if not responses:
            return self.rule_based_quality_default
        avg_len = sum(len(r) for r in responses) / max(1, len(responses))
        return min(1.0, 0.5 + min(0.5, avg_len / 800))

    def _rule_based_satisfaction(self, user_messages: List[str]) -> float:
        if not user_messages:
            return self.rule_based_satisfaction_default
        positive = sum(1 for m in user_messages if any(t in m for t in ("谢谢", "很好", "不错", "ok", "好的")))
        negative = sum(1 for m in user_messages if any(t in m for t in ("不对", "不行", "错误", "不好")))
        base = 0.6 + 0.1 * positive - 0.1 * negative
        return max(0.0, min(1.0, base))
    
    def _calculate_overall_score(self, metrics: Dict[str, float]) -> float:
        """计算总体得分"""
        if not metrics:
            return 0.5
        
        # 加权平均
        weights = self.settings.get('metrics_weights', {}) or {
            'response_quality': 0.3,
            'response_speed': 0.2,
            'user_satisfaction': 0.2,
            'task_completion': 0.2,
            'learning_efficiency': 0.1
        }
        
        total = 0.0
        total_weight = 0.0
        
        for metric, score in metrics.items():
            weight = weights.get(metric, 0.1)
            total += score * weight
            total_weight += weight
        
        return total / total_weight if total_weight > 0 else 0.5
    
    async def create_improvement_plan(self, 
                                      area: ImprovementArea,
                                      description: str,
                                      priority: int = 3) -> ImprovementPlan:
        """
        创建改进计划
        
        Args:
            area: 改进领域
            description: 改进描述
            priority: 优先级 (1-5)
        
        Returns:
            改进计划
        """
        # 使用 LLM 生成具体行动
        actions = await self._generate_improvement_actions(area, description)
        
        # 估计预期提升
        expected_improvement = await self._estimate_improvement(area, actions)
        
        plan = ImprovementPlan(
            id=hashlib.md5(f"{datetime.now()}{area.value}".encode()).hexdigest()[:12],
            created=datetime.now(),
            area=area,
            description=description,
            actions=actions,
            priority=priority,
            status='pending',
            expected_improvement=expected_improvement,
            actual_improvement=None,
            completed=None
        )
        
        self.improvement_plans[plan.id] = plan
        self._save_meta_data()
        
        return plan
    
    async def _generate_improvement_actions(self, 
                                           area: ImprovementArea,
                                           description: str) -> List[str]:
        """生成改进行动"""
        prompt = f"""针对以下改进领域，生成具体的行动步骤:

改进领域：{area.value}
描述：{description}

请列出 3-5 个具体的行动步骤:
"""
        
        try:
            messages = [{'role': 'user', 'content': prompt}]
            response = await self.llm.chat(messages, temperature=0.5, max_tokens=500)
            
            return [
                line.strip() 
                for line in response.split('\n') 
                if line.strip() and not line.startswith('改进领域') and not line.startswith('描述')
            ][:5]
        except Exception:
            return ["实施改进计划"]
    
    async def _estimate_improvement(self, 
                                   area: ImprovementArea,
                                   actions: List[str]) -> float:
        """估计改进效果"""
        prompt = f"""估计以下改进行动的效果 (0-1 分，1 为最大提升):

改进领域：{area.value}
行动:
{chr(10).join(actions)}

请只返回一个 0-1 之间的分数:
"""
        
        try:
            messages = [{'role': 'user', 'content': prompt}]
            response = await self.llm.chat(messages, temperature=0.3, max_tokens=50)
            
            import re
            score_match = re.search(r'(\d\.?\d*)', response)
            if score_match:
                return min(1.0, max(0.0, float(score_match.group(1))))
        except Exception:
            pass
        
        return self.estimated_improvement_fallback
    
    def _format_conversations(self, conversations: List[Dict]) -> str:
        """格式化对话"""
        lines = []
        for conv in conversations:
            role = '用户' if conv.get('role') == 'user' else 'AI'
            content = conv.get('content', '')[:200]
            if content:
                lines.append(f"{role}: {content}")
        return '\n'.join(lines)
    
    def get_status(self) -> Dict[str, Any]:
        latest = None
        if self.reports:
            latest = max(self.reports.values(), key=lambda r: r.timestamp)
        return {
            "last_run": self.last_run,
            "report_count": len(self.reports),
            "last_report_id": latest.id if latest else None,
            "last_report_period": latest.period if latest else None,
        }

    def get_performance_trend(self, days: int = 7) -> Dict:
        """获取性能趋势"""
        cutoff = datetime.now() - timedelta(days=days)
        recent_metrics = [
            m for m in self.metrics_history
            if datetime.fromisoformat(m['timestamp']) >= cutoff
        ]
        
        if not recent_metrics:
            return {
                'trend': 'stable',
                'change': 0,
                'data_points': 0
            }
        
        # 计算趋势
        scores = [m['overall_score'] for m in recent_metrics]
        
        if len(scores) < 2:
            return {
                'trend': 'stable',
                'change': 0,
                'data_points': len(scores)
            }
        
        # 简单趋势分析
        first_half = scores[:len(scores)//2]
        second_half = scores[len(scores)//2:]
        
        avg_first = sum(first_half) / len(first_half)
        avg_second = sum(second_half) / len(second_half)
        
        change = avg_second - avg_first
        
        if change > self.trend_change_significant:
            trend = 'improving'
        elif change < -self.trend_change_significant:
            trend = 'declining'
        else:
            trend = 'stable'
        
        return {
            'trend': trend,
            'change': change,
            'data_points': len(scores),
            'average_score': sum(scores) / len(scores)
        }
    
    def get_improvement_progress(self) -> Dict:
        """获取改进进度"""
        plans = list(self.improvement_plans.values())
        
        status_counts = {}
        for plan in plans:
            status = plan.status
            status_counts[status] = status_counts.get(status, 0) + 1
        
        return {
            'total_plans': len(plans),
            'by_status': status_counts,
            'completed': sum(1 for p in plans if p.status == 'completed'),
            'in_progress': sum(1 for p in plans if p.status == 'in_progress'),
            'pending': sum(1 for p in plans if p.status == 'pending')
        }
