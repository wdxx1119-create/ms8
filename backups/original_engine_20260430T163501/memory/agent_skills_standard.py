"""
Agent Skills Standard Implementation
Compatible with https://agentskills.io/ standard

This module implements the open Agent Skills standard for cross-platform compatibility.
Skills are portable across Cursor, Claude Code, VS Code, Letta, and other compatible agents.
"""
import json
import yaml
import re
from pathlib import Path
from typing import Dict, Optional, List, Any, Callable
from datetime import datetime
from dataclasses import dataclass, asdict
from enum import Enum

class SkillScope(Enum):
    """Skill scope enumeration."""
    PROJECT = "project"
    AGENT = "agent"
    GLOBAL = "global"
    BUNDLED = "bundled"

class SkillCategory(Enum):
    """Skill category enumeration."""
    CODE = "code"
    DOCUMENTATION = "documentation"
    TESTING = "testing"
    DEPLOYMENT = "deployment"
    DATABASE = "database"
    FRONTEND = "frontend"
    BACKEND = "backend"
    DEVOPS = "devops"
    SECURITY = "security"
    DATA = "data"
    AI_ML = "ai_ml"
    OTHER = "other"

@dataclass
class SkillMetadata:
    """
    Agent Skills Standard Metadata.
    Compatible with agentskills.io specification.
    """
    name: str
    description: str
    version: str = "1.0.0"
    author: str = "Unknown"
    license: str = "MIT"
    homepage: str = ""
    repository: str = ""
    category: SkillCategory = SkillCategory.OTHER
    tags: List[str] = None
    tools: List[str] = None
    triggers: List[str] = None
    min_agent_version: str = "1.0.0"
    dependencies: List[str] = None
    
    def __post_init__(self):
        if self.tags is None:
            self.tags = []
        if self.tools is None:
            self.tools = []
        if self.triggers is None:
            self.triggers = []
        if self.dependencies is None:
            self.dependencies = []
    
    def to_dict(self) -> Dict:
        """Convert to dictionary."""
        return {
            'name': self.name,
            'description': self.description,
            'version': self.version,
            'author': self.author,
            'license': self.license,
            'homepage': self.homepage,
            'repository': self.repository,
            'category': self.category.value if isinstance(self.category, SkillCategory) else self.category,
            'tags': self.tags,
            'tools': self.tools,
            'triggers': self.triggers,
            'min_agent_version': self.min_agent_version,
            'dependencies': self.dependencies
        }
    
    @classmethod
    def from_dict(cls, data: Dict) -> 'SkillMetadata':
        """Create from dictionary."""
        return cls(
            name=data.get('name', 'unknown'),
            description=data.get('description', ''),
            version=data.get('version', '1.0.0'),
            author=data.get('author', 'Unknown'),
            license=data.get('license', 'MIT'),
            homepage=data.get('homepage', ''),
            repository=data.get('repository', ''),
            category=data.get('category', 'other'),
            tags=data.get('tags', []),
            tools=data.get('tools', []),
            triggers=data.get('triggers', []),
            min_agent_version=data.get('min_agent_version', '1.0.0'),
            dependencies=data.get('dependencies', [])
        )
    
    @classmethod
    def from_yaml(cls, yaml_str: str) -> 'SkillMetadata':
        """Create from YAML frontmatter."""
        data = yaml.safe_load(yaml_str)
        return cls.from_dict(data)

@dataclass
class SkillTest:
    """Skill test case."""
    name: str
    input: str
    expected_output_contains: List[str] = None
    expected_files: List[str] = None
    
    def to_dict(self) -> Dict:
        return {
            'name': self.name,
            'input': self.input,
            'expected_output_contains': self.expected_output_contains or [],
            'expected_files': self.expected_files or []
        }

@dataclass
class SkillResource:
    """Skill resource file."""
    name: str
    content: str
    type: str = "text"  # text, code, image, etc.
    
    def to_dict(self) -> Dict:
        return {
            'name': self.name,
            'content': self.content,
            'type': self.type
        }

