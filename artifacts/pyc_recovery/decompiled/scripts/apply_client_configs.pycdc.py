# Source Generated with Decompyle++
# File: apply_client_configs.cpython-39.pyc (Python 3.9)

from __future__ import annotations
import json
import os
from datetime import datetime
from pathlib import Path
from typing import Dict, Tuple
from common import ROOT, expand, expand_keep, load_cfg

def _read_json(path = None):
    if not path.exists():
        return { }
    :
        if not path.exists():
            return { }
        
        return json.loads(path.read_text('utf-8', **('encoding',)))
    return json.loads(path.read_text('utf-8', **('encoding',)))
# WARNING: Decompyle incomplete


def _write_json(path = None, payload = None):
    path.parent.mkdir(True, True, **('parents', 'exist_ok'))
    path.write_text(json.dumps(payload, False, 2, **('ensure_ascii', 'indent')), 'utf-8', **('encoding',))


def _targets():
    all_targets = {
        'claude_desktop': (expand('~/Library/Application Support/Claude/claude_desktop_config.json'), 'Claude Desktop'),
        'cursor': (expand('~/.cursor/mcp.json'), 'Cursor'),
        'windsurf': (expand('~/.windsurf/mcp.json'), 'Windsurf') }
    raw = os.environ.get('OCMA_TARGET_KEYS', '').strip()
    if not raw:
        return all_targets
    keys = (lambda 
# [stderr]
Unsupported opcode: JUMP_IF_NOT_EXC_MATCH (210)
WARNING: Circular reference detected
Error decompyling /Users/s/Library/Caches/com.apple.python/Users/s/openclaw-memory-auto/scripts/apply_client_configs.cpython-39.pyc: std::bad_cast
