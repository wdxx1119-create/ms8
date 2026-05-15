"""MS8 connect layer."""

from __future__ import annotations

__all__ = ["MemoryServiceInterface"]


def __getattr__(name: str):
    if name == "MemoryServiceInterface":
        from ms8.connect.mcp_server.memory_service_interface import MemoryServiceInterface

        return MemoryServiceInterface
    raise AttributeError(name)
