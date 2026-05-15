"""
Memory Blocks - Letta-style personality and context memory
"""

import json

from .config import get_config
from .file_write_guard import secure_read_text, secure_write_text


class MemoryBlocks:
    """
    Manage personality and context memory blocks (Letta-style).

    Blocks:
    - human: User information (name, preferences, projects)
    - persona: Agent personality (role, style, capabilities)
    - archival: Long-term factual knowledge
    """

    def __init__(self):
        self.config = get_config()
        self.blocks_file = self.config["memory_dir"] / "memory_blocks.json"
        self.blocks = self._load_blocks()

    def _load_blocks(self) -> dict[str, str]:
        """Load memory blocks from file."""
        if self.blocks_file.exists():
            try:
                return json.loads(secure_read_text(self.blocks_file) or "{}")
            except json.JSONDecodeError:
                return {}
        else:
            # Default blocks
            default_blocks = {
                "human": "Name: User. Status: Human. Occupation: Building AI systems.",
                "persona": "I am a helpful AI assistant with persistent memory. I learn and improve over time.",
                "archival": "",
            }
            self._save_blocks(default_blocks)
            return default_blocks

    def _save_blocks(self, blocks: dict[str, str]) -> None:
        """Save memory blocks to file."""
        self.blocks_file.parent.mkdir(parents=True, exist_ok=True)
        secure_write_text(self.blocks_file, json.dumps(blocks, indent=2, ensure_ascii=False))

    def get_block(self, label: str) -> str | None:
        """Get a memory block by label."""
        return self.blocks.get(label)

    def set_block(self, label: str, value: str) -> None:
        """Set or update a memory block."""
        self.blocks[label] = value
        self._save_blocks(self.blocks)

    def update_block(self, label: str, instruction: str, new_content: str) -> None:
        """
        Update a memory block based on instruction.

        Args:
            label: Block label (human/persona/archival)
            instruction: What to remember/update
            new_content: New content to add/modify
        """
        current = self.blocks.get(label, "")

        # Check if content already exists
        if new_content in current:
            return  # Already remembered

        # Append new content
        if current:
            updated = f"{current}\n\n• {instruction}: {new_content}"
        else:
            updated = f"• {instruction}: {new_content}"

        self.blocks[label] = updated
        self._save_blocks(self.blocks)

    def list_blocks(self) -> list[dict[str, object]]:
        """List all memory blocks with metadata."""
        return [{"label": label, "content": content, "length": len(content)} for label, content in self.blocks.items()]

    def clear_block(self, label: str) -> None:
        """Clear a specific memory block."""
        if label in self.blocks:
            self.blocks[label] = ""
            self._save_blocks(self.blocks)

    def export_blocks(self) -> str:
        """Export all blocks as formatted string (for context injection)."""
        result = []
        for label, content in self.blocks.items():
            if content.strip():
                result.append(f"[{label.upper()}]\n{content}")
        return "\n\n".join(result)

    def import_from_letta_format(self, letta_blocks: list[dict]) -> None:
        """
        Import memory blocks from Letta format.

        Args:
            letta_blocks: List of {'label': str, 'value': str}
        """
        for block in letta_blocks:
            label = block.get("label", "unknown")
            value = block.get("value", "")
            self.blocks[label] = value
        self._save_blocks(self.blocks)
