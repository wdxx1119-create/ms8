"""
Local LLM Client - Ollama Integration
支持多模型、智能路由、语义缓存
"""
import asyncio
import hashlib
import os
import time
import json
from pathlib import Path
from typing import Dict, List, Optional, Any, Tuple
from dataclasses import dataclass, asdict
try:
    import ollama
except ImportError:
    ollama = None

try:
    import numpy as np
except ImportError:
    np = None


def _create_ollama_client():
    if ollama is None:
        raise RuntimeError("The 'ollama' Python package is not installed.")
    host = os.environ.get("OLLAMA_HOST", "http://127.0.0.1:11434")
    return ollama.Client(host=host, trust_env=False)

@dataclass
class LLMConfig:
    """LLM 配置 - 轻量模型优化版"""
    # 轻量快速模型 (主力)
    primary_model: str = 'gemma3:1b'
    complex_model: str = 'llama3.2:3b'
    
    # 备用模型 (不再使用大模型)
    reasoning_model: str = 'llama3.2:3b'
    
    # 嵌入模型
    embedding_model: str = 'nomic-embed-text:latest'
    
    # 路由阈值 (调整以适应轻量模型)
    complexity_threshold: float = 0.5          # 降低阈值
    reasoning_threshold: float = 0.7
    
    # 缓存配置 (更重要，减少 LLM 调用)
    cache_enabled: bool = True
    cache_ttl: int = 7200                      # 2 小时 (延长)
    cache_similarity_threshold: float = 0.80   # 降低阈值提高命中率
    
    # 批处理配置
    batch_enabled: bool = True
    batch_max_size: int = 10                   # 增加批处理大小
    batch_max_wait_ms: int = 200               # 增加等待时间

class SemanticCache:
    """语义缓存 - 基于向量相似度"""
    
    def __init__(self, embedding_model: str, similarity_threshold: float = 0.85, ttl: int = 3600):
        self.embedding_model = embedding_model
        self.similarity_threshold = similarity_threshold
        self.ttl = ttl
        self.cache: Dict[str, Dict] = {}  # {hash: {embedding, result, timestamp}}
        self.client = _create_ollama_client()

    def _ensure_vector(self, value: Any):
        """Normalize embedding fallback outputs into 1-D vectors."""
        if np is None:
            if isinstance(value, list):
                return [float(x) for x in value]
            return [float(value)]

        if isinstance(value, np.ndarray):
            arr = value.astype(float)
        elif isinstance(value, list):
            arr = np.array(value, dtype=float)
        else:
            arr = np.array([float(value)], dtype=float)
        return np.atleast_1d(arr)
    
    def _embed(self, text: str):
        """生成文本嵌入"""
        try:
            response = self.client.embeddings(
                model=self.embedding_model,
                prompt=text
            )
            return self._ensure_vector(response['embedding'])
        except Exception:
            # 回退到简单哈希（必须保持向量形态，避免标量触发 len() 错误）
            fallback = float(abs(hash(text)) % 10000)
            return self._ensure_vector([fallback])
    
    def _cosine_similarity(self, a, b) -> float:
        """计算余弦相似度"""
        a = self._ensure_vector(a)
        b = self._ensure_vector(b)

        if np is None:
            if len(a) == 1 or len(b) == 1:  # 回退情况
                return 1.0 if str(a) == str(b) else 0.0
            dot_product = sum(x * y for x, y in zip(a, b))
            norm_a = sum(x * x for x in a) ** 0.5
            norm_b = sum(y * y for y in b) ** 0.5
        else:
            if a.size == 1 or b.size == 1:  # 回退情况
                return 1.0 if float(a.reshape(-1)[0]) == float(b.reshape(-1)[0]) else 0.0
            dot_product = np.dot(a, b)
            norm_a = np.linalg.norm(a)
            norm_b = np.linalg.norm(b)
        
        if norm_a == 0 or norm_b == 0:
            return 0.0
        
        return float(dot_product / (norm_a * norm_b))
    
    def get(self, text: str) -> Optional[Any]:
        """获取缓存 (语义匹配)"""
        if not self.cache:
            return None
        
        query_embedding = self._embed(text)
        current_time = time.time()
        
        to_remove = []
        
        for hash_key, cached in self.cache.items():
            # 检查过期
            if current_time - cached['timestamp'] > self.ttl:
                to_remove.append(hash_key)
                continue
            
            # 计算相似度
            similarity = self._cosine_similarity(query_embedding, cached['embedding'])
            
            if similarity >= self.similarity_threshold:
                # 清理过期项
                for key in to_remove:
                    del self.cache[key]
                return cached['result']
        
        # 清理过期项
        for key in to_remove:
            del self.cache[key]
        
        return None
    
    def set(self, text: str, result: Any) -> None:
        """设置缓存"""
        embedding = self._embed(text)
        if np is None:
            hash_key = hashlib.md5(json.dumps(embedding, ensure_ascii=False).encode("utf-8")).hexdigest()
        else:
            hash_key = hashlib.md5(embedding.tobytes()).hexdigest()
        
        self.cache[hash_key] = {
            'embedding': embedding,
            'result': result,
            'timestamp': time.time()
        }
    
    def stats(self) -> Dict:
        """缓存统计"""
        return {
            'size': len(self.cache),
            'ttl': self.ttl,
            'similarity_threshold': self.similarity_threshold
        }

