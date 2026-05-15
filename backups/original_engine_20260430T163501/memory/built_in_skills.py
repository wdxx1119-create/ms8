"""
Built-in Skills Package
Pre-installed skills for common development tasks.
"""
from pathlib import Path
from typing import Dict, List
from .agent_skills_standard import AgentSkill, SkillMetadata, SkillCategory
from .config import get_config

class BuiltInSkills:
    """
    Built-in Skills Package.
    Provides pre-installed skills for common scenarios.
    """
    
    def __init__(self):
        self.config = get_config()
        self.bundled_skills_dir = self.config['memory_dir'].parent / 'skills' / '_bundled'
        self.bundled_skills_dir.mkdir(parents=True, exist_ok=True)
    
    def get_built_in_skills(self) -> List[Dict]:
        """Get list of all built-in skills."""
        return [
            {
                'name': 'python-development',
                'description': 'Python development best practices and patterns',
                'category': 'code',
                'installed': self._is_installed('python-development')
            },
            {
                'name': 'code-review',
                'description': 'Code review checklist and guidelines',
                'category': 'code',
                'installed': self._is_installed('code-review')
            },
            {
                'name': 'unit-testing',
                'description': 'Unit testing patterns and best practices',
                'category': 'testing',
                'installed': self._is_installed('unit-testing')
            },
            {
                'name': 'documentation',
                'description': 'Technical documentation writing guide',
                'category': 'documentation',
                'installed': self._is_installed('documentation')
            },
            {
                'name': 'database-migration',
                'description': 'Database migration workflows and safety',
                'category': 'database',
                'installed': self._is_installed('database-migration')
            },
            {
                'name': 'git-workflow',
                'description': 'Git branching and workflow strategies',
                'category': 'devops',
                'installed': self._is_installed('git-workflow')
            },
            {
                'name': 'api-design',
                'description': 'RESTful API design principles',
                'category': 'backend',
                'installed': self._is_installed('api-design')
            },
            {
                'name': 'security-checklist',
                'description': 'Security best practices checklist',
                'category': 'security',
                'installed': self._is_installed('security-checklist')
            },
            {
                'name': 'docker-basics',
                'description': 'Docker containerization basics',
                'category': 'devops',
                'installed': self._is_installed('docker-basics')
            },
            {
                'name': 'frontend-react',
                'description': 'React development patterns',
                'category': 'frontend',
                'installed': self._is_installed('frontend-react')
            }
        ]
    
    def _is_installed(self, skill_name: str) -> bool:
        """Check if a built-in skill is installed."""
        skill_path = self.bundled_skills_dir / skill_name
        return (skill_path / 'SKILL.md').exists()
    
    def install_built_in(self, skill_name: str) -> Dict:
        """
        Install a built-in skill.
        
        Args:
            skill_name: Name of skill to install
        
        Returns:
            Dict with installation status
        """
        skill_data = self._get_skill_content(skill_name)
        if not skill_data:
            return {
                'status': 'error',
                'error': f'Built-in skill "{skill_name}" not found'
            }
        
        skill_dir = self.bundled_skills_dir / skill_name
        skill_dir.mkdir(parents=True, exist_ok=True)
        
        # Create SKILL.md
        skill_file = skill_dir / 'SKILL.md'
        with open(skill_file, 'w', encoding='utf-8') as f:
            f.write(skill_data['frontmatter'])
            f.write('\n\n')
            f.write(skill_data['content'])
        
        # Create resources if any
        if skill_data.get('resources'):
            resources_dir = skill_dir / 'resources'
            resources_dir.mkdir(parents=True, exist_ok=True)
            
            for name, content in skill_data['resources'].items():
                with open(resources_dir / name, 'w', encoding='utf-8') as f:
                    f.write(content)
        
        return {
            'status': 'success',
            'message': f'Installed built-in skill "{skill_name}"',
            'path': str(skill_dir)
        }
    
    def _get_skill_content(self, skill_name: str) -> Dict:
        """Get built-in skill content."""
        skills = {
            'python-development': {
                'frontmatter': '''---
name: python-development
description: Python development best practices and patterns
version: 1.0.0
author: OpenClaw
license: MIT
category: code
tags:
  - python
  - development
  - best-practices
tools:
  - read
  - write
  - run
triggers:
  - "python code"
  - "write python"
  - "python function"
  - /python/i
---''',
                'content': ''' Python Development Skill

## Overview
This skill provides Python development best practices and patterns.

## Code Style
1. Follow PEP 8 style guide
2. Use 4 spaces for indentation
3. Maximum line length: 88 characters
4. Use type hints for function signatures

## Best Practices
1. Write docstrings for all public functions
2. Use context managers for resources
3. Handle exceptions appropriately
4. Write unit tests for critical code

## Common Patterns

### Function Template
```python
def function_name(param1: type, param2: type) -> ReturnType:
    """Brief description.
    
    Args:
        param1: Description
        param2: Description
    
    Returns:
        Description
    """
    pass
```

### Class Template
```python
class ClassName:
    """Class description."""
    
    def __init__(self, param: type):
        """Initialize."""
        self.param = param
```
''',
                'resources': {
                    'pep8_cheatsheet.txt': 'PEP 8 Style Guide Quick Reference\n...'
                }
            },
            
            'code-review': {
                'frontmatter': '''name: code-review
description: Code review checklist and guidelines
version: 1.0.0
author: OpenClaw
license: MIT
category: code
tags:
  - code-review
  - quality
  - best-practices
tools:
  - read
  - grep
triggers:
  - "code review"
  - "review this code"
  - "check code quality"''',
                'content': '''# Code Review Skill

## Code Review Checklist

### Functionality
- [ ] Code does what it's supposed to do
- [ ] Edge cases are handled
- [ ] Error handling is appropriate
- [ ] No logic errors

### Code Quality
- [ ] Follows style guidelines
- [ ] No code duplication
- [ ] Functions are small and focused
- [ ] Variable names are descriptive

### Testing
- [ ] Unit tests are included
- [ ] Tests cover edge cases
- [ ] Tests are readable and maintainable

### Documentation
- [ ] Code is self-documenting
- [ ] Complex logic has comments
- [ ] Public APIs have docstrings

### Security
- [ ] No hardcoded credentials
- [ ] Input validation is present
- [ ] No SQL injection vulnerabilities
- [ ] No XSS vulnerabilities
'''
            },
            
            'unit-testing': {
                'frontmatter': '''name: unit-testing
description: Unit testing patterns and best practices
version: 1.0.0
author: OpenClaw
license: MIT
category: testing
tags:
  - testing
  - unit-tests
  - pytest
tools:
  - read
  - write
  - run
triggers:
  - "unit test"
  - "write tests"
  - "test coverage"''',
                'content': '''# Unit Testing Skill

## Testing Principles

### FIRST
- **F**ast: Tests should run quickly
- **I**ndependent: Tests should not depend on each other
- **R**epeatable: Tests should produce same results
- **S**elf-validating: Tests should have clear pass/fail
- **T**imely: Tests should be written before or with code

## Test Structure (AAA Pattern)

```python
def test_example():
    # Arrange
    input_data = prepare_data()
    
    # Act
    result = function_under_test(input_data)
    
    # Assert
    assert result == expected_value
```

## Best Practices

1. Test one thing per test
2. Use descriptive test names
3. Keep tests simple and readable
4. Test behavior, not implementation
5. Use fixtures for common setup
'''
            }
        }
        
        return skills.get(skill_name)
    
    def install_all_built_in(self) -> Dict:
        """Install all built-in skills."""
        results = []
        for skill in self.get_built_in_skills():
            if not skill['installed']:
                result = self.install_built_in(skill['name'])
                results.append(result)
        
        return {
            'status': 'success',
            'message': f'Installed {len(results)} built-in skills',
            'details': results
        }

