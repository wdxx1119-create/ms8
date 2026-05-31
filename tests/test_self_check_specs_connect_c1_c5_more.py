from __future__ import annotations

import json
from pathlib import Path

from ms8.engine_core.maintenance.self_check import check_specs as cs


class _Core:
    def __init__(self, root: Path) -> None:
        self.config = {"settings": {"memory": {"connect": {"root": str(root)}}}}


def _write(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def test_c1_smoke_paths(tmp_path: Path, monkeypatch) -> None:
    root = tmp_path / "connect"
    core = _Core(root)

    # missing health.json -> warn
    out_missing = cs._check_c1_mcp_server_smoke(core, {})
    assert out_missing["status"] == "warn"

    # fallback via connect_report overall_ok
    _write(root / "runtime" / "connect_report.json", {"result": {"overall_ok": True}})
    out_fallback = cs._check_c1_mcp_server_smoke(core, {})
    assert out_fallback["status"] == "pass"

    # fallback via launchctl
    (root / "runtime" / "connect_report.json").unlink(missing_ok=True)
    _write(root / "runtime" / "health.json", {"mcp_server": {"ok": False}})
    monkeypatch.setattr(cs, "_launchctl_running", lambda label: True)
    out_launchd = cs._check_c1_mcp_server_smoke(core, {})
    assert out_launchd["status"] == "pass"


def test_c2_contract_config_missing_and_exception(tmp_path: Path, monkeypatch) -> None:
    core = _Core(tmp_path / "connect")

    # config missing
    monkeypatch.setattr(cs, "_connect_package_root", lambda: tmp_path / "pkg_missing")
    out_missing = cs._check_c2_mcp_tool_contract(core, {})
    assert out_missing["status"] == "warn"

    # config exists but constructor path fails -> warn unavailable
    pkg = tmp_path / "pkg_err"
    (pkg / "config").mkdir(parents=True, exist_ok=True)
    (pkg / "config" / "mcp_config.yaml").write_text("a: 1\n", encoding="utf-8")
    monkeypatch.setattr(cs, "_connect_package_root", lambda: pkg)

    class _Broken:
        @staticmethod
        def from_config(_cfg):  # noqa: ANN001
            raise ValueError("bad cfg")

    monkeypatch.setattr(
        "ms8.connect.mcp_server.memory_service_interface.MemoryServiceInterface",
        _Broken,
        raising=False,
    )
    out_err = cs._check_c2_mcp_tool_contract(core, {})
    assert out_err["status"] == "warn"


def test_c2_contract_missing_methods_and_bad_status_payload(tmp_path: Path, monkeypatch) -> None:
    core = _Core(tmp_path / "connect")
    pkg = tmp_path / "pkg_c2_more"
    (pkg / "config").mkdir(parents=True, exist_ok=True)
    (pkg / "config" / "mcp_config.yaml").write_text("x: 1\n", encoding="utf-8")
    monkeypatch.setattr(cs, "_connect_package_root", lambda: pkg)

    class _SvcMissingMethods:
        @staticmethod
        def from_config(_cfg):  # noqa: ANN001
            return _SvcMissingMethods()

        def submit(self, *_args, **_kwargs):  # noqa: ANN001
            return {"ok": True}

        def status(self):
            return {"ok": True}

    monkeypatch.setattr(
        "ms8.connect.mcp_server.memory_service_interface.MemoryServiceInterface",
        _SvcMissingMethods,
        raising=False,
    )
    out_missing_methods = cs._check_c2_mcp_tool_contract(core, {})
    assert out_missing_methods["status"] == "fail"
    assert "methods" in out_missing_methods["details"]

    class _SvcBadStatus:
        @staticmethod
        def from_config(_cfg):  # noqa: ANN001
            return _SvcBadStatus()

        def submit(self, *_args, **_kwargs):  # noqa: ANN001
            return {"ok": True}

        def query(self, *_args, **_kwargs):  # noqa: ANN001
            return {"ok": True}

        def context(self, *_args, **_kwargs):  # noqa: ANN001
            return {"ok": True}

        def status(self):
            return {"healthy": True}

        def profile(self, *_args, **_kwargs):  # noqa: ANN001
            return {"ok": True, "content": "x"}

    monkeypatch.setattr(
        "ms8.connect.mcp_server.memory_service_interface.MemoryServiceInterface",
        _SvcBadStatus,
        raising=False,
    )
    out_bad_status = cs._check_c2_mcp_tool_contract(core, {})
    assert out_bad_status["status"] == "fail"


def test_c3_and_c4_and_c5_branches(tmp_path: Path, monkeypatch) -> None:
    core = _Core(tmp_path / "connect")
    pkg = tmp_path / "pkg_ok"
    (pkg / "config").mkdir(parents=True, exist_ok=True)
    (pkg / "config" / "mcp_config.yaml").write_text("x: 1\n", encoding="utf-8")
    (pkg / "adapter_registry").mkdir(parents=True, exist_ok=True)
    (pkg / "local_llm_adapter").mkdir(parents=True, exist_ok=True)
    (pkg / "mcp_server").mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(cs, "_connect_package_root", lambda: pkg)

    # C3 partial resources
    class _SvcPartial:
        @staticmethod
        def from_config(_cfg):  # noqa: ANN001
            return _SvcPartial()

        def profile(self, key: str):  # noqa: ANN001
            if key == "long-term":
                return {"ok": True, "content": "x"}
            return {"ok": True}

    monkeypatch.setattr(
        "ms8.connect.mcp_server.memory_service_interface.MemoryServiceInterface",
        _SvcPartial,
        raising=False,
    )
    c3_partial = cs._check_c3_mcp_resource_contract(core, {})
    assert c3_partial["status"] == "warn"

    # C4 malformed registry
    (pkg / "adapter_registry" / "adapters.json").write_text(
        json.dumps({"ok": {"status": "active"}, "bad": "x"}, ensure_ascii=False),
        encoding="utf-8",
    )
    c4_warn = cs._check_c4_adapter_registry_integrity(core, {})
    assert c4_warn["status"] == "warn"

    # C5 violations (direct import / missing interface markers)
    (pkg / "local_llm_adapter" / "adapter_llm.py").write_text(
        "from memory.core import MemoryCore\n"
        "auto_memory.process_interaction('x')\n",
        encoding="utf-8",
    )
    (pkg / "mcp_server" / "mcp_server.py").write_text("print('x')\n", encoding="utf-8")
    c5_fail = cs._check_c5_interface_single_entry(core, {})
    assert c5_fail["status"] == "fail"
