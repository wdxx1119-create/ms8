"""
Subagents - Simple task delegation system (Letta-style)
"""

import json
import uuid
from datetime import datetime

from .config import get_config


class SubAgent:
    """Simple subagent for task delegation."""

    def __init__(self, name: str, description: str, tools: list[str] | None = None):
        self.name = name
        self.description = description
        self.tools = tools or ["all"]
        self.id = str(uuid.uuid4())[:8]

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "name": self.name,
            "description": self.description,
            "tools": self.tools,
        }


class SubAgentManager:
    """
    Manage and execute subagents (simplified Letta-style).

    Built-in subagents:
    - explore: Search and analyze
    - memory: Organize and clean memory
    - recall: Search history
    - reflection: Background consolidation
    """

    def __init__(self):
        self.config = get_config()
        self.subagents_dir = self.config["memory_dir"] / "subagents"
        self.subagents_dir.mkdir(parents=True, exist_ok=True)

        # Built-in subagents
        self.built_in_subagents = [
            SubAgent("explore", "Search and analyze information", ["read", "search", "list"]),
            SubAgent("memory", "Organize and clean memory blocks", ["memory_edit", "cleanup"]),
            SubAgent("recall", "Search conversation history", ["search", "read"]),
            SubAgent("reflection", "Background memory consolidation", ["memory_edit", "analyze"]),
        ]

        # Load custom subagents
        self.custom_subagents = self._load_custom_subagents()

    def _load_custom_subagents(self) -> list[SubAgent]:
        """Load custom subagents from .md files."""
        custom = []
        if self.subagents_dir.exists():
            for md_file in self.subagents_dir.glob("*.md"):
                try:
                    with open(md_file, encoding="utf-8") as f:
                        content = f.read()

                    # Parse YAML frontmatter (simplified)
                    if content.startswith("---"):
                        parts = content.split("---", 2)
                        if len(parts) >= 3:
                            frontmatter = parts[1].strip()
                            # Simple parsing
                            name = self._extract_yaml_value(frontmatter, "name")
                            desc = self._extract_yaml_value(frontmatter, "description")
                            if name and desc:
                                custom.append(SubAgent(name, desc))
                except (OSError, RuntimeError, TypeError, UnicodeDecodeError, ValueError) as e:
                    print(f"Error loading subagent {md_file}: {e}")

        return custom

    def _extract_yaml_value(self, yaml_str: str, key: str) -> str | None:
        """Extract value from simple YAML."""
        for line in yaml_str.split("\n"):
            if line.startswith(f"{key}:"):
                return line.split(":", 1)[1].strip().strip("\"'")
        return None

    def list_subagents(self) -> list[dict]:
        """List all available subagents."""
        all_agents = []
        for agent in self.built_in_subagents:
            agent_dict = agent.to_dict()
            agent_dict["type"] = "built-in"
            all_agents.append(agent_dict)

        for agent in self.custom_subagents:
            agent_dict = agent.to_dict()
            agent_dict["type"] = "custom"
            all_agents.append(agent_dict)

        return all_agents

    def spawn(self, subagent_name: str, task: str, background: bool = False) -> dict:
        """
        Spawn a subagent to execute a task.

        Args:
            subagent_name: Name of subagent to spawn
            task: Task description
            background: Run in background (don't wait for result)

        Returns:
            Dict with status and result (if not background)
        """
        # Find subagent
        subagent = None
        for agent in self.built_in_subagents + self.custom_subagents:
            if agent.name.lower() == subagent_name.lower():
                subagent = agent
                break

        if not subagent:
            return {"status": "error", "error": f'Subagent "{subagent_name}" not found'}

        # Execute task
        if background:
            # Run in background
            return self._spawn_background(subagent, task)
        else:
            # Run in foreground (wait for result)
            return self._spawn_foreground(subagent, task)

    def _spawn_foreground(self, subagent: SubAgent, task: str) -> dict:
        """Execute subagent in foreground (wait for result)."""
        start_time = datetime.now()

        # Simulate subagent execution (simplified)
        # In a real implementation, this would spawn a separate process
        result = self._execute_subagent_task(subagent, task)

        end_time = datetime.now()
        duration = (end_time - start_time).total_seconds()

        return {
            "status": "success",
            "subagent": subagent.name,
            "task": task,
            "result": result,
            "duration_seconds": duration,
            "timestamp": end_time.isoformat(),
        }

    def _spawn_background(self, subagent: SubAgent, task: str) -> dict:
        """Execute subagent in background (return immediately)."""
        # Create task file
        task_file = self.subagents_dir / f"task_{uuid.uuid4().hex[:8]}.json"
        task_data = {
            "subagent": subagent.name,
            "task": task,
            "status": "queued",
            "created_at": datetime.now().isoformat(),
        }

        with open(task_file, "w", encoding="utf-8") as f:
            json.dump(task_data, f, indent=2)

        # In a real implementation, this would spawn a background process
        # For now, just return acknowledgment
        return {
            "status": "launched",
            "subagent": subagent.name,
            "task": task,
            "task_id": task_file.stem,
            "message": f'Subagent "{subagent.name}" launched in background',
        }

    def _execute_subagent_task(self, subagent: SubAgent, task: str) -> str:
        """
        Execute subagent task (simplified implementation).

        In a full implementation, this would:
        1. Create a separate agent context
        2. Run the subagent with its own tools and memory
        3. Return only the final result (not intermediate steps)
        """
        # Simplified execution - just return a placeholder
        # Real implementation would use separate LLM calls

        if subagent.name == "explore":
            return f"[Explore] Analyzed: {task}. Found relevant information."
        elif subagent.name == "memory":
            return f"[Memory] Organized and cleaned memory for: {task}"
        elif subagent.name == "recall":
            return f"[Recall] Searched history for: {task}"
        elif subagent.name == "reflection":
            return f"[Reflection] Consolidated memories about: {task}"
        else:
            return f"[{subagent.name}] Completed: {task}"

    def create_custom_subagent(
        self, name: str, description: str, instructions: str, tools: list[str] | None = None
    ) -> dict:
        """
        Create a custom subagent.

        Args:
            name: Unique identifier
            description: When to use this subagent
            instructions: System prompt for the subagent
            tools: Allowed tools

        Returns:
            Dict with status and subagent info
        """
        # Create .md file
        md_file = self.subagents_dir / f"{name}.md"

        content = f"""---
name: {name}
description: {description}
tools: {", ".join(tools) if tools else "all"}
---

{instructions}
"""

        with open(md_file, "w", encoding="utf-8") as f:
            f.write(content)

        # Reload custom subagents
        self.custom_subagents = self._load_custom_subagents()

        return {
            "status": "success",
            "message": f'Created custom subagent "{name}"',
            "subagent": {"name": name, "description": description, "file": str(md_file)},
        }
