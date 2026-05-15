# Source Generated with Decompyle++
# File: memory_service_interface.cpython-39.pyc (Python 3.9)

from __future__ import annotations
import json
import os
import sys
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional
from integration_hooks.service_models import MemoryCandidate
ERR_CORE_UNAVAILABLE = 'E_CORE_UNAVAILABLE'
ERR_PROFILE_NOT_FOUND = 'E_PROFILE_NOT_FOUND'
ERR_PROFILE_PARSE = 'E_PROFILE_PARSE'
ERR_PROFILE_UNKNOWN = 'E_PROFILE_UNKNOWN'

class MemoryServiceInterface:
    '''Thin service boundary between server/adapter and memory engine.'''
    
    def __init__(self = None, core = None, config = None, core_error = (None,)):
        self.core = core
        if not config:
            pass
        self.config = { }
        if not core_error:
            pass
        self.core_error = ''
        self.workspace = self._expand(self.config.get('memory_core', { }).get('workspace', '~/.codex/memories/openclaw-memory-workspace'))
        self.profile_cfg = self.config.get('resources', { }) if isinstance(self.config.get('resources', { }), dict) else { }

    
    def from_config(cls = None, config = None):
        if not config:
            pass
        cfg = { }
        python_path = os.path.expanduser(cfg.get('memory_core', { }).get('python_path', '~/.codex/skills/openclaw-memory/python'))
        if python_path not in sys.path:
            sys.path.insert(0, python_path)
        core = None
        core_error = ''
    # WARNING: Decompyle incomplete

    from_config = None(from_config)
    
    def available(self = None):
        return self.core is not None

    
    def submit(self = None, candidate = None):
        if not self.available():
            return self._core_unavailable()
        if not candidate.source:
            pass
        source = None('')
        if not candidate.content:
            pass
        text = str('')
    # WARNING: Decompyle incomplete

    
    def query(self = None, query = None, top_k = None):
        if not self.available():
            return self._core_unavailable()
        rows = self.core.retrieve_memories(query, top_k, **('top_k',))
        if not rows:
            pass
        normalized = (lambda .0 = None: [ self._normalize_result_row(row) for row in .0 ])([])
        :
            if not self.available():
                return self._core_unavailable()
            rows = self.core.retrieve_memories(query, top_k, **('top_k',))
            if not rows:
                pass
            normalized = (lambda .0 = None: [ self._normalize_result_row(row) for row in .0 ])([])
            
            return {
                'ok': True,
                'query': query,
                'count': len(normalized),
                'results': normalized }
        return {
            'ok': True,
            'query': query,
            'count': len(normalized),
            'results': normalized }
    # WARNING: Decompyle incomplete

    
    def context(self = None, message = None):
        if not self.available():
            return self._core_unavailable()
        payload = self.core.get_response_memory_context(message)
        :
            if not self.available():
                return self._core_unavailable()
            payload = self.core.get_response_memory_context(message)
            
            return {
                'ok': True,
                'context': self._json_safe(payload) }
        return {
            'ok': True,
            'context': self._json_safe(payload) }
    # WARNING: Decompyle incomplete

    
    def status(self = None):
        if not self.available():
            return self._core_unavailable()
        health = self.core.get_monitoring_status() if hasattr(self.core, 'get_monitoring_status') else { }
        :
            if not self.available():
                return self._core_unavailable()
            health = self.core.get_monitoring_status() if hasattr(self.core, 'get_monitoring_status') else { }
            
            return {
                'ok': True,
                'server': self.config.get('server', { }).get('name', 'openclaw-memory'),
                'time': datetime.utcnow().isoformat(),
                'health': self._json_safe(health) }
        return {
            'ok': True,
            'server': self.config.get('server', { }).get('name', 'openclaw-memory'),
            'time': datetime.utcnow().isoformat(),
            'health': self._json_safe(health) }
    # WARNING: Decompyle incomplete

    
    def profile(self = None, resource_key = None):
        if resource_key == 'long-term':
            p = self.workspace / 'MEMORY.md'
            if not p.exists():
                return self._profile_error(resource_key, 'MEMORY.md not found', ERR_PROFILE_NOT_FOUND)
            tail_chars = None._resource_int('long_term_tail_chars', 12000)
            content = p.read_text('utf-8', 'ignore', **('encoding', 'errors'))[-tail_chars:]
            return self._profile_ok(resource_key, content, p, **('path',))
        if None == 'profile':
            p = self.workspace / 'memory' / 'memory_blocks.json'
            if not p.exists():
                return self._profile_error(resource_key, 'profile not found', ERR_PROFILE_NOT_FOUND)
            obj = json.loads(p.read_text('utf-8', **('encoding',)))
    # WARNING: Decompyle incomplete

    
    def _core_unavailable(self = None):
        return {
            'ok': False,
            'error': f'''MemoryCore unavailable: {self.core_error}''',
            'error_code': ERR_CORE_UNAVAILABLE }

    
    def _resource_int(self = None, key = None, default = None):
        raw = self.profile_cfg.get(key, default)
    # WARNING: Decompyle incomplete

    
    def _profile_ok(self = None, resource_key = None, content = None, path = {
        'resource_key': 'str',
        'content': 'str',
        'path': 'Path',
        'return': 'Dict[str, Any]' }):
        return {
            'ok': True,
            'resource': resource_key,
            'content': content,
            'path': str(path) }

    
    def _profile_error(self = None, resource_key = None, error = None, error_code = {
        'resource_key': 'str',
        'error': 'str',
        'error_code': 'str',
        'return': 'Dict[str, Any]' }):
        return {
            'ok': False,
            'resource': resource_key,
            'error': error,
            'error_code': error_code,
            'content': error }

    
    def _normalize_submit_result(self = None, raw = None):
        items = raw if isinstance(raw, list) else []
        normalized_items = []
        accepted_count = 0
        review_count = 0
        rejected_count = 0
        saved_any = False
        for item in items:
            row = item if isinstance(item, dict) else { }
            result = row.get('result') if isinstance(row.get('result'), dict) else { }
            record = row.get('record') if isinstance(row.get('record'), dict) else { }
            if not result.get('status') and record.get('status'):
                pass
            status = str('')
            if status in frozenset({'saved', 'success', 'accepted'}):
                accepted_count += 1
                saved_any = True
            elif status in frozenset({'pending_review', 'saved_review', 'queued_review', 'redacted_accept'}):
                review_count += 1
                saved_any = True
            elif status:
                rejected_count += 1
            normalized_items.append({
                'result': self._json_safe(result),
                'record': self._normalize_result_row(record),
                'status': status })
        return {
            'items': normalized_items,
            'accepted_count': accepted_count,
            'review_count': review_count,
            'rejected_count': rejected_count,
            'saved_any': saved_any }

    
    def _normalize_result_row(self = None, row = None):
        obj = row if isinstance(row, dict) else { }
        content = obj.get('content')
        if content is None:
            content = obj.get('text')
        text = '' if content is None else str(content)
        return {
            'id': str(obj.get('id', '')) if obj.get('id') is not None else '',
            'source': str(obj.get('source', '')) if obj.get('source') is not None else '',
            'content': text,
            'text': text,
            'category': str(obj.get('category', '')) if obj.get('category') is not None else '',
            'confidence': obj.get('confidence', None),
            'date': self._json_safe(obj.get('date', '')),
            'status': str(obj.get('status', '')) if obj.get('status') is not None else '',
            'knowledge_tier': str(obj.get('knowledge_tier', '')) if obj.get('knowledge_tier') is not None else '',
            'trust_level': str(obj.get('trust_level', '')) if obj.get('trust_level') is not None else '',
            'raw': self._json_safe(obj) }

    
    def _json_safe(value = None):
        if isinstance(value, dict):
            return (lambda .0: pass# WARNING: Decompyle incomplete
)(value.items())
        if None(value, list):
            return (lambda .0: [ MemoryServiceInterface._json_safe(v) for v in .0 ])(value)
        if None(value, (datetime, date)):
            return value.isoformat()

    _json_safe = None(_json_safe)
    
    def _expand(p = None):
        return Path(p).expanduser().resolve()

    _expand = None(_expand)


# [stderr]
Unsupported opcode: JUMP_IF_NOT_EXC_MATCH (210)
Unsupported opcode: JUMP_IF_NOT_EXC_MATCH (210)
Unsupported opcode: JUMP_IF_NOT_EXC_MATCH (210)
WARNING: Circular reference detected
Unsupported opcode: JUMP_IF_NOT_EXC_MATCH (210)
WARNING: Circular reference detected
Unsupported opcode: JUMP_IF_NOT_EXC_MATCH (210)
WARNING: Circular reference detected
Unsupported opcode: JUMP_IF_NOT_EXC_MATCH (210)
Unsupported opcode: JUMP_IF_NOT_EXC_MATCH (210)
Unsupported opcode: MAP_ADD (188)
