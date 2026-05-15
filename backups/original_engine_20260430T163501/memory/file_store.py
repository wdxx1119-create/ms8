"""
File-based memory storage implementation
"""
import datetime
from pathlib import Path
from typing import Optional
from .config import get_config
from .file_write_guard import secure_append_text, secure_read_text, secure_write_text
from .utils import calculate_file_hash

class FileMemoryStore:
    """Handle file-based memory operations."""
    
    def __init__(self):
        self.config = get_config()
        self.memory_md_hash = ""
        self._load_memory_md_hash()
    
    def _load_memory_md_hash(self):
        """Load current hash of MEMORY.md."""
        self.memory_md_hash = calculate_file_hash(self.config['memory_md'])
    
    def append_to_daily_log(self, content: str) -> None:
        """Append content to today's daily log file."""
        today = datetime.date.today().isoformat()
        daily_dir = self.config.get("daily_dir", self.config["memory_dir"] / "daily")
        daily_dir.mkdir(parents=True, exist_ok=True)
        log_file = daily_dir / f"{today}.md"
        timestamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        secure_append_text(log_file, f"\n## {timestamp}\n{content}\n")
    
    def read_memory_md(self) -> str:
        """Read content of MEMORY.md."""
        if not self.config['memory_md'].exists():
            return "# MEMORY.md - Your Long-Term Memory\n\nThis is your curated long-term memory. Edit freely!\n"
        return secure_read_text(self.config['memory_md'])
    
    def write_memory_md(self, content: str) -> None:
        """Write content to MEMORY.md and update hash."""
        secure_write_text(self.config['memory_md'], content)
        self._load_memory_md_hash()
    
    def has_memory_md_changed(self) -> bool:
        """Check if MEMORY.md has been modified externally."""
        current_hash = calculate_file_hash(self.config['memory_md'])
        return current_hash != self.memory_md_hash
    
    def reload_memory_md_if_changed(self) -> bool:
        """Reload MEMORY.md if it has changed and update hash."""
        if self.has_memory_md_changed():
            self._load_memory_md_hash()
            return True
        return False
