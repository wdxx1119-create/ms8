"""Project-local memory layer mounted under absorb.

Project memory is a project-oriented submodule of absorb:
absorb handles local ingestion and governance, while project_memory
turns one project into AI-friendly local context artifacts.
"""

from .cli import run_project_memory_cli

__all__ = ["run_project_memory_cli"]
