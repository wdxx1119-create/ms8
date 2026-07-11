from __future__ import annotations

from ms8.connect.mcp_server import mcp_server
from ms8.connect.mcp_server.stdio_server import TOOL_DEFINITIONS


class _FakeService:
    def pre_action_check(self, action, *, memory_ids=None, explicit_user_confirmation=False):
        return {
            "ok": True,
            "decision": "deny",
            "allowed": False,
            "execution_performed": False,
            "action": action,
            "memory_ids": list(memory_ids or []),
            "explicit_user_confirmation": explicit_user_confirmation,
        }


def test_pre_action_tool_is_registered_and_never_executes(monkeypatch) -> None:
    monkeypatch.setattr(
        mcp_server.MemoryServiceInterface,
        "from_config",
        classmethod(lambda cls, cfg: _FakeService()),
    )
    assert "pre_action_check" in mcp_server.TOOL_NAMES
    assert "pre_action_check" in TOOL_DEFINITIONS
    out = mcp_server.call_tool(
        "pre_action_check",
        {
            "action": "send an email",
            "memory_ids": ["m-1"],
            "explicit_user_confirmation": False,
        },
        config={"mcp": {"enabled": True}},
    )
    assert out["ok"] is True
    assert out["allowed"] is False
    assert out["execution_performed"] is False
