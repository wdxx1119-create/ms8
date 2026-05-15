# Source Generated with Decompyle++
# File: generate_client_configs.cpython-39.pyc (Python 3.9)

from __future__ import annotations
import json
from pathlib import Path
from common import ROOT, expand, expand_keep, load_cfg

def main():
    cfg = load_cfg()
    bridge_venv = expand(cfg.get('bridge_env', { }).get('venv_dir', '~/openclaw-memory-auto/.venv'))
    bridge_py = bridge_venv / 'bin' / 'python'
    core_py = expand_keep(cfg.get('memory_core', { }).get('core_venv_python', '~/.codex/skills/openclaw-memory/.venv/bin/python'))
    py = core_py if core_py.exists() else bridge_py
    server_file = ROOT / 'mcp_server' / 'mcp_server.py'
    out_dir = ROOT / 'runtime' / 'client_snippets'
    out_dir.mkdir(True, True, **('parents', 'exist_ok'))
    snippet = {
        'mcpServers': {
            'openclaw-memory': {
                'command': str(py),
                'args': [
                    str(server_file)],
                'env': {
                    'OPENCLAW_MCP_CLIENT': 'CLIENT_NAME' } } } }
    for name in ('claude_desktop_config.json', 'cursor_mcp.json', 'windsurf_mcp.json'):
        (out_dir / name).write_text(json.dumps(snippet, False, 2, **('ensure_ascii', 'indent')), 'utf-8', **('encoding',))
    print(f'''snippets_dir={out_dir}''')
    print('generate_client_configs=ok')
    return 0

if __name__ == '__main__':
    raise SystemExit(main())
