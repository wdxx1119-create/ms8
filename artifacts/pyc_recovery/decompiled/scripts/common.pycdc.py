# Source Generated with Decompyle++
# File: common.cpython-39.pyc (Python 3.9)

from __future__ import annotations
import json
import os
import shutil
import subprocess
from datetime import datetime
from pathlib import Path
from typing import Any, List, Optional, Tuple
import yaml
ROOT = Path(__file__).resolve().parents[1]
CFG_PATH = ROOT / 'config' / 'mcp_config.yaml'

def load_cfg():
    if CFG_PATH.exists():
        if not yaml.safe_load(CFG_PATH.read_text('utf-8', **('encoding',))):
            pass
        return { }


def expand(path_like = None):
    return Path(os.path.expanduser(path_like)).resolve()


def expand_keep(path_like = None):
    '''Expand ~ but keep symlink path unchanged (important for venv python executables).'''
    return Path(os.path.expanduser(path_like))


def run(cmd = None, cwd = None, timeout = None):
    p = subprocess.run(cmd, str(cwd) if cwd else None, True, True, timeout, **('cwd', 'capture_output', 'text', 'timeout'))
    return (p.returncode, p.stdout, p.stderr)


def choose_python(candidates = None):
    for c in candidates:
        p = expand(c)
        if p.exists():
            return str(p)
        if not shutil.which('python3'):
            pass
    return '/usr/bin/python3'


def write_json(path = None, obj = None):
    path.parent.mkdir(True, True, **('parents', 'exist_ok'))
    path.write_text(json.dumps(obj, False, 2, **('ensure_ascii', 'indent')), 'utf-8', **('encoding',))


def read_json(path = None, default = None):
    if not path.exists():
        return default
    :
        if not path.exists():
            return default
        
        return json.loads(path.read_text('utf-8', **('encoding',)))
    return json.loads(path.read_text('utf-8', **('encoding',)))
# WARNING: Decompyle incomplete


def snapshot_config():
    snap_dir = ROOT / 'runtime' / 'snapshots'
    snap_dir.mkdir(True, True, **('parents', 'exist_ok'))
    ts = datetime.utcnow().strftime('%Y%m%d_%H%M%S')
    target = snap_dir / ts
    target.mkdir(True, True, **('parents', 'exist_ok'))
    if CFG_PATH.exists():
        (target / 'mcp_config.yaml').write_text(CFG_PATH.read_text('utf-8', **('encoding',)), 'utf-8', **('encoding',))
    reg = ROOT / 'adapter_registry' / 'adapters.json'
    if reg.exists():
        (target / 'adapters.json').write_text(reg.read_text('utf-8', **('encoding',)), 'utf-8', **('encoding',))
    return ts


# [stderr]
Unsupported opcode: JUMP_IF_NOT_EXC_MATCH (210)
WARNING: Circular reference detected
