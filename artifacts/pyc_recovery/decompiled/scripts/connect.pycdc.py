# Source Generated with Decompyle++
# File: connect.cpython-39.pyc (Python 3.9)

from __future__ import annotations
import argparse
import json
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from common import ROOT, expand, expand_keep, load_cfg, snapshot_config, write_json
from integration_hooks.service_models import ConnectResult

def _now():
    return datetime.now(timezone.utc).isoformat()


def _run(cmd = None, env = None, timeout = None):
    if not env:
        pass
    proc = subprocess.run(cmd, str(ROOT), os.environ.copy(), True, True, timeout, **('cwd', 'env', 'capture_output', 'text', 'timeout'))
    return (proc.returncode, proc.stdout, proc.stderr)


def _pick_core_python(cfg = None):
    core = expand_keep(cfg.get('memory_core', { }).get('core_venv_python', '~/.codex/skills/openclaw-memory/.venv/bin/python'))
    if core.exists():
        return str(core)
    bridge = None(cfg.get('bridge_env', { }).get('venv_dir', '~/openclaw-memory-auto/.venv')) / 'bin' / 'python'
    if bridge.exists():
        return str(bridge)
    return None.executable


def _detect_targets(cfg = None):

# [stderr]
PycBuffer::getByte(): Unexpected end of stream
