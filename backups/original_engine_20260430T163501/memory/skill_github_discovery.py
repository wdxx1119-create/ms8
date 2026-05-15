"""
GitHub-based Skill Discovery System
Automatically discovers and indexes skills from GitHub repositories.
"""
import os
import requests
import json
import base64
import time
import yaml
from typing import List, Dict, Optional
from datetime import datetime, timedelta
from pathlib import Path

from .config import get_config
from .file_write_guard import atomic_write_json

class GitHubSkillDiscovery:
    """Discover skills from GitHub repositories."""
    
    def __init__(self, github_token: str = None):
        """
        Initialize GitHub skill discovery.
        
        Args:
            github_token: Optional GitHub personal access token for higher rate limits
        """
        runtime_token = github_token or self._load_runtime_token()
        self.session = requests.Session()
        self.session.headers.update({
            'Accept': 'application/vnd.github.v3+json',
            'User-Agent': 'OpenClaw-Skill-Discovery/1.0'
        })
        
        if runtime_token:
            self.session.headers['Authorization'] = f'token {runtime_token}'
        
        self.base_url = "https://api.github.com"
        cfg = get_config()
        self.workspace_dir = Path(cfg["workspace_dir"])
        skills_cfg = cfg["settings"]["memory"].get("skills_system", {})
        self.enabled = os.environ.get("OPENCLAW_MEMORY_DISABLE_GITHUB_SYNC", "").lower() not in {
            "1",
            "true",
            "yes",
        } and bool(skills_cfg.get("github_enabled", True))
        self.timeout = float(os.environ.get("OPENCLAW_MEMORY_GITHUB_TIMEOUT", "3"))
        self.cache_ttl = float(skills_cfg.get("cache_ttl_hours", 6)) * 3600
        self.cache_file = cfg["memory_dir"] / "skill_cache.json"
        self.cache_file.parent.mkdir(parents=True, exist_ok=True)
        self.rate_limited_until = None
        
        # Default skill repositories to search
        self.skill_repos = [
            'letta-ai/skills',
            'microsoft/promptbase',
            'openai/openai-cookbook',
            'anthropics/claude-code',
        ]

    def _load_runtime_token(self) -> Optional[str]:
        """Load GitHub token from env or runtime.env."""
        token = os.environ.get("GITHUB_TOKEN")
        if token:
            return token

        runtime_env = Path(__file__).resolve().parents[2] / "runtime.env"
        if runtime_env.exists():
            try:
                for line in runtime_env.read_text(encoding="utf-8").splitlines():
                    if line.startswith("GITHUB_TOKEN="):
                        return line.split("=", 1)[1].strip()
            except Exception:
                return None
        return None

    def _load_cache(self) -> Dict:
        if not self.cache_file.exists():
            return {"timestamp": None, "skills": []}
        try:
            return json.loads(self.cache_file.read_text(encoding="utf-8"))
        except Exception:
            return {"timestamp": None, "skills": []}

    def _save_cache(self, skills: List[Dict]) -> None:
        payload = {"timestamp": datetime.now().isoformat(), "skills": skills}
        atomic_write_json(self.cache_file, payload, ensure_ascii=False, indent=2)

    def _cache_valid(self, payload: Dict) -> bool:
        ts = payload.get("timestamp")
        if not ts:
            return False
        try:
            stamp = datetime.fromisoformat(ts)
            return (datetime.now() - stamp).total_seconds() < self.cache_ttl
        except Exception:
            return False

    def _local_skill_fallback(self, query: str = None, limit: int = 20) -> List[Dict]:
        skills_dir = self.workspace_dir / ".skills"
        if not skills_dir.exists():
            return []
        out: List[Dict] = []
        q = str(query or "").strip().lower()
        for d in sorted(skills_dir.iterdir()):
            if not d.is_dir():
                continue
            name = d.name
            if q and q not in name.lower():
                continue
            out.append(
                {
                    "name": name,
                    "description": "Local installed skill",
                    "source": "local_workspace",
                    "owner": "local",
                    "repo": "workspace/.skills",
                    "stars": 0,
                    "updated_at": datetime.now().isoformat(),
                    "category": "local",
                    "tags": ["local"],
                }
            )
            if len(out) >= max(1, int(limit)):
                break
        return out

    def _request(self, url: str, params: Optional[Dict] = None) -> Optional[requests.Response]:
        if self.rate_limited_until and time.time() < float(self.rate_limited_until or 0):
            return None
        retries = 2
        for attempt in range(retries + 1):
            try:
                response = self.session.get(url, params=params, timeout=self.timeout)
            except Exception:
                response = None
            if response is None:
                continue
            if response.status_code in (403, 429):
                reset = response.headers.get("X-RateLimit-Reset")
                if reset and reset.isdigit():
                    self.rate_limited_until = float(reset)
                else:
                    self.rate_limited_until = time.time() + 300
                return response
            if response.status_code >= 500 and attempt < retries:
                time.sleep(0.5 * (attempt + 1))
                continue
            return response
        return None
    
    def search_skills(self, 
                      query: str = None,
                      category: str = None,
                      tags: List[str] = None,
                      min_stars: int = 0,
                      sort_by: str = 'stars',
                      limit: int = 50) -> List[Dict]:
        """
        Search skills from GitHub repositories.
        
        Args:
            query: Search query string
            category: Filter by category
            tags: Filter by tags
            min_stars: Minimum star count
            sort_by: Sort by (stars/updated/name)
            limit: Maximum results
        
        Returns:
            List of skill metadata dictionaries
        """
        all_skills = []
        cached = self._load_cache()
        if not self.enabled:
            return cached.get("skills", [])
        if self.rate_limited_until and self._cache_valid(cached):
            return cached.get("skills", [])
        
        # Search each configured repository
        for repo in self.skill_repos:
            try:
                repo_skills = self._search_repo_skills(
                    repo, query, category, tags, min_stars
                )
                all_skills.extend(repo_skills)
            except Exception as e:
                print(f'Error searching {repo}: {e}')

        if len(all_skills) < max(3, min(limit, 5)):
            try:
                all_skills.extend(self._search_github_code(query=query, limit=limit))
            except Exception as e:
                print(f'Error searching GitHub code: {e}')
        
        # Filter and sort
        if query:
            all_skills = self._filter_by_query(all_skills, query)
        
        if tags:
            all_skills = self._filter_by_tags(all_skills, tags)
        
        all_skills = self._sort_skills(all_skills, sort_by)
        
        results = all_skills[:limit]
        if results:
            self._save_cache(results)
            return results
        if self._cache_valid(cached):
            return cached.get("skills", [])
        local_fallback = self._local_skill_fallback(query=query, limit=limit)
        if local_fallback:
            self._save_cache(local_fallback)
            return local_fallback
        return results

    def _search_github_code(self, query: str = None, limit: int = 20) -> List[Dict]:
        """Fallback search using GitHub code search for SKILL.md files."""
        search_terms = query or "agent skill"
        api_query = f"{search_terms} filename:SKILL.md"
        response = self._request(
            f"{self.base_url}/search/code",
            params={"q": api_query, "per_page": min(limit, 20)},
        )
        if response is None or response.status_code != 200:
            if response is not None:
                print(f"GitHub code search failed: {response.status_code}")
            return []

        items = response.json().get("items", [])
        results: List[Dict] = []
        for item in items:
            repo = item.get("repository", {})
            owner = repo.get("owner", {}).get("login")
            repo_name = repo.get("name")
            path = item.get("path")
            if not owner or not repo_name or not path:
                continue
            meta = self._get_skill_metadata(owner, repo_name, path.rsplit("/", 1)[0], repo)
            if meta:
                results.append(meta)
        return results
    
    def _search_repo_skills(self, 
                            repo: str,
                            query: str = None,
                            category: str = None,
                            tags: List[str] = None,
                            min_stars: int = 0) -> List[Dict]:
        """Search skills in a specific repository."""
        owner, repo_name = repo.split('/')
        
        # Get repository info
        repo_response = self.session.get(
            f'{self.base_url}/repos/{owner}/{repo_name}',
            timeout=self.timeout,
        )
        
        if repo_response.status_code != 200:
            print(f'Failed to get repo {repo}: {repo_response.status_code}')
            return []
        
        repo_info = repo_response.json()
        
        # Check minimum stars
        if repo_info.get('stargazers_count', 0) < min_stars:
            return []
        
        # Get skills directory
        skills = []
        
        try:
            # Try to get skills/ directory
            contents_response = self.session.get(
                f'{self.base_url}/repos/{owner}/{repo_name}/contents/skills',
                timeout=self.timeout,
            )
            
            if contents_response.status_code == 200:
                contents = contents_response.json()
                
                # Process each skill directory
                for item in contents:
                    if item['type'] == 'dir':
                        skill_meta = self._get_skill_metadata(
                            owner, repo_name, item['path'], repo_info
                        )
                        if skill_meta:
                            skills.append(skill_meta)
            
            # Also check root level for SKILL.md files
            if not skills:
                contents_response = self._request(
                    f'{self.base_url}/repos/{owner}/{repo_name}/contents',
                )
                
                if contents_response is not None and contents_response.status_code == 200:
                    contents = contents_response.json()
                    
                    for item in contents:
                        if item['name'].endswith('.md') and 'SKILL' in item['name'].upper():
                            skill_meta = self._get_skill_metadata(
                                owner, repo_name, item['path'], repo_info
                            )
                            if skill_meta:
                                skills.append(skill_meta)
        
        except Exception as e:
            print(f'Error getting contents from {repo}: {e}')
        
        return skills
    
    def _get_skill_metadata(self, 
                            owner: str, 
                            repo_name: str, 
                            path: str,
                            repo_info: Dict) -> Optional[Dict]:
        """Get metadata for a single skill."""
        try:
            # Get SKILL.md content
            skill_path = f'{path}/SKILL.md' if '/' in path else path
            
            content_response = self._request(
                f'{self.base_url}/repos/{owner}/{repo_name}/contents/{skill_path}',
            )
            
            if content_response is None or content_response.status_code != 200:
                return None
            
            content_data = content_response.json()
            content = base64.b64decode(content_data['content']).decode('utf-8')
            
            # Parse YAML frontmatter
            skill_meta = self._parse_skill_frontmatter(content)
            
            if skill_meta:
                # Add repository info
                skill_meta['repository'] = f'{owner}/{repo_name}'
                skill_meta['path'] = path
                skill_meta['url'] = content_data['html_url']
                skill_meta['stars'] = repo_info.get('stargazers_count', 0)
                skill_meta['repo_url'] = repo_info.get('html_url', '')
                
                return skill_meta
        
        except Exception as e:
            print(f'Error getting skill metadata: {e}')
        
        return None
    
    def _parse_skill_frontmatter(self, content: str) -> Optional[Dict]:
        """Parse YAML frontmatter from SKILL.md content."""
        try:
            if not content.startswith('---'):
                return None
            
            parts = content.split('---', 2)
            if len(parts) < 2:
                return None
            
            frontmatter = parts[1].strip()
            data = yaml.safe_load(frontmatter)
            
            if not data or not isinstance(data, dict):
                return None
            
            return {
                'name': data.get('name', 'unknown'),
                'description': data.get('description', ''),
                'version': data.get('version', '1.0.0'),
                'author': data.get('author', 'unknown'),
                'license': data.get('license', 'MIT'),
                'category': data.get('category', 'other'),
                'tags': data.get('tags', []),
                'triggers': data.get('triggers', []),
                'tools': data.get('tools', []),
            }
        
        except Exception as e:
            print(f'Error parsing frontmatter: {e}')
            return None
    
    def _filter_by_query(self, skills: List[Dict], query: str) -> List[Dict]:
        """Filter skills by search query."""
        query_lower = query.lower()
        
        filtered = []
        for skill in skills:
            # Search in name, description, tags
            searchable = ' '.join([
                skill.get('name', ''),
                skill.get('description', ''),
                ' '.join(skill.get('tags', []))
            ]).lower()
            
            if query_lower in searchable:
                filtered.append(skill)
        
        return filtered
    
    def _filter_by_tags(self, skills: List[Dict], tags: List[str]) -> List[Dict]:
        """Filter skills by tags."""
        filtered = []
        
        for skill in skills:
            skill_tags = [t.lower() for t in skill.get('tags', [])]
            
            if any(tag.lower() in skill_tags for tag in tags):
                filtered.append(skill)
        
        return filtered
    
    def _sort_skills(self, skills: List[Dict], sort_by: str) -> List[Dict]:
        """Sort skills by specified field."""
        reverse = True
        
        if sort_by == 'name':
            reverse = False
            key_func = lambda x: x.get('name', '').lower()
        elif sort_by == 'updated':
            key_func = lambda x: x.get('updated', '')
        elif sort_by == 'rating':
            key_func = lambda x: x.get('rating', 0)
        else:  # Default: stars
            key_func = lambda x: x.get('stars', 0)
        
        return sorted(skills, key=key_func, reverse=reverse)
    
    def get_skill_catalog(self, org: str = 'openclaw') -> Dict:
        """
        Get complete skill catalog from a GitHub organization.
        
        Args:
            org: GitHub organization name
        
        Returns:
            Complete catalog dictionary
        """
        skills = self.search_skills(limit=1000)
        
        # Group by category
        categories = {}
        for skill in skills:
            cat = skill.get('category', 'other')
            if cat not in categories:
                categories[cat] = []
            categories[cat].append(skill)
        
        return {
            'version': '1.0',
            'updated': datetime.now().isoformat(),
            'total_skills': len(skills),
            'categories': categories,
            'skills': skills
        }
    
    def get_trending_skills(self, days: int = 7, limit: int = 10) -> List[Dict]:
        """
        Get trending skills (recently updated with high stars).
        
        Args:
            days: Number of days to consider
            limit: Maximum results
        
        Returns:
            List of trending skills
        """
        # Get all skills
        all_skills = self.search_skills(limit=200)
        
        # Filter recently updated
        from datetime import timedelta
        cutoff = datetime.now() - timedelta(days=days)
        
        trending = []
        for skill in all_skills:
            # Simple heuristic: high stars + recent activity
            stars = skill.get('stars', 0)
            if stars >= 10:  # At least 10 stars
                trending.append(skill)
        
        # Sort by stars
        trending.sort(key=lambda x: x.get('stars', 0), reverse=True)
        
        return trending[:limit]
    
    def get_skill_recommendations(self, 
                                   context: str,
                                   limit: int = 5) -> List[Dict]:
        """
        Get skill recommendations based on context.
        
        Args:
            context: Current context or task description
            limit: Maximum recommendations
        
        Returns:
            List of recommended skills
        """
        # Extract keywords from context
        keywords = context.lower().split()
        
        # Search for each keyword
        all_results = []
        for keyword in keywords:
            if len(keyword) > 3:  # Skip short words
                results = self.search_skills(query=keyword, limit=10)
                all_results.extend(results)
        
        # Remove duplicates
        seen = set()
        unique = []
        for skill in all_results:
            skill_id = f"{skill['repository']}/{skill['name']}"
            if skill_id not in seen:
                seen.add(skill_id)
                unique.append(skill)
        
        # Score and sort
        for skill in unique:
            score = 0
            searchable = ' '.join([
                skill.get('name', ''),
                skill.get('description', ''),
                ' '.join(skill.get('tags', []))
            ]).lower()
            
            for keyword in keywords:
                if keyword in searchable:
                    score += 1
            
            skill['relevance_score'] = score
        
        unique.sort(key=lambda x: x.get('relevance_score', 0), reverse=True)
        
        return unique[:limit]
