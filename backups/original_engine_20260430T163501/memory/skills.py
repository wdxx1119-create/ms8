"""
Skills System - Reusable knowledge modules (Letta-style)
"""
import json
import re
import shutil
from pathlib import Path
from typing import Dict, Optional, List
from datetime import datetime
from .config import get_config

class Skill:
    """Represents a reusable skill."""
    
    def __init__(self, name: str, description: str, scope: str = 'project'):
        self.name = name
        self.description = description
        self.scope = scope  # project, agent, global, built-in
        self.path: Optional[Path] = None
        self.content: Dict = {}
    
    def load(self) -> bool:
        """Load skill content from SKILL.md."""
        if not self.path or not self.path.exists():
            return False
        
        skill_file = self.path / 'SKILL.md'
        if not skill_file.exists():
            return False
        
        with open(skill_file, 'r', encoding='utf-8') as f:
            content = f.read()
        
        # Parse YAML frontmatter
        if content.startswith('---'):
            parts = content.split('---', 2)
            if len(parts) >= 3:
                frontmatter = parts[1].strip()
                body = parts[2].strip()
                
                # Parse YAML
                import yaml
                try:
                    yaml_data = yaml.safe_load(frontmatter)
                    
                    # Store parsed metadata at top level for easy access
                    self.content = {
                        'name': yaml_data.get('name', 'unknown'),
                        'description': yaml_data.get('description', ''),
                        'version': yaml_data.get('version', '1.0.0'),
                        'tags': yaml_data.get('tags', []),
                        'triggers': yaml_data.get('triggers', []),
                        'category': yaml_data.get('category', 'other'),
                        'tools': yaml_data.get('tools', []),
                        'frontmatter': frontmatter,
                        'body': body,
                        'resources': []
                    }
                    
                    # Load resources if they exist
                    resources_dir = self.path / 'resources'
                    if resources_dir.exists():
                        for resource_file in resources_dir.glob('*'):
                            with open(resource_file, 'r', encoding='utf-8') as f:
                                self.content['resources'].append({
                                    'name': resource_file.name,
                                    'content': f.read()
                                })
                    
                    return True
                except Exception as e:
                    print(f'Error parsing skill YAML: {e}')
                    return False
        
        return False
    
    def to_dict(self) -> Dict:
        return {
            'name': self.name,
            'description': self.description,
            'scope': self.scope,
            'path': str(self.path) if self.path else None,
            'content': self.content
        }