class SkillDiscovery:
    """
    Skill Discovery System.
    Intelligently suggests relevant skills based on context.
    """
    
    def __init__(self, skill_manager):
        self.skill_manager = skill_manager
    
    def get_relevant_skills(self, context: str, top_k: int = 3) -> List[Dict]:
        """
        Get skills relevant to current context.
        
        Args:
            context: Current conversation or task context
            top_k: Number of skills to return
        
        Returns:
            List of relevant skills with relevance scores
        """
        all_skills = self.skill_manager.skills
        scored_skills = []
        context_lower = context.lower()
        
        for skill in all_skills:
            # Extract skill metadata (handle both Skill and AgentSkill objects)
            skill_name = None
            skill_description = None
            skill_tags = []
            skill_triggers = []
            skill_category = None
            has_matches_trigger = False
            
            # Case 1: AgentSkill object (from agent_skills_standard.py)
            if hasattr(skill, 'metadata') and skill.metadata:
                skill_name = skill.metadata.name
                skill_description = skill.metadata.description
                skill_tags = skill.metadata.tags
                skill_triggers = skill.metadata.triggers
                skill_category = skill.metadata.category.value if hasattr(skill.metadata.category, 'value') else str(skill.metadata.category)
                if hasattr(skill, 'matches_trigger'):
                    has_matches_trigger = skill.matches_trigger(context)
            
            # Case 2: Skill object (from skills.py) - parse from content
            elif hasattr(skill, 'content') and skill.content:
                skill_name = skill.content.get('name', skill.name if hasattr(skill, 'name') else 'unknown')
                skill_description = skill.content.get('description', skill.description if hasattr(skill, 'description') else '')
                
                # Parse frontmatter for tags and triggers
                frontmatter = skill.content.get('frontmatter', '')
                if frontmatter:
                    import yaml
                    try:
                        fm_data = yaml.safe_load(frontmatter)
                        if fm_data:
                            skill_tags = fm_data.get('tags', [])
                            skill_triggers = fm_data.get('triggers', [])
                            skill_category = fm_data.get('category', 'other')
                    except:
                        pass
            
            # Case 3: Dictionary (from to_dict())
            elif isinstance(skill, dict):
                metadata = skill.get('metadata', {})
                skill_name = metadata.get('name', 'unknown')
                skill_description = metadata.get('description', '')
                skill_tags = metadata.get('tags', [])
                skill_triggers = metadata.get('triggers', [])
                skill_category = metadata.get('category', 'other')
            else:
                # Skip unknown skill types
                continue
            
            # Skip if no valid metadata
            if not skill_name or skill_name == 'unknown':
                continue
            
            # Calculate relevance score
            score = 0
            reasons = []
            
            # Check triggers (exact match or regex)
            if has_matches_trigger:
                score += 10
                reasons.append("matches trigger")
            else:
                for trigger in skill_triggers:
                    if trigger.lower() in context_lower:
                        score += 10
                        reasons.append(f"trigger: {trigger}")
                        break
                    # Check regex pattern
                    if trigger.startswith('/') and trigger.endswith('/'):
                        import re
                        try:
                            if re.search(trigger[1:-1], context, re.IGNORECASE):
                                score += 10
                                reasons.append(f"regex trigger: {trigger}")
                                break
                        except:
                            pass
            
            # Check tags
            for tag in skill_tags:
                if tag.lower() in context_lower:
                    score += 5
                    reasons.append(f"tag: {tag}")
            
            # Check description
            if skill_description and skill_description.lower() in context_lower:
                score += 3
                reasons.append("matches description")
            
            # Check category keywords
            if skill_category:
                category_keywords = {
                    'code': ['code', 'program', 'function', 'class', 'module', 'python', 'java'],
                    'testing': ['test', 'unit test', 'pytest', 'coverage', 'spec'],
                    'documentation': ['document', 'readme', 'doc', 'comment', 'write'],
                    'database': ['database', 'sql', 'migration', 'schema', 'db'],
                    'devops': ['docker', 'deploy', 'ci/cd', 'pipeline', 'git', 'container'],
                    'frontend': ['frontend', 'ui', 'react', 'css', 'html', 'javascript', 'web'],
                    'backend': ['backend', 'api', 'server', 'endpoint', 'rest', 'graphql'],
                    'security': ['security', 'vulnerability', 'authentication', 'auth', 'encrypt']
                }
                
                keywords = category_keywords.get(skill_category, [])
                for keyword in keywords:
                    if keyword in context_lower:
                        score += 2
                        reasons.append(f"category: {skill_category}")
                        break
            
            if score > 0:
                scored_skills.append({
                    'skill': {
                        'metadata': {
                            'name': skill_name,
                            'description': skill_description,
                            'tags': skill_tags,
                            'category': skill_category
                        }
                    },
                    'score': score,
                    'reason': "; ".join(reasons)
                })
        
        # Sort by score and return top_k
        scored_skills.sort(key=lambda x: x['score'], reverse=True)
        return scored_skills[:top_k]
    
    def get_system_prompt_injection(self) -> str:
        """Get reason why skill is relevant."""
        reasons = []
        context_lower = context.lower()
        
        if skill.matches_trigger(context):
            reasons.append("matches trigger")
        
        if skill.metadata.description.lower() in context_lower:
            reasons.append("matches description")
        
        if skill.metadata.tags:
            matching_tags = [t for t in skill.metadata.tags if t.lower() in context_lower]
            if matching_tags:
                reasons.append(f"tags: {', '.join(matching_tags[:3])}")
        
        return "; ".join(reasons) if reasons else "contextual match"
    
    def get_system_prompt_injection(self) -> str:
        """
        Generate system prompt injection with available skills.
        
        Returns:
            Formatted string for system prompt
        """
        skills = self.skill_manager.list_skills()
        
        if not skills:
            return ""
        
        prompt_parts = [
            "\n\n## Available Skills\n",
            "You have access to the following skills. Consider using them when relevant:\n"
        ]
        
        for skill in skills[:10]:  # Limit to 10 skills
            metadata = skill.get('metadata', {})
            name = metadata.get('name', 'unknown')
            desc = metadata.get('description', '')
            prompt_parts.append(f"- **{name}**: {desc}\n")
        
        if len(skills) > 10:
            prompt_parts.append(f"- ... and {len(skills) - 10} more skills\n")
        
        prompt_parts.append("\nUse the `load_skill` tool to load a skill when working on a relevant task.\n")
        
        return ''.join(prompt_parts)
