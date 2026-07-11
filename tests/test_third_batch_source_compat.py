from __future__ import annotations

from ms8.connect.mcp_server import mcp_server


class _CaptureService:
    def __init__(self) -> None:
        self.payloads: list[dict] = []

    def submit(self, payload):
        self.payloads.append(dict(payload))
        return {"ok": True, "accepted": True}


def test_submit_source_preserves_default_and_records_explicit_client(tmp_path, monkeypatch) -> None:
    service = _CaptureService()
    monkeypatch.setenv("OPENCLAW_MEMORY_AUTO_ROOT", str(tmp_path / "connect"))
    monkeypatch.setattr(mcp_server.MemoryServiceInterface, "from_config", lambda _cfg: service)

    default = mcp_server.call_tool(
        "submit",
        {"content": "default submit source"},
        config={"mcp": {"enabled": True}},
    )
    explicit = mcp_server.call_tool(
        "submit",
        {"content": "explicit submit source", "client": "Codex"},
        config={"mcp": {"enabled": True}},
    )

    assert default["ok"] is True
    assert explicit["ok"] is True
    assert service.payloads[0]["source"] == "mcp:submit"
    assert service.payloads[1]["source"] == "mcp:codex"


def test_batch_source_preserves_default_and_records_explicit_client(tmp_path, monkeypatch) -> None:
    service = _CaptureService()
    monkeypatch.setenv("OPENCLAW_MEMORY_AUTO_ROOT", str(tmp_path / "connect"))
    monkeypatch.setattr(mcp_server.MemoryServiceInterface, "from_config", lambda _cfg: service)

    default = mcp_server.call_tool(
        "batch_submit",
        {"memories": [{"content": "default batch source"}]},
        config={"mcp": {"enabled": True}},
    )
    explicit = mcp_server.call_tool(
        "batch_submit",
        {
            "client_name": "Claude",
            "memories": [{"content": "explicit batch source"}],
        },
        config={"mcp": {"enabled": True}},
    )

    assert default["ok"] is True
    assert explicit["ok"] is True
    assert service.payloads[0]["source"] == "mcp:batch_submit"
    assert service.payloads[1]["source"] == "mcp:claude"
