# Source Generated with Decompyle++
# File: rollback_client_configs.cpython-39.pyc (Python 3.9)

from __future__ import annotations
import argparse
import json
import os
from pathlib import Path
from common import ROOT, expand

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


def _resolve_target_keys(cli_targets = None):
    if cli_targets.strip():
        return (lambda .0: [ x.strip() for x in .0 if x.strip() ])(cli_targets.split(','))
    raw = None.environ.get('OCMA_TARGET_KEYS', '').strip()
    if raw:
        return (lambda .0: [ x.strip() for x in .0 if x.strip() ])(raw.split(','))


def main():
    parser = argparse.ArgumentParser('Rollback MCP client configs.', **('description',))
    parser.add_argument('--targets', '', 'Comma-separated targets: claude_desktop,cursor,windsurf', **('default', 'help'))
    args = parser.parse_args()
    snap_root = ROOT / 'runtime' / 'snapshots' / 'client_configs'
    snaps = sorted((lambda .0: [ p for p in .0 if p.is_dir() ])(snap_root.glob('*')))
    if not snaps:
        print('rollback_client_configs=no_snapshot')
        return 1
    latest = None[-1]
    targets = {
        'claude_desktop': expand('~/Library/Application Support/Claude/claude_desktop_config.json'),
        'cursor': expand('~/.cursor/mcp.json'),
        'windsurf': expand('~/.windsurf/mcp.json') }
    keys = _resolve_target_keys(args.targets)
    if keys:
        targets = (lambda .0 = None: pass# WARNING: Decompyle incomplete
)(targets.items())
    if not targets:
        print('rollback_client_configs=no_targets')
        return 1
    restored = None
    for key, target in targets.items():
        backup = latest / f'''{key}.backup.json'''
        if backup.exists():
            target.parent.mkdir(True, True, **('parents', 'exist_ok'))
            target.write_text(backup.read_text('utf-8', **('encoding',)), 'utf-8', **('encoding',))
            restored[key] = 'full_restore'
            continue
        obj = _read_json(target)
        mcp_servers = obj.get('mcpServers') if isinstance(obj, dict) else None
        if isinstance(mcp_servers, dict) and 'openclaw-memory' in mcp_servers:
            mcp_servers.pop('openclaw-memory', None)
            obj['mcpServers'] = mcp_servers
            _write_json(target, obj)
            restored[key] = 'entry_removed'
            continue
        restored[key] = 'no_change'
    out = {
        'rollback_snapshot': latest.name,
        'restored': restored }
    print(json.dumps(out, False, 2, **('ensure_ascii', 'indent')))
    return 0

if __name__ == '__main__':
    raise SystemExit(main())

# [stderr]
Unsupported opcode: JUMP_IF_NOT_EXC_MATCH (210)
WARNING: Circular reference detected
Unsupported opcode: MAP_ADD (188)
