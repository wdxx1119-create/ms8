"""
Git integration utilities for memory version control
"""

from datetime import datetime
from pathlib import Path
from typing import Any, cast

try:
    import git as git_module

    GIT_AVAILABLE = True
except ImportError:
    GIT_AVAILABLE = False
    git_module = cast(Any, None)

from .config import get_config


class GitMemoryManager:
    """Handle Git operations for memory files."""

    def __init__(self):
        self.config = get_config()
        self.git_enabled = self.config["settings"]["memory"]["git"]["enabled"]
        self.repo_path = self.config["settings"]["memory"]["git"]["repo_path"]
        self.auto_commit = self.config["settings"]["memory"]["git"]["auto_commit"]

        if not Path(self.repo_path).is_absolute():
            self.repo_path = self.config["workspace_dir"] / self.repo_path

        self.repo = None
        if GIT_AVAILABLE and self.git_enabled:
            self._init_repo()

    def _init_repo(self) -> bool:
        """Initialize or open Git repository."""
        try:
            # Check if it's already a git repository
            git_dir = Path(self.repo_path) / ".git"
            if git_dir.exists():
                self.repo = git_module.Repo(self.repo_path)
            else:
                # Initialize new repository
                self.repo = git_module.Repo.init(self.repo_path)
                # Add initial commit if repository is empty
                if not self.repo.heads:
                    self.repo.index.add(["."])
                    self.repo.index.commit("Initial commit: Memory module setup")
            return True
        except (OSError, RuntimeError, TypeError, ValueError) as e:
            print(f"Error initializing Git repository: {e}")
            self.repo = None
            return False

    def _get_memory_files(self) -> list[str]:
        """Get list of memory-related files to track (allowlist + safety exclusions)."""
        memory_files: list[str] = []
        workspace = Path(self.config["workspace_dir"])
        memory_dir = Path(self.config["memory_dir"])

        explicit_files = [
            workspace / "MEMORY.md",
            workspace / "config.yaml",
            workspace / "config.project.yaml",
            memory_dir / "memory.db",
            memory_dir / "knowledge_graph.db",
            memory_dir / "auto_memory_records.jsonl",
            memory_dir / "auto_memory_index.json",
            memory_dir / "knowledge_feedback.jsonl",
            memory_dir / "knowledge_feedback_rebalanced.jsonl",
            memory_dir / "synthetic_candidates.json",
            memory_dir / "synthetic_history.json",
            memory_dir / "synthetic_gaps.json",
            memory_dir / "maintenance_state.json",
            memory_dir / "governance_report.json",
            memory_dir / "graph_relation_quality.json",
            memory_dir / "context_optimization_suggestions_latest.json",
        ]
        dynamic_globs = [
            ("*.md", memory_dir),
            ("*.json", memory_dir),
            ("*.jsonl", memory_dir),
        ]
        excluded_prefixes = (
            "backups/",
            "restore_drill/",
            "whoosh_index/",
            "cleanup_snapshots/",
            "archive/",
            "subagent_logs/",
            "subagent_tasks/",
            "skill_index/",
        )
        excluded_suffixes = (
            ".log",
            ".archived.log",
            ".bak",
        )

        def _add(path: Path) -> None:
            if not path.exists() or not path.is_file():
                return
            try:
                rel = str(path.relative_to(self.repo_path))
            except ValueError:
                return
            norm = rel.replace("\\", "/")
            if any(norm.startswith(f"memory/{p}") for p in excluded_prefixes):
                return
            if any(norm.endswith(suf) for suf in excluded_suffixes):
                return
            if norm not in memory_files:
                memory_files.append(norm)

        for p in explicit_files:
            _add(p)
        for pattern, root in dynamic_globs:
            for p in root.glob(pattern):
                _add(p)
        return sorted(memory_files)

    def has_changes(self) -> bool:
        """Check if memory files have uncommitted changes."""
        if not self.repo or not GIT_AVAILABLE:
            return False

        try:
            memory_files = self._get_memory_files()
            if not memory_files:
                return False

            # Check if any memory files are modified or untracked
            changed_files = []
            for item in self.repo.index.diff(None):  # Modified files
                if item.a_path in memory_files:
                    changed_files.append(item.a_path)

            for item in self.repo.untracked_files:  # Untracked files
                if item in memory_files:
                    changed_files.append(item)

            return len(changed_files) > 0

        except (OSError, RuntimeError, TypeError, ValueError) as e:
            print(f"Error checking Git changes: {e}")
            return False

    def commit_if_needed(self, message: str | None = None) -> bool:
        """
        Commit memory files if there are changes.

        Args:
            message: Custom commit message. If None, uses default format.

        Returns:
            True if commit was made, False otherwise.
        """
        if not self.repo or not GIT_AVAILABLE or not self.git_enabled:
            return False

        if not self.has_changes():
            return False

        try:
            # Add memory files to index
            memory_files = self._get_memory_files()
            self.repo.index.add(memory_files)

            # Create commit message
            if not message:
                timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                message = f"Update memory on {timestamp}"

            # Commit changes
            self.repo.index.commit(message)
            return True

        except (OSError, RuntimeError, TypeError, ValueError) as e:
            print(f"Error committing to Git: {e}")
            return False

    def manual_commit(self, message: str) -> bool:
        """Manually trigger a commit with custom message."""
        return self.commit_if_needed(message)

    def get_commit_history(self, max_count: int = 10) -> list[dict]:
        """Get recent commit history for memory files."""
        if not self.repo or not GIT_AVAILABLE:
            return []

        try:
            commits = []
            for commit in self.repo.iter_commits(max_count=max_count):
                commits.append(
                    {
                        "hash": commit.hexsha[:8],
                        "message": commit.message.strip(),
                        "author": str(commit.author),
                        "date": commit.committed_datetime.isoformat(),
                        "files_changed": len(commit.stats.files),
                    }
                )
            return commits
        except (OSError, RuntimeError, TypeError, ValueError) as e:
            print(f"Error getting commit history: {e}")
            return []

    def is_available(self) -> bool:
        """Check if Git functionality is available."""
        return GIT_AVAILABLE and self.git_enabled and self.repo is not None
