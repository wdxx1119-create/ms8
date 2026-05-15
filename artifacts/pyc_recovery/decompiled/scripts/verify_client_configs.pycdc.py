# Source Generated with Decompyle++
# File: verify_client_configs.cpython-39.pyc (Python 3.9)

from __future__ import annotations
import argparse
import json
import os
from pathlib import Path
from common import expand

def _read_json(path = None):
    if not path.exists():
        return { }
    :
        if not path.exists():
            return { }
        
        return json.loads(path.read_text('utf-8', **('encoding',)))
    return json.loads(path.read_text('utf-8', **('encoding',)))
# WARNING: Decompyle incomplete


def _resolve_target_keys(cli_targets = None):
    if cli_targets.strip():
        return (lambda .0: [ x.strip() for x in .0 if x.strip() ])(cli_targets.split(','))
    raw = None.environ.get('OCMA_TARGET_KEYS', '').strip()
    if raw:
        return (lambda .0: [ x.strip() for x in .0 if x.strip() ])(raw.split(','))
    report_path = None('~/openclaw-memory-auto/runtime/connect_report.json')
    report = _read_json(report_path)
    keys = report.get('target_keys', [])
    if isinstance(keys, list) and keys:
        return (lambda .0: [ str(x).strip() for x in .0 if str(x).strip() ])(keys)


def main():
    parser = argparse.ArgumentParser('Verify MCP client configs.', **('description',))
    parser.add_argument('--targets', '', 'Comma-separated targets: claude_desktop,cursor,windsurf', **('default', 'help'))
    args = parser.parse_args()
    all_targets = {
        'claude_desktop': (expand('~/Library/Application Support/Claude/claude_desktop_config.json'), 'Claude Desktop'),
        'cursor': (expand('~/.cursor/mcp.json'), 'Cursor'),
        'windsurf': (expand('~/.windsurf/mcp.json'), 'Windsurf') }
    keys = _resolve_target_keys(args.targets)
    if keys:
        targets = (lambda .0 = None: pass# WARNING: Decompyle incomplete
)(all_targets.items())
    else:
        targets = all_targets
    ok_all = True
    report = { }
    for path, client_name in targets.items():
        obj = _read_json(path)
        server = { }.get('openclaw-memory') if isinstance(obj, dict) else None
        client_ok = isinstance(server, dict)
        if client_ok:
            cmd = str(server.get('command', ''))
            args = server.get('args') if isinstance(server.get('args'), list) else []
            env = server.get('env') if isinstance(server.get('env'), dict) else { }
            if bool(cmd) and len(args) >= 1:
                pass
            client_ok = env.get('OPENCLAW_MCP_CLIENT') == client_name
        report[key] = {
            'ok': client_ok,
            'path': str(path),
            'exists': path.exists(),
            'client': client_name }
        if ok_all:
            pass
        ok_all = client_ok
    out = {
        'ok': ok_all,
        'report': report,
        'next_step': '请重启 Claude Desktop/Cursor/Windsurf 以加载新配置' }
    print(json.dumps(out, False, 2, **('ensure_ascii', 'indent')))
    if ok_all:
        return 0

if __name__ == '__main__':
    raise SystemExit(main())

# [stderr]
Unsupported opcode: JUMP_IF_NOT_EXC_MATCH (210)
WARNING: Circular reference detected
Unsupported opcode: MAP_ADD (188)