class SkillManager:
    """
    Manage reusable skills (Letta-style).
    
    Skill scopes:
    - project: .skills/ (specific to current project)
    - agent: ~/.openclaw/agents/{id}/skills/ (specific to one agent)
    - global: ~/.openclaw/skills/ (shared across all agents)
    - built-in: Bundled with OpenClaw
    """
    
    def __init__(self):
        self.config = get_config()
        self.workspace_dir = Path(self.config['memory_dir']).parent
        self.settings = self.config['settings']['memory'].get('skills_system', {})
        
        # Skill directories by scope
        self.skill_dirs = {
            'project': self.workspace_dir / '.skills',
            'agent': self.config['memory_dir'] / 'skills',
            'global': self.workspace_dir / 'skills',
            'bundled': self.workspace_dir / 'skills' / '_bundled',  # Built-in skills
        }
        
        # Ensure directories exist
        for dir_path in self.skill_dirs.values():
            dir_path.mkdir(parents=True, exist_ok=True)
        
        # Load all skills
        self.skills = self._load_all_skills()
    
    def _load_all_skills(self) -> List[Skill]:
        """Load skills from all directories."""
        skills = []
        
        for scope, dir_path in self.skill_dirs.items():
            if not dir_path.exists():
                continue
            
            for skill_dir in dir_path.iterdir():
                if skill_dir.is_dir() and (skill_dir / 'SKILL.md').exists():
                    skill = Skill(skill_dir.name, '', scope)
                    skill.path = skill_dir
                    if skill.load():
                        # Extract description from frontmatter
                        if 'frontmatter' in skill.content:
                            desc = self._extract_yaml_value(
                                skill.content['frontmatter'], 'description'
                            )
                            if desc:
                                skill.description = desc
                        skills.append(skill)
        
        return skills
    
    def _extract_yaml_value(self, yaml_str: str, key: str) -> Optional[str]:
        """Extract value from simple YAML."""
        for line in yaml_str.split('\n'):
            if line.startswith(f'{key}:'):
                return line.split(':', 1)[1].strip().strip('"\'')
        return None
    
    def list_skills(self) -> List[Dict]:
        """List all available skills."""
        return [skill.to_dict() for skill in self.skills]
    
    def get_skill(self, name: str) -> Optional[Skill]:
        """Get a skill by name."""
        for skill in self.skills:
            if skill.name.lower() == name.lower():
                return skill
        return None

    def _dedupe_preserve_order(self, values: List[str]) -> List[str]:
        deduped: List[str] = []
        seen = set()
        for value in values:
            normalized = value.strip()
            if not normalized:
                continue
            key = normalized.lower()
            if key in seen:
                continue
            seen.add(key)
            deduped.append(normalized)
        return deduped
    
    def create_skill(self, name: str, description: str, 
                     instructions: str, scope: str = 'project',
                     resources: Dict[str, str] = None,
                     metadata: Dict[str, object] = None) -> Dict:
        """
        Create a new skill.
        
        Args:
            name: Unique identifier
            description: What this skill teaches
            instructions: Core instructions for the skill
            scope: project/agent/global
            resources: Optional resource files {filename: content}
        
        Returns:
            Dict with status and skill info
        """
        if scope not in self.skill_dirs:
            return {
                'status': 'error',
                'error': f'Invalid scope: {scope}. Must be one of: {list(self.skill_dirs.keys())}'
            }

        existing = self.get_skill(name)
        allow_overwrite = bool(self.settings.get('allow_overwrite', False))
        auto_suffix = bool(self.settings.get('auto_suffix_on_conflict', True))
        if existing and not allow_overwrite:
            if not auto_suffix:
                return {
                    'status': 'error',
                    'error': f'Skill "{name}" already exists'
                }
            suffix = 1
            base = name
            while self.get_skill(f"{base}-{suffix}") is not None:
                suffix += 1
            name = f"{base}-{suffix}"
        
        skill_dir = self.skill_dirs[scope] / name
        skill_dir.mkdir(parents=True, exist_ok=True)
        metadata = metadata or {}
        tags = self._dedupe_preserve_order([str(item) for item in metadata.get('tags', [])])
        triggers = self._dedupe_preserve_order([str(item) for item in metadata.get('triggers', [])])
        category = str(metadata.get('category', 'workflow'))
        tools = self._dedupe_preserve_order([str(item) for item in metadata.get('tools', [])])

        # Create SKILL.md
        skill_file = skill_dir / 'SKILL.md'
        content = f"""---
name: {name}
description: {description}
version: 1.0.0
created: {datetime.now().isoformat()}
category: {category}
tags: {json.dumps(tags, ensure_ascii=False)}
triggers: {json.dumps(triggers, ensure_ascii=False)}
tools: {json.dumps(tools, ensure_ascii=False)}
---

# {name} Skill

{instructions}

## Usage

This skill can be loaded when working on related tasks.

## Resources

"""
        
        if resources:
            resources_dir = skill_dir / 'resources'
            resources_dir.mkdir(parents=True, exist_ok=True)
            
            for filename, resource_content in resources.items():
                resource_file = resources_dir / filename
                with open(resource_file, 'w', encoding='utf-8') as f:
                    f.write(resource_content)
                content += f"- `{filename}`\n"
        else:
            content += "No resources attached.\n"
        
        with open(skill_file, 'w', encoding='utf-8') as f:
            f.write(content)
        
        # Reload skills
        self.skills = self._load_all_skills()
        
        return {
            'status': 'success',
            'message': f'Created skill "{name}" in {scope} scope',
            'skill': {
                'name': name,
                'description': description,
                'path': str(skill_dir),
                'scope': scope
            }
        }
    
    def learn_skill_from_trajectory(self, trajectory: List[Dict], 
                                     skill_name: str,
                                     instructions: str = None) -> Dict:
        """
        Learn a new skill from a conversation trajectory.
        
        Args:
            trajectory: List of {role, content, tool_calls} from conversation
            skill_name: Name for the new skill
            instructions: Optional instructions for what to extract
        
        Returns:
            Dict with status and skill info
        """
        # Analyze trajectory to extract reusable pattern
        extracted = self._extract_pattern_from_trajectory(trajectory, skill_name, instructions)
        
        if not extracted:
            return {
                'status': 'error',
                'error': 'Could not extract reusable pattern from trajectory'
            }
        
        # Create skill
        return self.create_skill(
            name=skill_name,
            description=extracted['description'],
            instructions=extracted['instructions'],
            scope='project',
            resources=extracted.get('resources'),
            metadata=extracted.get('metadata'),
        )
    
    def _extract_pattern_from_trajectory(self, trajectory: List[Dict],
                                          skill_name: str,
                                          instructions: str = None) -> Optional[Dict]:
        """
        Extract reusable pattern from conversation trajectory.
        
        This is a simplified implementation. A full implementation would:
        1. Use LLM to analyze the trajectory
        2. Identify repeated patterns or successful workflows
        3. Extract generalizable instructions
        4. Create resource files from examples
        """
        if len(trajectory) < 2:
            return None

        normalized = []
        for msg in trajectory:
            normalized.append({
                'role': msg.get('role', 'unknown'),
                'content': str(msg.get('content', '')).strip(),
                'tool_calls': msg.get('tool_calls', []),
            })

        user_messages = [m['content'] for m in normalized if m['role'] == 'user' and m['content']]
        assistant_messages = [m['content'] for m in normalized if m['role'] == 'assistant' and m['content']]
        all_text = '\n'.join(f"{m['role']}: {m['content']}" for m in normalized)

        frequent_terms = self._extract_keywords(' '.join(user_messages + assistant_messages))
        tags = self._dedupe_preserve_order(frequent_terms[:6])
        triggers = self._derive_triggers(user_messages)
        tools = self._derive_tools(normalized)
        steps = self._derive_steps(normalized)
        pitfalls = self._derive_pitfalls(user_messages, assistant_messages)
        examples = self._derive_examples(normalized)
        category = self._derive_category(tags, tools, user_messages)
        resources = {
            'trajectory_excerpt.txt': all_text[:2000],
            'skill_outline.json': json.dumps({
                'triggers': triggers,
                'tools': tools,
                'steps': steps,
                'pitfalls': pitfalls,
            }, indent=2, ensure_ascii=False),
        }

        instruction_text = self._build_structured_skill_body(
            skill_name=skill_name,
            extracted_context=instructions or "Automatically extracted from conversation trajectory.",
            triggers=triggers,
            tools=tools,
            steps=steps,
            pitfalls=pitfalls,
            examples=examples,
        )

        return {
            'description': f'Structured workflow learned from conversation on {datetime.now().strftime("%Y-%m-%d")}',
            'instructions': instruction_text,
            'resources': resources,
            'metadata': {
                'tags': tags,
                'triggers': triggers,
                'category': category,
                'tools': tools,
            },
        }

    def _extract_keywords(self, text: str) -> List[str]:
        words = re.findall(r"[A-Za-z][A-Za-z0-9_-]{3,}", text.lower())
        stop = {'this', 'that', 'with', 'from', 'have', 'your', 'will', 'into', 'when', 'then', 'they'}
        ranked: List[str] = []
        counts: Dict[str, int] = {}
        for word in words:
            if word in stop:
                continue
            counts[word] = counts.get(word, 0) + 1
        for word, _count in sorted(counts.items(), key=lambda item: (-item[1], item[0])):
            ranked.append(word)
        return ranked

    def _derive_triggers(self, user_messages: List[str]) -> List[str]:
        triggers: List[str] = []
        for message in user_messages[:6]:
            cleaned = ' '.join(message.split())
            if cleaned:
                triggers.append(cleaned[:80])
        return self._dedupe_preserve_order(triggers[:4] or ['similar task request'])

    def _derive_tools(self, trajectory: List[Dict]) -> List[str]:
        tools: List[str] = []
        for msg in trajectory:
            for tool_call in msg.get('tool_calls', []) or []:
                name = tool_call.get('name') or tool_call.get('tool') or 'tool'
                if name not in tools:
                    tools.append(name)
        return self._dedupe_preserve_order(tools[:8])

    def _derive_steps(self, trajectory: List[Dict]) -> List[str]:
        steps: List[str] = []
        if trajectory:
            steps.append('Clarify the goal and constraints from the user request.')
        if any(msg.get('tool_calls') for msg in trajectory):
            steps.append('Inspect relevant files, data, or external state before changing anything.')
            steps.append('Use the discovered context to perform the task with the required tools.')
        if any(msg.get('role') == 'assistant' for msg in trajectory):
            steps.append('Summarize the outcome and note any remaining risks or follow-ups.')
        return steps[:5]

    def _derive_pitfalls(self, user_messages: List[str], assistant_messages: List[str]) -> List[str]:
        pitfalls: List[str] = []
        combined = ' '.join(user_messages + assistant_messages).lower()
        if 'error' in combined or 'failed' in combined:
            pitfalls.append('Watch for previously observed errors and validate the fix before finishing.')
        if 'windows' in combined or 'macos' in combined or 'linux' in combined:
            pitfalls.append('Check platform-specific paths and runtime assumptions.')
        pitfalls.append('Do not skip verification after making changes.')
        return self._dedupe_preserve_order(pitfalls[:4])

    def _derive_examples(self, trajectory: List[Dict]) -> List[str]:
        examples: List[str] = []
        for msg in trajectory:
            if msg.get('role') == 'user' and msg.get('content'):
                examples.append(msg['content'][:120])
            if len(examples) >= 2:
                break
        return examples

    def _derive_category(self, tags: List[str], tools: List[str], user_messages: List[str]) -> str:
        joined = ' '.join(tags + tools + user_messages).lower()
        if any(term in joined for term in ['sql', 'sqlite', 'database', 'migration']):
            return 'database'
        if any(term in joined for term in ['test', 'verify', 'assert']):
            return 'testing'
        if any(term in joined for term in ['deploy', 'release', 'ship']):
            return 'deployment'
        if any(term in joined for term in ['python', 'code', 'patch', 'refactor']):
            return 'code'
        return 'workflow'

    def _build_structured_skill_body(self, skill_name: str, extracted_context: str,
                                     triggers: List[str], tools: List[str],
                                     steps: List[str], pitfalls: List[str],
                                     examples: List[str]) -> str:
        lines = [
            f"This skill captures the reusable `{skill_name}` workflow.",
            "",
            "## Context",
            extracted_context,
            "",
            "## Triggers",
        ]
        lines.extend(f"- {trigger}" for trigger in triggers)
        lines.append("")
        lines.append("## Workflow")
        lines.extend(f"{index}. {step}" for index, step in enumerate(steps, start=1))
        lines.append("")
        lines.append("## Tools")
        if tools:
            lines.extend(f"- `{tool}`" for tool in tools)
        else:
            lines.append("- Use the tools already available in the current environment.")
        lines.append("")
        lines.append("## Pitfalls")
        lines.extend(f"- {pitfall}" for pitfall in pitfalls)
        if examples:
            lines.append("")
            lines.append("## Examples")
            lines.extend(f"- {example}" for example in examples)
        return '\n'.join(lines)
    
    def load_skill(self, name: str) -> Optional[str]:
        """
        Load a skill's full content (for injection into context).
        
        Returns:
            Formatted skill content or None if not found
        """
        skill = self.get_skill(name)
        if not skill:
            return None
        
        content = [f"# Skill: {skill.name}"]
        content.append(f"\n{skill.description}\n")
        
        if skill.content:
            if 'body' in skill.content:
                content.append(skill.content['body'])
            
            if 'resources' in skill.content and skill.content['resources']:
                content.append("\n## Resources\n")
                for resource in skill.content['resources']:
                    content.append(f"\n### {resource['name']}\n")
                    content.append(resource['content'])
        
        return '\n'.join(content)
    
    def delete_skill(self, name: str) -> Dict:
        """Delete a skill."""
        skill = self.get_skill(name)
        if not skill or not skill.path:
            return {
                'status': 'error',
                'error': f'Skill "{name}" not found'
            }
        
        try:
            shutil.rmtree(skill.path)
            self.skills = self._load_all_skills()
            return {
                'status': 'success',
                'message': f'Deleted skill "{name}"'
            }
        except Exception as e:
            return {
                'status': 'error',
                'error': f'Failed to delete skill: {str(e)}'
            }
