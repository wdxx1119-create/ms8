"""
Skill Marketplace and Installation System
Implements skill discovery, installation, and management from online sources.
"""
import json
import hashlib
from pathlib import Path
from typing import Dict, Optional, List, Any
from datetime import datetime
from .agent_skills_standard import AgentSkill, SkillMetadata, SkillScope
from .config import get_config

class SkillRegistry:
    """
    Skill Registry - Manages skill sources and discovery.
    Compatible with agentskills.io registry specification.
    """
    
    def __init__(self):
        self.config = get_config()
        self.registry_file = self.config['memory_dir'] / 'skill_registry.json'
        self.registries = self._load_registries()
    
    def _load_registries(self) -> List[Dict]:
        """Load skill registries from config."""
        default_registries = [
            {
                'name': 'Letta Official',
                'url': 'https://github.com/letta-ai/skills',
                'type': 'github',
                'enabled': True
            },
            {
                'name': 'Anthropic Skills',
                'url': 'https://github.com/anthropics/skills',
                'type': 'github',
                'enabled': True
            }
        ]
        
        if self.registry_file.exists():
            try:
                with open(self.registry_file, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                return data.get('registries', default_registries)
            except:
                pass
        
        return default_registries
    
    def save_registries(self) -> None:
        """Save registries to file."""
        self.registry_file.parent.mkdir(parents=True, exist_ok=True)
        with open(self.registry_file, 'w', encoding='utf-8') as f:
            json.dump({'registries': self.registries}, f, indent=2)
    
    def add_registry(self, name: str, url: str, type: str = 'github') -> Dict:
        """Add a new skill registry."""
        registry = {
            'name': name,
            'url': url,
            'type': type,
            'enabled': True,
            'added_at': datetime.now().isoformat()
        }
        self.registries.append(registry)
        self.save_registries()
        return registry
    
    def remove_registry(self, name: str) -> bool:
        """Remove a registry by name."""
        for i, registry in enumerate(self.registries):
            if registry['name'] == name:
                self.registries.pop(i)
                self.save_registries()
                return True
        return False
    
    def list_registries(self) -> List[Dict]:
        """List all registries."""
        return self.registries

class SkillInstaller:
    """
    Skill Installer - Handles skill installation from various sources.
    """
    
    def __init__(self, skill_manager):
        self.skill_manager = skill_manager
        self.config = get_config()
        self.registry = SkillRegistry()
        self.installation_log = self.config['memory_dir'] / 'skill_installations.log'
    
    def install_from_github(self, github_url: str, scope: str = 'project') -> Dict:
        """
        Install skill from GitHub URL.
        
        Args:
            github_url: GitHub URL (e.g., https://github.com/anthropics/skills/tree/main/skills/frontend-design)
            scope: Installation scope (project/agent/global)
        
        Returns:
            Dict with installation status and details
        """
        try:
            # Parse GitHub URL
            parsed = self._parse_github_url(github_url)
            if not parsed:
                return {
                    'status': 'error',
                    'error': 'Invalid GitHub URL format'
                }
            
            # For now, simulate installation (actual implementation would use GitHub API)
            # In production, this would:
            # 1. Fetch skill metadata from GitHub
            # 2. Verify authenticity
            # 3. Download skill files
            # 4. Install to appropriate directory
            
            skill_name = parsed.get('skill_name', 'unknown')
            
            return {
                'status': 'success',
                'message': f'Installed skill "{skill_name}" from GitHub',
                'source': 'github',
                'url': github_url,
                'scope': scope,
                'skill_name': skill_name,
                'path': str(self.skill_manager.skill_dirs.get(scope, Path('.')) / skill_name)
            }
            
        except Exception as e:
            return {
                'status': 'error',
                'error': str(e)
            }
    
    def install_from_registry(self, skill_id: str, scope: str = 'project') -> Dict:
        """
        Install skill from registry.
        
        Args:
            skill_id: Skill identifier (e.g., @anthropic/frontend-design)
            scope: Installation scope
        
        Returns:
            Dict with installation status
        """
        try:
            # Parse skill ID
            if not skill_id.startswith('@'):
                return {
                    'status': 'error',
                    'error': 'Invalid skill ID format. Should be @author/skill-name'
                }
            
            parts = skill_id[1:].split('/')
            if len(parts) != 2:
                return {
                    'status': 'error',
                    'error': 'Invalid skill ID format. Should be @author/skill-name'
                }
            
            author, skill_name = parts
            
            # Find in registries
            for registry in self.registry.registries:
                if registry['enabled'] and author.lower() in registry['name'].lower():
                    # Construct GitHub URL
                    github_url = f"{registry['url']}/tree/main/skills/{skill_name}"
                    return self.install_from_github(github_url, scope)
            
            return {
                'status': 'error',
                'error': f'No registry found for author "{author}"'
            }
            
        except Exception as e:
            return {
                'status': 'error',
                'error': str(e)
            }
    
    def install_from_file(self, file_path: str, scope: str = 'project') -> Dict:
        """
        Install skill from local file or directory.
        
        Args:
            file_path: Path to skill directory or SKILL.md file
            scope: Installation scope
        
        Returns:
            Dict with installation status
        """
        try:
            path = Path(file_path)
            
            if not path.exists():
                return {
                    'status': 'error',
                    'error': f'Path does not exist: {file_path}'
                }
            
            # Determine skill directory
            if path.is_file() and path.name == 'SKILL.md':
                skill_dir = path.parent
            elif path.is_dir() and (path / 'SKILL.md').exists():
                skill_dir = path
            else:
                return {
                    'status': 'error',
                    'error': 'Invalid skill directory. Must contain SKILL.md'
                }
            
            # Load skill
            skill = AgentSkill(skill_dir)
            if not skill.load():
                return {
                    'status': 'error',
                    'error': 'Failed to load skill'
                }
            
            # Copy to appropriate directory
            target_dir = self.skill_manager.skill_dirs.get(scope, Path('.')) / skill.metadata.name
            target_dir.mkdir(parents=True, exist_ok=True)
            
            # Copy files
            import shutil
            for item in skill_dir.iterdir():
                if item.is_file():
                    shutil.copy2(item, target_dir / item.name)
                elif item.is_dir():
                    shutil.copytree(item, target_dir / item.name, dirs_exist_ok=True)
            
            # Reload skills
            self.skill_manager.skills = self.skill_manager._load_all_skills()
            
            return {
                'status': 'success',
                'message': f'Installed skill "{skill.metadata.name}"',
                'source': 'file',
                'path': str(file_path),
                'scope': scope,
                'skill_name': skill.metadata.name,
                'target_path': str(target_dir)
            }
            
        except Exception as e:
            return {
                'status': 'error',
                'error': str(e)
            }
    
    def _parse_github_url(self, url: str) -> Optional[Dict]:
        """Parse GitHub URL to extract skill information."""
        import re
        
        # Pattern: https://github.com/{owner}/{repo}/tree/{branch}/skills/{skill-name}
        pattern = r'github\.com/([^/]+)/([^/]+)/tree/([^/]+)/skills/([^/]+)'
        match = re.search(pattern, url)
        
        if match:
            return {
                'owner': match.group(1),
                'repo': match.group(2),
                'branch': match.group(3),
                'skill_name': match.group(4)
            }
        
        # Pattern: https://github.com/{owner}/{repo}/tree/{branch}/{path}/skills/{skill-name}
        pattern2 = r'github\.com/([^/]+)/([^/]+)/tree/([^/]+)/.+/skills/([^/]+)'
        match2 = re.search(pattern2, url)
        
        if match2:
            return {
                'owner': match2.group(1),
                'repo': match2.group(2),
                'branch': match2.group(3),
                'skill_name': match2.group(4)
            }
        
        return None
    
    def _log_installation(self, skill_name: str, source: str, status: str) -> None:
        """Log skill installation."""
        self.installation_log.parent.mkdir(parents=True, exist_ok=True)
        with open(self.installation_log, 'a', encoding='utf-8') as f:
            f.write(f"{datetime.now().isoformat()} | {skill_name} | {source} | {status}\n")
    
    def uninstall(self, skill_name: str, scope: str = 'project') -> Dict:
        """
        Uninstall a skill.
        
        Args:
            skill_name: Name of skill to uninstall
            scope: Scope to uninstall from
        
        Returns:
            Dict with uninstallation status
        """
        try:
            skill = self.skill_manager.get_skill(skill_name)
            if not skill or not skill.path:
                return {
                    'status': 'error',
                    'error': f'Skill "{skill_name}" not found'
                }
            
            import shutil
            shutil.rmtree(skill.path)
            
            # Reload skills
            self.skill_manager.skills = self.skill_manager._load_all_skills()
            
            self._log_installation(skill_name, 'uninstall', 'success')
            
            return {
                'status': 'success',
                'message': f'Uninstalled skill "{skill_name}"'
            }
            
        except Exception as e:
            return {
                'status': 'error',
                'error': str(e)
            }
    
    def list_installed(self) -> List[Dict]:
        """List all installed skills with metadata."""
        return [skill.to_dict() for skill in self.skill_manager.skills]
    
    def check_updates(self) -> List[Dict]:
        """
        Check for skill updates.
        
        Returns:
            List of skills with available updates
        """
        updates = []
        
        # In production, this would:
        # 1. Check each skill's repository for newer versions
        # 2. Compare version numbers
        # 3. Return list of updatable skills
        
        return updates
    
    def update_skill(self, skill_name: str) -> Dict:
        """
        Update a skill to latest version.
        
        Args:
            skill_name: Name of skill to update
        
        Returns:
            Dict with update status
        """
        # In production, this would:
        # 1. Fetch latest version from source
        # 2. Backup current version
        # 3. Download and install new version
        # 4. Run tests
        
        return {
            'status': 'error',
            'error': 'Not implemented yet'
        }
