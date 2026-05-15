# Source Generated with Decompyle++
# File: status.cpython-39.pyc (Python 3.9)

from __future__ import annotations
import json
from pathlib import Path
from typing import Any, Dict
from common import ROOT, read_json

def _tail_steps(connect_report = None):
    steps = connect_report.get('steps', []) if isinstance(connect_report, dict) else []
    out = { }
    for row in steps[-6:]:
        name = str(row.get('step', ''))
        if not name:
            continue
        out[name] = {
            'ok': bool(row.get('ok', False)),
            'rc': row.get('rc', None),
            'timestamp': row.get('timestamp', '') }
    return out


def main():
    health = read_json(ROOT / 'runtime' / 'health.json', { })
    connect = read_json(ROOT / 'runtime' / 'connect_report.json', { })
    scan = read_json(ROOT / 'runtime' / 'scan_report.json', { })
    if not health.get('status'):
        pass
    if bool({ }.get('ok', False)):
        pass
    smoke_ok = bool(health.get('save_useful', False))
    if not connect.get('result'):
        pass
    connect_ok = bool({ }.get('overall_ok', False))
    warnings = []
    if not connect:
        warnings.append('missing_connect_report')
    if not health:
        warnings.append('missing_health_report')
    if not connect and connect_ok:
        warnings.append('connect_not_ready')
    if not health and smoke_ok:
        warnings.append('smoke_not_useful')
    scan_decisions = scan.get('decisions', []) if isinstance(scan, dict) else []
    skipped = (lambda .0: [ d for d in .0 if d.get('action') == 'skipped' ])(scan_decisions)
    if connect_ok:
        pass
    report = {
        'ok': smoke_ok,
        'summary': {
            'connect_status': connect.get('status', 'unknown'),
            'connect_overall_ok': connect_ok,
            'smoke_ok': smoke_ok,
            'save_useful': bool(health.get('save_useful', False)),
            'target_keys': connect.get('target_keys', []),
            'detected_targets': connect.get('detected_targets', []) },
        'scan': {
            'scan_found': scan.get('scan_found', 0),
            'registered_added': scan.get('registered_added', 0),
            'skipped_count': len(skipped),
            'skip_reasons': (lambda .0: [ {
'name': s.get('name', ''),
'reason': s.get('skip_reason', ''),
'error_code': s.get('error_code', '') } for s in .0 ])(skipped)[:10] },
        'steps': _tail_steps(connect),
        'warnings': warnings,
        'paths': {
            'connect_report': str(ROOT / 'runtime' / 'connect_report.json'),
            'health': str(ROOT / 'runtime' / 'health.json'),
            'scan_report': str(ROOT / 'runtime' / 'scan_report.json') } }
    print(json.dumps(report, False, 2, **('ensure_ascii', 'indent')))
    if report['ok']:
        return 0

if __name__ == '__main__':
    raise SystemExit(main())
