#!/usr/bin/env python3
"""Reinstall built-in skills into the active workspace."""
from pathlib import Path
import shutil

from .config import get_config
from .core import MemoryCore

# Remove old bundled directory
bundled_dir = get_config()['memory_dir'].parent / 'skills' / '_bundled'
if bundled_dir.exists():
    shutil.rmtree(bundled_dir)
    print(f'Removed {bundled_dir}')

# Reinstall built-in skills
memory = MemoryCore()

print('Installing all built-in skills...')
result = memory.install_all_built_in_skills()
print(f'Result: {result["status"]}')
print(f'Message: {result.get("message", "N/A")}')

# Verify installation
print(f'\nBundled dir exists: {bundled_dir.exists()}')
if bundled_dir.exists():
    skills = list(bundled_dir.iterdir())
    print(f'Skills installed: {len(skills)}')
    for s in skills:
        skill_file = s / 'SKILL.md'
        if skill_file.exists():
            with open(skill_file, 'r', encoding='utf-8') as f:
                first_line = f.readline().strip()
            print(f'  - {s.name} (first line: {repr(first_line)})')
        else:
            print(f'  - {s.name} (NO SKILL.md)')
