# Source Generated with Decompyle++
# File: adapter_llm.cpython-39.pyc (Python 3.9)

from __future__ import annotations
import os
import sys
from pathlib import Path
from typing import Dict
import yaml
ROOT = Path(__file__).resolve().parents[1]
CFG = ROOT / 'config' / 'mcp_config.yaml'

def _load_cfg():
    if CFG.exists():
        if not yaml.safe_load(CFG.read_text('utf-8', **('encoding',))):
            pass
        return { }

CONFIG = _load_cfg()
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
from integration_hooks.service_models import MemoryCandidate
from mcp_server.memory_service_interface import MemoryServiceInterface
SERVICE = MemoryServiceInterface.from_config(CONFIG)

def submit_memory_candidate(candidate = None):
    if not SERVICE.available():
        return {
            'ok': False,
            'reason': 'core_unavailable',
            'error': SERVICE.core_error,
            'error_code': 'E_CORE_UNAVAILABLE' }
    text = None(candidate.get('content', '')).strip()
    if not str(candidate.get('source', 'adapter:unknown')).strip():
        pass
    source = 'adapter:unknown'
    if not text:
        return {
            'ok': False,
            'reason': 'empty_content',
            'error_code': 'E_EMPTY_CONTENT' }
    if not candidate.get('metadata'):
        pass
    mem = None(source, text, dict({ }), str(candidate.get('kind', 'interaction_memory')), candidate.get('confidence', None), str(candidate.get('llm_summary', '')), **('source', 'content', 'metadata', 'kind', 'confidence', 'llm_summary'))
    return SERVICE.submit(mem)


def parse_event_rule_layer(event):
    candidate = {
        'source': event.get('source', 'adapter:event'),
        'content': event.get('text', ''),
        'metadata': {
            'timestamp': event.get('timestamp') },
        'kind': 'interaction_memory' }
    return candidate


def llm_enhance(candidate):
    text = str(candidate.get('content', ''))
    candidate['llm_summary'] = text[:80] + '...' if len(text) > 80 else text
    candidate['confidence'] = 0.95
    return candidate


def process_event(event):
    candidate = parse_event_rule_layer(event)
    candidate = llm_enhance(candidate)
    return submit_memory_candidate(candidate)

if __name__ == '__main__':
    sample_event = {
        'source': 'adapter:CustomToolX',
        'text': '用户操作日志示例，自动进入记忆 pipeline',
        'timestamp': '2026-04-15T10:00:00' }
    print(process_event(sample_event))