class SmartRouter:
    """智能路由 - 决定使用哪个模型"""
    
    def __init__(self, config: LLMConfig):
        self.config = config
        self.client = _create_ollama_client()
    
    def calculate_complexity(self, context: str, instruction: str = '') -> float:
        """
        计算请求复杂度 (0-1)
        
        因素：
        - 上下文长度
        - 情感词密度
        - 指代和省略
        - 多意图
        - 逻辑复杂度
        """
        text = context + ' ' + instruction
        score = 0.0
        
        # 因素 1: 文本长度 (0-0.25)
        if len(text) > 2000:
            score += 0.25
        elif len(text) > 1000:
            score += 0.15
        elif len(text) > 500:
            score += 0.05
        
        # 因素 2: 情感词密度 (0-0.25)
        emotion_words = [
            '讨厌', '喜欢', '爱', '恨', '永远', '绝对', '必须',
            '一定', '肯定', '特别', '非常', '极其', '最'
        ]
        emotion_count = sum(1 for word in emotion_words if word in text)
        score += min(0.25, emotion_count * 0.05)
        
        # 因素 3: 指代和省略 (0-0.2)
        pronouns = ['这个', '那个', '之前', '还是', '它', '他', '她', '这样', '那样']
        if any(p in text for p in pronouns):
            score += 0.2
        
        # 因素 4: 多意图 (0-0.15)
        if text.count(',') >= 3 or text.count(';') >= 2 or text.count('。') >= 3:
            score += 0.15
        elif text.count(',') >= 2 or text.count(';') >= 1:
            score += 0.08
        
        # 因素 5: 逻辑复杂度 (0-0.15)
        logic_words = ['如果', '那么', '因为', '所以', '但是', '然而', '虽然', '尽管']
        logic_count = sum(1 for word in logic_words if word in text)
        score += min(0.15, logic_count * 0.05)
        
        return min(1.0, score)
    
    def select_model(self, context: str, instruction: str = '', task_type: str = 'general') -> str:
        """
        选择模型
        
        返回：'primary' / 'complex' / 'reasoning'
        """
        complexity = self.calculate_complexity(context, instruction)
        
        # 任务类型优先
        if task_type == 'reasoning':
            return 'reasoning'
        elif task_type == 'complex':
            return 'complex'
        
        
        # 根据复杂度选择
        if complexity >= self.config.reasoning_threshold:
            return 'reasoning'
        elif complexity >= self.config.complexity_threshold:
            return 'complex'
        else:
            return 'primary'
    
    def get_model_name(self, model_type: str) -> str:
        """获取模型名称"""
        mapping = {
            'primary': self.config.primary_model,
            'complex': self.config.complex_model,
            'reasoning': self.config.reasoning_model
        }
        return mapping.get(model_type, self.config.primary_model)

