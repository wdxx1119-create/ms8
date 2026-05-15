# Source Generated with Decompyle++
# File: mcp_server.cpython-39.pyc (Python 3.9)

import json
import os
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Optional
import yaml
from integration_hooks.service_models import MemoryCandidate
from mcp.server.fastmcp import FastMCP
from mcp_server.memory_service_interface import MemoryServiceInterface
ROOT = Path(__file__).resolve().parents[1]
CONFIG_PATH = ROOT / 'config' / 'mcp_config.yaml'
ERR_WRITE_DENIED = 'E_WRITE_DENIED'
ERR_CLIENT_MISMATCH = 'E_CLIENT_MISMATCH'
ERR_CLIENT_NOT_REGISTERED = 'E_CLIENT_NOT_REGISTERED'
ERR_CLIENT_TOKEN_MISMATCH = 'E_CLIENT_TOKEN_MISMATCH'
ERR_CORE_UNAVAILABLE = 'E_CORE_UNAVAILABLE'

def _expand(p = None):
    return Path(os.path.expanduser(p)).resolve()


def _load_config():
    if CONFIG_PATH.exists():
        if not yaml.safe_load(CONFIG_PATH.read_text('utf-8', **('encoding',))):
            pass
        return { }

CONFIG = _load_config()
SERVICE = MemoryServiceInterface.from_config(CONFIG)

def _audit(event = None):
    p = _expand(CONFIG.get('runtime', { }).get('audit_log', '~/openclaw-memory-auto/logs/audit.log'))
    p.parent.mkdir(True, True, **('parents', 'exist_ok'))
# WARNING: Decompyle incomplete


def _load_registry():
    p = ROOT / 'adapter_registry' / 'adapters.json'
    if not p.exists():
        return { }
    obj = json.loads(p.read_text('utf-8', **('encoding',)))
    if isinstance(obj, dict):
        pass
    return obj
# WARNING: Decompyle incomplete


def _get_client_name(client_name = None):
    env_client = str(os.environ.get('OPENCLAW_MCP_CLIENT', '')).strip()
    if not client_name:
        pass
    arg_client = str('').strip()
    default_name = str(CONFIG.get('security', { }).get('default_client_name', 'unknown')).strip()
    if arg_client and env_client and arg_client != env_client:
        return '__mismatch__'
    if not None and env_client:
        pass
    return default_name


def _source_tag(client_name = None):
    return f'''mcp:{client_name}'''


def _enforce_client_token(client_name = None):
    tokens = CONFIG.get('security', { }).get('client_tokens', { })
    if not isinstance(tokens, dict):
        return None
    expected = None(tokens.get(client_name, '')).strip()
    if not expected:
        return None
    actual = None(os.environ.get('OPENCLAW_MCP_CLIENT_TOKEN', '')).strip()
    if actual or actual != expected:
        return ERR_CLIENT_TOKEN_MISMATCH


def _write_allowed(client_name = None):

# [stderr]
Unsupported opcode: DICT_UPDATE (214)
Unsupported opcode: JUMP_IF_NOT_EXC_MATCH (210)
PycBuffer::getByte(): Unexpected end of stream
