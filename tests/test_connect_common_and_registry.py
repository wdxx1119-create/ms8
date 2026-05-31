from __future__ import annotations

import json
from pathlib import Path

import pytest

from ms8.connect.adapter_registry import registry as registry_mod
from ms8.connect.scripts import common


def test_connect_root_uses_env_and_creates_dirs(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root = tmp_path / "connect-root"
    monkeypatch.setenv("OPENCLAW_MEMORY_AUTO_ROOT", str(root))
    got = common.connect_root()
    assert got == root
    assert (got / "runtime").exists()
    assert (got / "logs").exists()


def test_connect_root_falls_back_when_unwritable(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENCLAW_MEMORY_AUTO_ROOT", str(tmp_path / "no-write"))
    monkeypatch.setattr(common, "_is_writable_dir", lambda _p: False)
    monkeypatch.chdir(tmp_path)
    got = common.connect_root()
    assert got == (tmp_path / ".ms8" / "connect").resolve()
    assert (got / "runtime").exists()
    assert (got / "logs").exists()


def test_load_yaml_and_json_error_paths(tmp_path: Path) -> None:
    bad_yaml = tmp_path / "bad.yaml"
    bad_yaml.write_text("a: [", encoding="utf-8")
    assert common.load_yaml(bad_yaml) == {}

    seq_yaml = tmp_path / "seq.yaml"
    seq_yaml.write_text("- a\n- b\n", encoding="utf-8")
    assert common.load_yaml(seq_yaml) == {}

    bad_json = tmp_path / "bad.json"
    bad_json.write_text("{", encoding="utf-8")
    assert common.read_json(bad_json) == {}

    seq_json = tmp_path / "seq.json"
    seq_json.write_text("[1,2,3]", encoding="utf-8")
    assert common.read_json(seq_json) == {}


def test_write_json_and_append_audit(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    out = tmp_path / "x" / "a.json"
    payload = {"a": 1}
    common.write_json(out, payload)
    assert json.loads(out.read_text(encoding="utf-8")) == payload

    connect_root = tmp_path / "connect"
    (connect_root / "logs").mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(common, "connect_root", lambda: connect_root)
    common.append_audit("hello")
    audit = connect_root / "logs" / "audit.log"
    text = audit.read_text(encoding="utf-8")
    assert "hello" in text


def test_choose_python_and_run(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(common.shutil, "which", lambda name: "/usr/bin/python3" if name == "python3" else None)
    assert common.choose_python() == "/usr/bin/python3"

    result = common.run(["python3", "-c", "print('ok')"])
    assert result["ok"] is True
    assert result["code"] == 0
    assert "ok" in result["stdout"]


def test_snapshot_config_uses_helpers(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(common, "connect_root", lambda: tmp_path / "r")
    monkeypatch.setattr(common, "connect_package_root", lambda: tmp_path / "p")
    monkeypatch.setattr(common, "load_cfg", lambda: {"k": "v"})
    snap = common.snapshot_config()
    assert snap["connect_root"] == str(tmp_path / "r")
    assert snap["package_root"] == str(tmp_path / "p")
    assert snap["config"] == {"k": "v"}


def test_registry_read_write_and_upsert(tmp_path: Path) -> None:
    base = tmp_path / "registry"
    assert registry_mod.load_registry(base) == {}
    path = registry_mod.save_registry({"a": {"status": "active"}}, base)
    assert path.name == "adapters.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    assert data["a"]["status"] == "active"

    row = registry_mod.upsert_adapter("tool", status="on", capabilities=["x", 2], metadata={"m": 1}, base_dir=base)
    assert row["status"] == "on"
    assert row["capabilities"] == ["x", "2"]
    assert row["metadata"] == {"m": 1}


def test_registry_load_invalid_and_class_wrapper(tmp_path: Path) -> None:
    base = tmp_path / "registry2"
    bad = base / "adapters.json"
    bad.parent.mkdir(parents=True, exist_ok=True)
    bad.write_text("{", encoding="utf-8")
    assert registry_mod.load_registry(base) == {}

    reg = registry_mod.AdapterRegistry(base)
    reg.register("k1", capabilities=["a"])
    items = reg.list_adapters()
    assert "k1" in items
    assert items["k1"]["capabilities"] == ["a"]


def test_adapter_registry_write_allowed_true_and_false(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    reg = registry_mod.AdapterRegistry(tmp_path / "r")

    good_root = tmp_path / "good"
    monkeypatch.setattr(registry_mod, "connect_root", lambda: good_root)
    assert reg.is_write_allowed() is True

    bad_root = tmp_path / "bad-file"
    bad_root.write_text("x", encoding="utf-8")
    monkeypatch.setattr(registry_mod, "connect_root", lambda: bad_root)
    assert reg.is_write_allowed() is False