class BatchLLM:
    """批处理 LLM 调用"""
    
    def __init__(self, config: LLMConfig, router: SmartRouter):
        self.config = config
        self.router = router
        self.client = _create_ollama_client()
        self.queue: asyncio.Queue = None
        self.results: Dict[str, Any] = {}
        self.events: Dict[str, asyncio.Event] = {}
        self.batch_task: Optional[asyncio.Task] = None
    
    async def _ensure_queue(self):
        """确保队列初始化"""
        if self.queue is None:
            self.queue = asyncio.Queue()
            if self.config.batch_enabled:
                self.batch_task = asyncio.create_task(self._process_batch())
    
    async def submit(self, task_id: str, model: str, messages: List[Dict], **kwargs) -> str:
        """提交任务到批处理队列"""
        await self._ensure_queue()
        
        # 创建完成事件
        self.events[task_id] = asyncio.Event()
        
        # 加入队列
        await self.queue.put((task_id, model, messages, kwargs))
        
        # 等待结果
        await self.events[task_id].wait()
        
        # 获取结果
        result = self.results.pop(task_id, None)
        self.events.pop(task_id, None)
        
        return result
    
    async def _process_batch(self):
        """处理批处理队列"""
        while True:
            try:
                # 收集一批任务
                batch = []
                start_time = time.time()
                
                while len(batch) < self.config.batch_max_size:
                    try:
                        task = await asyncio.wait_for(
                            self.queue.get(),
                            timeout=self.config.batch_max_wait_ms / 1000
                        )
                        batch.append(task)
                    except asyncio.TimeoutError:
                        break
                
                if not batch:
                    await asyncio.sleep(0.1)
                    continue
                
                # 按模型分组
                by_model = {}
                for task_id, model, messages, kwargs in batch:
                    if model not in by_model:
                        by_model[model] = []
                    by_model[model].append((task_id, messages, kwargs))
                
                # 每组调用一次 LLM
                for model, tasks in by_model.items():
                    if len(tasks) == 1:
                        # 单个任务，直接调用
                        task_id, messages, kwargs = tasks[0]
                        response = await asyncio.to_thread(
                            self.client.chat,
                            model=model,
                            messages=messages,
                            options=kwargs.get('options', {})
                        )
                        result = response['message']['content']
                    else:
                        # 多个任务，合并调用
                        combined_prompt = self._merge_prompts([m for _, m, _ in tasks])
                        response = await asyncio.to_thread(
                            self.client.chat,
                            model=model,
                            messages=[{'role': 'user', 'content': combined_prompt}],
                            options=kwargs.get('options', {})
                        )
                        results = self._split_results(response['message']['content'], len(tasks))
                        for (task_id, _, _), result in zip(tasks, results):
                            self.results[task_id] = result
                            self.events[task_id].set()
                        continue
                    
                    # 存储结果
                    self.results[task_id] = result
                    self.events[task_id].set()
                
            except Exception as e:
                print(f"[BatchLLM Error] {e}")
                await asyncio.sleep(1)
    
    def _merge_prompts(self, prompts: List[List[Dict]]) -> str:
        """合并多个提示词"""
        merged = f"请依次处理以下{len(prompts)}个任务：\n\n"
        for i, messages in enumerate(prompts):
            content = messages[-1]['content'] if messages else ''
            merged += f"任务{i+1}: {content}\n"
        merged += "\n请按格式返回结果：\n任务 1: [结果]\n任务 2: [结果]\n..."
        return merged
    
    def _split_results(self, response: str, count: int) -> List[str]:
        """拆分结果"""
        # 简单按行拆分
        lines = response.strip().split('\n')
        results = []
        for line in lines:
            if ':' in line:
                results.append(line.split(':', 1)[1].strip())
        
        # 如果拆分失败，返回原始结果
        if len(results) != count:
            return [response] * count
        
        return results

