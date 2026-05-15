"""
Local Search Index for Skills
Uses Whoosh for full-text search capabilities.
"""

from datetime import datetime
from pathlib import Path

from .config import get_config

# Check if whoosh is available
try:
    from whoosh.analysis import StemmingAnalyzer
    from whoosh.fields import DATETIME, ID, KEYWORD, NUMERIC, TEXT, Schema
    from whoosh.index import create_in, exists_in, open_dir
    from whoosh.qparser import MultifieldParser

    WHOOSH_AVAILABLE = True
except ImportError:
    WHOOSH_AVAILABLE = False
    print("Warning: Whoosh not available. Install with: pip install whoosh")


class SkillSearchIndex:
    """Local search index for skills using Whoosh."""

    def __init__(self, index_dir: str | None = None):
        """
        Initialize search index.

        Args:
            index_dir: Directory to store index (default: workspace memory/skill_index)
        """
        if index_dir is None:
            index_dir = get_config()["memory_dir"] / "skill_index"

        self.index_dir = Path(index_dir)
        self.index_dir.mkdir(parents=True, exist_ok=True)

        if WHOOSH_AVAILABLE:
            self.schema = self._create_schema()
            self.ix = self._open_or_create_index()
        else:
            self.schema = None
            self.ix = None
            # Fallback to simple JSON-based search
            self.skills_data: list[dict] = []

    def _create_schema(self):
        """Create search schema."""
        return Schema(
            # Core fields
            name=ID(stored=True, unique=True),
            title=TEXT(stored=True, analyzer=StemmingAnalyzer()),
            description=TEXT(stored=True, analyzer=StemmingAnalyzer()),
            content=TEXT(stored=True, analyzer=StemmingAnalyzer()),
            # Metadata fields
            category=ID(stored=True),
            tags=KEYWORD(stored=True, scorable=True),
            author=ID(stored=True),
            version=TEXT(stored=True),
            license=ID(stored=True),
            # Metrics
            stars=NUMERIC(stored=True),
            downloads=NUMERIC(stored=True),
            rating=NUMERIC(stored=True),
            relevance_score=NUMERIC(stored=True),
            # Timestamps
            created=DATETIME(stored=True),
            updated=DATETIME(stored=True),
            # Location
            repository=ID(stored=True),
            path=ID(stored=True),
            url=TEXT(stored=True),
            repo_url=TEXT(stored=True),
        )

    def _open_or_create_index(self):
        """Open existing index or create new one."""
        if exists_in(self.index_dir):
            return open_dir(self.index_dir)
        else:
            return create_in(self.index_dir, self.schema)

    def index_skill(self, skill: dict) -> bool:
        """
        Index a single skill.

        Args:
            skill: Skill metadata dictionary

        Returns:
            True if successful
        """
        if not WHOOSH_AVAILABLE:
            # Fallback: store in memory
            self.skills_data.append(skill)
            return True

        try:
            writer = self.ix.writer()

            # Prepare document
            doc = {
                "name": skill.get("name", ""),
                "title": skill.get("name", ""),
                "description": skill.get("description", ""),
                "content": self._get_full_content(skill),
                "category": skill.get("category", "other"),
                "tags": " ".join(skill.get("tags", [])),
                "author": skill.get("author", "unknown"),
                "version": skill.get("version", "1.0.0"),
                "license": skill.get("license", "MIT"),
                "stars": skill.get("stars", 0),
                "downloads": skill.get("downloads", 0),
                "rating": skill.get("rating", 0),
                "relevance_score": skill.get("relevance_score", 0),
                "repository": skill.get("repository", ""),
                "path": skill.get("path", ""),
                "url": skill.get("url", ""),
                "repo_url": skill.get("repo_url", ""),
            }

            # Handle dates
            for date_field in ["created", "updated"]:
                date_val = skill.get(date_field)
                if date_val:
                    try:
                        if isinstance(date_val, str):
                            doc[date_field] = datetime.fromisoformat(date_val.replace("Z", "+00:00"))
                        else:
                            doc[date_field] = date_val
                    except (TypeError, ValueError) as exc:
                        print(f"[SkillSearchIndex] Invalid skill date field '{date_field}': {exc}")

            writer.add_document(**doc)
            writer.commit()

            return True

        except (RuntimeError, TypeError, ValueError, OSError) as e:
            print(f"Error indexing skill: {e}")
            return False

    def _get_full_content(self, skill: dict) -> str:
        """Get full skill content for indexing."""
        parts = [
            skill.get("name", ""),
            skill.get("description", ""),
            " ".join(skill.get("tags", [])),
            skill.get("category", ""),
        ]
        return " ".join(parts)

    def search(
        self,
        query: str,
        category: str | None = None,
        tags: list[str] | None = None,
        min_stars: int = 0,
        min_rating: float = 0,
        limit: int = 10,
    ) -> list[dict]:
        """
        Search skills.

        Args:
            query: Search query string
            category: Filter by category
            tags: Filter by tags
            min_stars: Minimum star count
            min_rating: Minimum rating
            limit: Maximum results

        Returns:
            List of matching skills with scores
        """
        if not WHOOSH_AVAILABLE:
            return self._fallback_search(query, category, tags, min_stars, limit)

        try:
            # Build query
            parser = MultifieldParser(["title", "description", "content", "tags"], schema=self.schema)
            query_obj = parser.parse(query)

            # Execute search
            with self.ix.searcher() as searcher:
                results = searcher.search(query_obj, limit=limit * 2)

                # Filter and format results
                matching_skills = []
                for hit in results:
                    skill = dict(hit)

                    # Apply filters
                    if category and skill.get("category") != category:
                        continue
                    if min_stars and skill.get("stars", 0) < min_stars:
                        continue
                    if min_rating and skill.get("rating", 0) < min_rating:
                        continue
                    if tags:
                        skill_tags = skill.get("tags", "").split()
                        if not any(t in skill_tags for t in tags):
                            continue

                    # Add search score
                    skill["score"] = hit.score
                    matching_skills.append(skill)

                    if len(matching_skills) >= limit:
                        break

                return matching_skills

        except (RuntimeError, TypeError, ValueError) as e:
            print(f"Search error: {e}")
            return []

    def _fallback_search(
        self,
        query: str,
        category: str | None = None,
        tags: list[str] | None = None,
        min_stars: int = 0,
        limit: int = 10,
    ) -> list[dict]:
        """Fallback search when Whoosh is not available."""
        query_lower = query.lower()
        results = []

        for skill in self.skills_data:
            # Check query match
            searchable = " ".join(
                [
                    skill.get("name", ""),
                    skill.get("description", ""),
                    " ".join(skill.get("tags", [])),
                ]
            ).lower()

            if query_lower not in searchable:
                continue

            # Apply filters
            if category and skill.get("category") != category:
                continue
            if min_stars and skill.get("stars", 0) < min_stars:
                continue
            if tags:
                skill_tags = skill.get("tags", [])
                if not any(t in skill_tags for t in tags):
                    continue

            results.append(skill)

        # Sort by stars
        results.sort(key=lambda x: x.get("stars", 0), reverse=True)

        return results[:limit]

    def suggest(self, prefix: str, field: str = "name", limit: int = 5) -> list[str]:
        """
        Get search suggestions based on prefix.

        Args:
            prefix: Prefix to match
            field: Field to search in
            limit: Maximum suggestions

        Returns:
            List of suggestions
        """
        if not WHOOSH_AVAILABLE:
            return self._fallback_suggest(prefix, field, limit)

        suggestions = []

        try:
            with self.ix.searcher() as searcher:
                reader = searcher.reader()

                # Get all values from field
                if field in reader.fieldnames():
                    for text in reader.field_texts(field):
                        if text.lower().startswith(prefix.lower()):
                            suggestions.append(text)
                            if len(suggestions) >= limit:
                                break
        except (RuntimeError, OSError) as e:
            print(f"Suggestion error: {e}")

        return suggestions

    def _fallback_suggest(self, prefix: str, field: str, limit: int) -> list[str]:
        """Fallback suggestion when Whoosh is not available."""
        suggestions = []
        prefix_lower = prefix.lower()

        for skill in self.skills_data:
            value = skill.get(field, "")
            if value.lower().startswith(prefix_lower):
                suggestions.append(value)
                if len(suggestions) >= limit:
                    break

        return suggestions

    def update_index(self, skills: list[dict]) -> int:
        """
        Update index with new skills.

        Args:
            skills: List of skill dictionaries

        Returns:
            Number of skills indexed
        """
        count = 0

        for skill in skills:
            if self.index_skill(skill):
                count += 1

        return count

    def get_categories(self) -> list[str]:
        """Get all unique categories."""
        if not WHOOSH_AVAILABLE:
            return self._fallback_get_categories()

        try:
            with self.ix.searcher() as searcher:
                reader = searcher.reader()
                if "category" in reader.fieldnames():
                    return list(reader.field_texts("category"))
        except (RuntimeError, OSError) as exc:
            print(f"[SkillSearchIndex] Failed reading categories from index: {exc}")

        return []

    def _fallback_get_categories(self) -> list[str]:
        """Fallback get categories."""
        categories = set()
        for skill in self.skills_data:
            cat = skill.get("category", "other")
            categories.add(cat)
        return list(categories)

    def get_tags(self) -> list[str]:
        """Get all unique tags."""
        if not WHOOSH_AVAILABLE:
            return self._fallback_get_tags()

        try:
            with self.ix.searcher() as searcher:
                reader = searcher.reader()
                if "tags" in reader.fieldnames():
                    all_tags = set()
                    for tag_text in reader.field_texts("tags"):
                        all_tags.update(tag_text.split())
                    return list(all_tags)
        except (RuntimeError, OSError) as exc:
            print(f"[SkillSearchIndex] Failed reading tags from index: {exc}")

        return []

    def _fallback_get_tags(self) -> list[str]:
        """Fallback get tags."""
        tags = set()
        for skill in self.skills_data:
            tags.update(skill.get("tags", []))
        return list(tags)

    def clear_index(self) -> bool:
        """Clear all indexed data."""
        if not WHOOSH_AVAILABLE:
            self.skills_data = []
            return True

        try:
            # Delete index directory
            import shutil

            if self.index_dir.exists():
                shutil.rmtree(self.index_dir)

            # Recreate
            self.index_dir.mkdir(parents=True, exist_ok=True)
            self.ix = create_in(self.index_dir, self.schema)

            return True
        except OSError as e:
            print(f"Error clearing index: {e}")
            return False

    def get_index_stats(self) -> dict:
        """Get index statistics."""
        if not WHOOSH_AVAILABLE:
            return {"total_skills": len(self.skills_data), "whoosh_available": False}

        try:
            with self.ix.searcher() as searcher:
                return {
                    "total_skills": searcher.doc_count(),
                    "whoosh_available": True,
                    "index_dir": str(self.index_dir),
                }
        except RuntimeError:
            return {"total_skills": 0, "whoosh_available": True, "error": "Cannot access index"}
