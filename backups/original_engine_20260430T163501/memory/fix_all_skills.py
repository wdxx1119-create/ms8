#!/usr/bin/env python3
"""Fix bundled SKILL.md files to have proper YAML frontmatter."""
from pathlib import Path

from .config import get_config


bundled_dir = get_config()['memory_dir'].parent / 'skills' / '_bundled'

if not bundled_dir.exists():
    print(f'Error: {bundled_dir} does not exist')
    sys.exit(1)

fixed_count = 0

for skill_dir in bundled_dir.iterdir():
    if not skill_dir.is_dir():
        continue
    
    skill_file = skill_dir / 'SKILL.md'
    if not skill_file.exists():
        print(f'Skip {skill_dir.name}: No SKILL.md')
        continue
    
    with open(skill_file, 'r', encoding='utf-8') as f:
        content = f.read()
    
    # Check if already has proper format
    if content.startswith('---\n'):
        # Check if has closing ---
        lines = content.split('\n')
        has_closing = False
        insert_pos = 0
        
        for i, line in enumerate(lines):
            if line.strip() == '---' and i > 0:
                has_closing = True
                break
            if line.startswith('# '):
                insert_pos = i
                break
        
        if has_closing:
            print(f'OK: {skill_dir.name}')
            continue
        
        # Need to add closing ---
        if insert_pos > 0:
            lines.insert(insert_pos, '---')
            content = '\n'.join(lines)
            with open(skill_file, 'w', encoding='utf-8') as f:
                f.write(content)
            print(f'Fixed (added closing ---): {skill_dir.name}')
            fixed_count += 1
    else:
        # Missing opening ---
        # Find where to insert closing ---
        lines = content.split('\n')
        insert_pos = 0
        
        for i, line in enumerate(lines):
            if line.startswith('# '):
                insert_pos = i
                break
        
        if insert_pos > 0:
            lines.insert(insert_pos, '---')
        
        content = '---\n' + '\n'.join(lines)
        
        with open(skill_file, 'w', encoding='utf-8') as f:
            f.write(content)
        
        print(f'Fixed (added opening ---): {skill_dir.name}')
        fixed_count += 1

print(f'\nTotal fixed: {fixed_count} files')

# Verify all files
print('\nVerification:')
for skill_dir in bundled_dir.iterdir():
    if skill_dir.is_dir():
        skill_file = skill_dir / 'SKILL.md'
        if skill_file.exists():
            with open(skill_file, 'r', encoding='utf-8') as f:
                first_line = f.readline().strip()
            
            # Check for closing ---
            with open(skill_file, 'r', encoding='utf-8') as f:
                content = f.read()
                has_closing = '\n---\n' in content or content.endswith('\n---')
            
            status = 'OK' if first_line == '---' and has_closing else 'FAIL'
            print(f'  [{status}] {skill_dir.name}')