class LocalLLM:
    """
    本地 LLM 客户端 - 完整集成
    
    功能:
    - 多模型支持
    - 智能路由
    - 语义缓存
    - 批处理优化
    """
    
    def __init__(self, config: LLMConfig = None):
        self.config = config or LLMConfig()
        self.client = _create_ollama_client()
        
        # 初始化组件
        self.router = SmartRouter(self.config)
        self.cache = SemanticCache(
            self.config.embedding_model,
            self.config.cache_similarity_threshold,
            self.config.cache_ttl
        ) if self.config.cache_enabled else None
        self.batch = BatchLLM(self.config, self.router)
        
        # 统计信息
        self.stats = {
            'total_calls': 0,
            'cache_hits': 0,
            'cache_misses': 0,
            'batch_calls': 0,
            'by_model': {
                self.config.primary_model: 0,
                self.config.complex_model: 0,
                self.config.reasoning_model: 0
            }
        }
    
    async def chat(self,
                   messages: List[Dict],
                   temperature: float = 0.7,
                   max_tokens: int = 1024,
                   task_type: str = 'general',
                   use_cache: bool = True,
                   use_batch: bool = True) -> str:
        """
        聊天调用
        
        Args:
            messages: 对话历史
            temperature: 温度 (0-1)
            max_tokens: 最大输出长度
            task_type: 任务类型 (general/reasoning/complex)
            use_cache: 是否使用缓存
            use_batch: 是否使用批处理
        
        Returns:
            LLM 响应文本
        """
        self.stats['total_calls'] += 1
        
        # 1. 检查缓存
        if use_cache and self.cache:
            cache_key = self._create_cache_key(messages)
            cached = self.cache.get(cache_key)
            if cached:
                self.stats['cache_hits'] += 1
                return cached
            self.stats['cache_misses'] += 1
        
        # 2. 选择模型
        context = '\n'.join([m.get('content', '') for m in messages])
        model_type = self.router.select_model(context, task_type=task_type)
        model_name = self.router.get_model_name(model_type)
        
        # 确保模型名称有效
        if not model_name or model_name == 'unknown':
            model_name = self.config.primary_model
        
        self.stats['by_model'][model_name] = self.stats['by_model'].get(model_name, 0) + 1
        
        # 3. 调用 LLM
        options = {
            'temperature': temperature,
            'num_predict': max_tokens
        }
        
        try:
            if use_batch and self.config.batch_enabled:
                # 批处理调用
                task_id = hashlib.md5(f"{time.time()}{messages}".encode()).hexdigest()[:12]
                response = await self.batch.submit(task_id, model_name, messages, options=options)
                self.stats['batch_calls'] += 1
            else:
                # 直接调用
                response_obj = await asyncio.to_thread(
                    self.client.chat,
                    model=model_name,
                    messages=messages,
                    options=options
                )
                response = response_obj['message']['content']
                
                # 如果响应为空，重试一次
                if not response or len(response.strip()) == 0:
                    response_obj = await asyncio.to_thread(
                        self.client.chat,
                        model=model_name,
                        messages=messages,
                        options=options
                    )
                    response = response_obj['message']['content']
        except Exception as e:
            # 错误时返回错误信息
            response = f"[LLM Error] {str(e)}"
        
        # 4. 缓存结果
        if use_cache and self.cache:
            self.cache.set(cache_key, response)
        
        return response
    
    def _create_cache_key(self, messages: List[Dict]) -> str:
        """创建缓存键"""
        content = '\n'.join([m.get('content', '') for m in messages])
        return content
    
    async def generate_reason(self, instruction: str, context: List[Dict]) -> str:
        """生成编辑理由"""
        prompt = f"""你是一个 AI 助手的自我改进系统。请分析以下改进指令，生成编辑理由。

指令：{instruction}

最近对话上下文：
{self._format_context(context)}

请分析：
1. 检测到的模式 (关键词出现频率、情感倾向)
2. 与历史记忆的一致性
3. 建议更新哪个记忆块 (human/persona/archival)
4. 置信度 (0-1)

输出格式：
检测模式：[分析结果]
一致性：[高/中/低]
建议更新：[记忆块类型]
置信度：[0-1]
理由：[详细说明]
"""
        
        messages = [{'role': 'user', 'content': prompt}]
        response = await self.chat(messages, temperature=0.3, max_tokens=500, task_type='complex')
        return response
    
    async def validate_improvement(self, improvement: Dict) -> Dict:
        """验证改进质量"""
        prompt = f"""你是一个 AI 系统质量评估师。请评估以下自我改进的质量。


改进内容：
{improvement.get('description', '')}

改进前状态：
{improvement.get('before_state', '')}

改进后状态：
{improvement.get('after_state', '')}

请从以下维度评分 (0-1):
1. 一致性：与现有系统是否一致
2. 清晰度：内容是否清晰明确
3. 相关性：与 AI 功能是否相关
4. 完整性：信息是否完整
5. 价值：对系统改进是否有价值

输出格式：
一致性：[0-1]
清晰度：[0-1]
相关性：[0-1]
完整性：[0-1]
价值：[0-1]
总体评分：[0-1]
验证结论：[通过/需要测试/拒绝]
详细理由：[说明]
"""
        
        messages = [{'role': 'user', 'content': prompt}]
        response = await self.chat(messages, temperature=0.3, max_tokens=800, task_type='complex')
        return self._parse_validation(response)
    
    async def detect_patterns(self, conversations: List[Dict]) -> Dict:
        """检测用户行为模式"""
        prompt = f"""你是一个用户行为分析师。请分析以下对话，识别用户的行为模式。

对话历史 (最近{len(conversations)}条):
{self._format_conversations(conversations)}

请识别：
1. 重复出现的偏好 (如代码风格、工具偏好等)
2. 情感倾向 (对某些事物的正面/负面态度)
3. 决策模式 (如何做决定)
4. 工作时间模式 (如果可识别)
5. 沟通风格偏好

输出格式：
识别的模式：
1. [模式名称] (强度：0-1)
   - 证据：[支持该模式的对话]
   - 建议：[如何处理这个模式]

2. [模式名称] (强度：0-1)
   ...
"""
        
        messages = [{'role': 'user', 'content': prompt}]
        response = await self.chat(messages, temperature=0.5, max_tokens=1500, task_type='reasoning')
        return self._parse_patterns(response)
    
    def _format_context(self, context: List[Dict]) -> str:
        """格式化对话上下文"""
        lines = []
        for msg in context[-10:]:
            if isinstance(msg, dict):
                role = '用户' if msg.get('role') == 'user' else 'AI'
                content = str(msg.get('content', ''))[:200]
            else:
                role = 'AI'
                content = str(msg)[:200]
            lines.append(f"{role}: {content}")
        return '\n'.join(lines)
    
    def _format_conversations(self, conversations: List[Dict]) -> str:
        """格式化对话历史"""
        return self._format_context(conversations)
    
    def _parse_validation(self, response: str) -> Dict:
        """解析验证结果"""
        lines = response.strip().split('\n')
        result = {}
        
        for line in lines:
            if ':' in line:
                key, value = line.split(':', 1)
                key = key.strip()
                value = value.strip()
                
                if key in ['一致性', '清晰度', '相关性', '完整性', '价值', '总体评分']:
                    try:
                        result[key] = float(value)
                    except:
                        result[key] = 0.5
                else:
                    result[key] = value
        
        return result
    
    def _parse_patterns(self, response: str) -> Dict:
        """解析模式识别结果"""
        return {'raw_response': response, 'patterns': []}
    
    def get_stats(self) -> Dict:
        """获取统计信息"""
        cache_stats = self.cache.stats() if self.cache else {}
        return {
            **self.stats,
            'cache': cache_stats,
            'config': {
                'primary_model': self.config.primary_model,
                'complex_model': self.config.complex_model,
                'reasoning_model': self.config.reasoning_model
            }
        }
    
    def get_model_info(self) -> Dict:
        """获取模型信息"""
        try:
            response = self.client.list()
            models_list = response.get('models', [])
            model_names = []
            for m in models_list:
                # Ollama API 返回格式可能是 'model' 或 'name'
                name = m.get('model') or m.get('name', 'unknown')
                model_names.append(name)
            return {
                'available': True,
                'models': model_names,
                'config': asdict(self.config)
            }
        except Exception as e:
            return {
                'available': False,
                'error': str(e),
                'message': '请确保 Ollama 服务正在运行 (ollama serve)'
            }
