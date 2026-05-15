# Source Generated with Decompyle++
# File: smoke_test.cpython-39.pyc (Python 3.9)

from __future__ import annotations
import argparse
import json
import os
import sys
from pathlib import Path
from datetime import date, datetime, timezone
from typing import Any, Dict
from common import ROOT, write_json

def _json_safe(value = None):
    if isinstance(value, dict):
        return (lambda .0: pass# WARNING: Decompyle incomplete
)(value.items())
    if None(value, list):
        return (lambda .0: [ _json_safe(v) for v in .0 ])(value)
    if None(value, (datetime, date)):
        return value.isoformat()
    if None(value, Path):
        return str(value)


def _compact_value(value = None, max_len = None):
    if isinstance(value, dict):
        keep = {
            'count',
            'time',
            'server',
            'error',
            'query',
            'mode',
            'ok',
            'error_code'}
        out = { }
        for k, v in value.items():
            if k in keep:
                out[k] = _compact_value(v, max_len, **('max_len',))
                continue
                return out
            if None(value, list):
                return (lambda .0 = None: [ _compact_value(v, max_len, **('max_len',)) for v in .0 ])(value[:3])
            if None(value, str) and len(value) > max_len:
                return value[:max_len] + '...'
            return None


def main():
    parser = argparse.ArgumentParser('Run MCP smoke test.', **('description',))
    parser.add_argument('--compact', 'store_true', 'Print compact report for easier ops inspection', **('action', 'help'))
    args = parser.parse_args()
    os.environ['OPENCLAW_MCP_SMOKE'] = '1'
    os.environ['OPENCLAW_MCP_CLIENT'] = 'Claude Desktop'
    sys.path.insert(0, str(ROOT))
    server = mcp_server
    import mcp_server
    status_payload = server.memory_status()
    stamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')
    sample = f'''决定采用方案B，并将缓存配置改为 enabled=true。smoke={stamp}'''
    save = server.save_memory(sample, 'Claude Desktop', **('client_name',))
    search = server.search_memory('smoke', 3, **('top_k',))
    context = server.get_context('请总结最近的配置变更和决策')
    save_ok = bool(save.get('ok', False))
    save_useful = False
    if save_ok:
        mode = str(save.get('mode', ''))
        if mode == 'append_interaction':
            save_useful = True
        elif mode == 'auto_memory_pipeline':
            result = save.get('result', [])
            if isinstance(result, list):
                for item in result:
                    if not item:
                        pass
                    item_result = { }.get('result', { })
                    if not item_result:
                        pass
                    item_status = str({ }.get('status', ''))
                    if not item:
                        pass
                    if not { }.get('record'):
                        pass
                    record_status = str({ }.get('status', ''))
                    if item_status in frozenset({'saved', 'queued_review', 'success', 'saved_review'}):
                        save_useful = True
                    elif record_status in frozenset({'pending_review', 'redacted_accept', 'accepted'}):
                        save_useful = True
                    
                    report = {
                        'status': status_payload,
                        'save': save,
                        'save_useful': save_useful,
                        'search': search,
                        'search_ok': bool(search.get('ok', False)),
                        'context': context,
                        'context_ok': bool(context.get('ok', False)),
                        'compact': bool(args.compact) }
                    out = ROOT / 'runtime' / 'health.json'
                    write_json(out, _json_safe(report))
                    safe_status = _json_safe(status_payload)
                    safe_save = _json_safe(save)
                    safe_search = _json_safe(search)
                    safe_context = _json_safe(context)
    to_print = {
        'status': _compact_value(safe_status),
        'save': _compact_value(safe_save),
        'save_useful': save_useful,
        'search': _compact_value(safe_search),
        'search_ok': bool(search.get('ok', False)),
        'context': _compact_value(safe_context),
        'context_ok': bool(context.get('ok', False)) } if args.compact else _json_safe(report)
    print(json.dumps(to_print, False, 2, **('ensure_ascii', 'indent')))
    if not status_payload.get('ok'):
        return 1
    if not None:
        return 1
    if not None:
        return 1

if __name__ == '__main__':
    raise SystemExit(main())

# [stderr]
Unsupported opcode: MAP_ADD (188)
Warning: Stack history is not empty!
Warning: block stack is not empty!
