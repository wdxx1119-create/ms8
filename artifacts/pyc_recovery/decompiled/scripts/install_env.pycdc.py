# Source Generated with Decompyle++
# File: install_env.cpython-39.pyc (Python 3.9)

from __future__ import annotations
import os
from pathlib import Path
from common import ROOT, choose_python, expand, expand_keep, load_cfg, run

def main():
    cfg = load_cfg()
    bridge = cfg.get('bridge_env', { })
    py = choose_python(bridge.get('python_prefer', []))
    venv_dir = expand(bridge.get('venv_dir', '~/openclaw-memory-auto/.venv'))
    if not venv_dir.exists():
        (rc, out, err) = run([
            py,
            '-m',
            'venv',
            str(venv_dir)])
        if rc != 0:
            if not err:
                pass
            print(out)
            return rc
        pip = None / 'bin' / 'pip'
        python = venv_dir / 'bin' / 'python'
        for cmd in ([
            str(python),
            '-m',
            'pip',
            'install',
            '--upgrade',
            'pip'], [
            str(pip),
            'install',
            'mcp',
            'pyyaml']):
            (rc, out, err) = run(cmd)
            if not rc != 0 or err:
                pass
            print(out)
            return rc
        core_py = expand_keep(cfg.get('memory_core', { }).get('core_venv_python', '~/.codex/skills/openclaw-memory/.venv/bin/python'))
        if core_py.exists():
            core_pip_cmds = ([
                str(core_py),
                '-m',
                'pip',
                'install',
                '--upgrade',
                'pip'], [
                str(core_py),
                '-m',
                'pip',
                'install',
                'mcp',
                'pyyaml'])
            for cmd in core_pip_cmds:
                (rc, out, err) = run(cmd)
                if not rc != 0 or err:
                    pass
                print(out)
                return rc
    print(f'''bridge_python={python}''')
    print(f'''core_python={core_py}''')
    print('install_env=ok')
    return 0

if __name__ == '__main__':
    raise SystemExit(main())