class AgentSkill:
    """
    Agent Skill - Compatible with agentskills.io standard.
    
    Directory structure:
    skill-name/
    ├── SKILL.md              # Main skill file with YAML frontmatter
    ├── skill.json            # Optional: JSON metadata (alternative to frontmatter)
    ├── README.md             # Optional: Extended documentation
    ├── resources/            # Optional: Resource files
    │   ├── example.py
    │   └── template.md
    ├── tests/               # Optional: Test cases
    │   └── tests.json
    └── examples/            # Optional: Example usage
        └── example.md
    """
    
    def __init__(self, path: Path):
        self.path = path
        self.metadata: Optional[SkillMetadata] = None
        self.content: str = ""
        self.resources: List[SkillResource] = []
        self.tests: List[SkillTest] = []
        self.examples: List[Dict] = []
        self.loaded = False
    
    def load(self) -> bool:
        """
        Load skill from directory.
        
        Returns:
            bool: True if successfully loaded
        """
        if not self.path.exists():
            return False
        
        # Load SKILL.md
        skill_file = self.path / 'SKILL.md'
        if not skill_file.exists():
            # Try skill.json
            json_file = self.path / 'skill.json'
            if json_file.exists():
                return self._load_from_json(json_file)
            return False
        
        with open(skill_file, 'r', encoding='utf-8') as f:
            content = f.read()
        
        # Parse YAML frontmatter
        if content.startswith('---'):
            parts = content.split('---', 2)
            if len(parts) >= 3:
                frontmatter = parts[1].strip()
                body = parts[2].strip()
                
                try:
                    self.metadata = SkillMetadata.from_yaml(frontmatter)
                    self.content = body
                    self.loaded = True
                except Exception as e:
                    print(f"Error parsing skill metadata: {e}")
                    return False
        
        # Load resources
        self._load_resources()
        
        # Load tests
        self._load_tests()
        
        # Load examples
        self._load_examples()
        
        return True
    
    def _load_from_json(self, json_file: Path) -> bool:
        """Load skill from JSON file."""
        try:
            with open(json_file, 'r', encoding='utf-8') as f:
                data = json.load(f)
            
            self.metadata = SkillMetadata.from_dict(data.get('metadata', {}))
            self.content = data.get('content', '')
            self.loaded = True
            
            # Load resources
            self._load_resources()
            
            return True
        except Exception as e:
            print(f"Error loading skill from JSON: {e}")
            return False
    
    def _load_resources(self) -> None:
        """Load skill resources."""
        resources_dir = self.path / 'resources'
        if not resources_dir.exists():
            return
        
        for resource_file in resources_dir.glob('*'):
            if resource_file.is_file():
                try:
                    with open(resource_file, 'r', encoding='utf-8') as f:
                        content = f.read()
                    
                    # Determine type
                    suffix = resource_file.suffix.lower()
                    type_map = {
                        '.py': 'code',
                        '.js': 'code',
                        '.ts': 'code',
                        '.md': 'text',
                        '.txt': 'text',
                        '.json': 'json',
                        '.yaml': 'yaml',
                        '.yml': 'yaml',
                    }
                    resource_type = type_map.get(suffix, 'text')
                    
                    self.resources.append(SkillResource(
                        name=resource_file.name,
                        content=content,
                        type=resource_type
                    ))
                except Exception as e:
                    print(f"Error loading resource {resource_file}: {e}")
    
    def _load_tests(self) -> None:
        """Load skill tests."""
        tests_file = self.path / 'tests' / 'tests.json'
        if not tests_file.exists():
            return
        
        try:
            with open(tests_file, 'r', encoding='utf-8') as f:
                data = json.load(f)
            
            for test_data in data.get('tests', []):
                self.tests.append(SkillTest(
                    name=test_data.get('name', 'Unnamed'),
                    input=test_data.get('input', ''),
                    expected_output_contains=test_data.get('expected_output_contains', []),
                    expected_files=test_data.get('expected_files', [])
                ))
        except Exception as e:
            print(f"Error loading tests: {e}")
    
    def _load_examples(self) -> None:
        """Load skill examples."""
        examples_dir = self.path / 'examples'
        if not examples_dir.exists():
            return
        
        for example_file in examples_dir.glob('*.md'):
            try:
                with open(example_file, 'r', encoding='utf-8') as f:
                    content = f.read()
                
                self.examples.append({
                    'name': example_file.stem,
                    'content': content,
                    'file': str(example_file)
                })
            except Exception as e:
                print(f"Error loading example {example_file}: {e}")
    
    def save(self) -> bool:
        """Save skill to directory."""
        if not self.metadata:
            return False
        
        # Create directory
        self.path.mkdir(parents=True, exist_ok=True)
        
        # Create SKILL.md
        skill_file = self.path / 'SKILL.md'
        
        # Build frontmatter
        frontmatter = yaml.dump(self.metadata.to_dict(), allow_unicode=True, default_flow_style=False)
        
        content = f"---\n{frontmatter}---\n\n{self.content}"
        
        with open(skill_file, 'w', encoding='utf-8') as f:
            f.write(content)
        
        # Save resources
        if self.resources:
            resources_dir = self.path / 'resources'
            resources_dir.mkdir(parents=True, exist_ok=True)
            
            for resource in self.resources:
                resource_file = resources_dir / resource.name
                with open(resource_file, 'w', encoding='utf-8') as f:
                    f.write(resource.content)
        
        # Save tests
        if self.tests:
            tests_dir = self.path / 'tests'
            tests_dir.mkdir(parents=True, exist_ok=True)
            
            tests_file = tests_dir / 'tests.json'
            with open(tests_file, 'w', encoding='utf-8') as f:
                json.dump({
                    'tests': [test.to_dict() for test in self.tests]
                }, f, indent=2)
        
        return True
    
    def matches_trigger(self, text: str) -> bool:
        """
        Check if text matches any skill trigger.
        
        Args:
            text: User input text
        
        Returns:
            bool: True if trigger matches
        """
        if not self.metadata or not self.metadata.triggers:
            return False
        
        text_lower = text.lower()
        
        for trigger in self.metadata.triggers:
            # Check exact match
            if trigger.lower() in text_lower:
                return True
            
            # Check regex pattern
            if trigger.startswith('/') and trigger.endswith('/'):
                try:
                    pattern = re.compile(trigger[1:-1], re.IGNORECASE)
                    if pattern.search(text):
                        return True
                except:
                    pass
        
        return False
    
    def to_dict(self) -> Dict:
        """Convert skill to dictionary."""
        return {
            'metadata': self.metadata.to_dict() if self.metadata else {},
            'content': self.content,
            'resources': [r.to_dict() for r in self.resources],
            'tests': [t.to_dict() for t in self.tests],
            'examples': self.examples,
            'path': str(self.path),
            'loaded': self.loaded
        }
    
    def get_full_content(self) -> str:
        """Get full skill content with resources."""
        result = [self.content]
        
        if self.resources:
            result.append("\n\n## Resources\n")
            for resource in self.resources:
                result.append(f"\n### {resource.name}\n```{resource.type}\n{resource.content}\n```")
        
        if self.examples:
            result.append("\n\n## Examples\n")
            for example in self.examples:
                result.append(f"\n### {example['name']}\n{example['content']}")
        
        return '\n'.join(result)
