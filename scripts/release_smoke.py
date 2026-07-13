from __future__ import annotations

import argparse
import os
import shutil
import stat
import subprocess
import sys
import tempfile
from collections.abc import Sequence
from importlib.resources import files
from pathlib import Path


def _configure_stdio() -> None:
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if callable(reconfigure):
            reconfigure(encoding="utf-8", errors="backslashreplace")


def _run(name: str, args: Sequence[str], env: dict[str, str]) -> None:
    print(f"[STEP] {name}")
    completed = subprocess.run(
        list(args),
        env=env,
        text=True,
        encoding="utf-8",
        errors="replace",
        capture_output=True,
        check=False,
    )
    if completed.stdout:
        print(completed.stdout.rstrip())
    if completed.stderr:
        print(completed.stderr.rstrip(), file=sys.stderr)
    if completed.returncode != 0:
        raise RuntimeError(f"{name} failed with exit code {completed.returncode}")
    print(f"[OK] {name}")


def _make_writable(path: Path) -> None:
    try:
        path.chmod(path.stat().st_mode | stat.S_IWUSR)
    except OSError:
        pass


def _cleanup(path: Path) -> None:
    if not path.exists():
        return
    for item in path.rglob("*"):
        _make_writable(item)
    _make_writable(path)
    shutil.rmtree(path, ignore_errors=False)


def _validate_packaged_resources() -> None:
    from ms8.connect.scripts.common import connect_package_root, load_cfg, read_json

    root = connect_package_root()
    cfg_path = root / "config" / "mcp_config.yaml"
    registry_path = root / "adapter_registry" / "adapters.json"
    assert cfg_path.is_file(), f"missing packaged MCP config: {cfg_path}"
    assert registry_path.is_file(), f"missing packaged adapter registry: {registry_path}"

    cfg = load_cfg()
    assert cfg.get("mcp", {}).get("enabled") is True, cfg

    registry = read_json(registry_path)
    adapter = registry.get("ms8_default_adapter", {})
    assert adapter.get("status") == "active", registry
    capabilities = set(adapter.get("capabilities", []))
    expected = {"submit", "query", "context", "status", "profile"}
    assert expected.issubset(capabilities), capabilities

    package_root = files("ms8")
    assert package_root.joinpath("connect/config/mcp_config.yaml").is_file()
    assert package_root.joinpath("connect/adapter_registry/adapters.json").is_file()
    print("[OK] packaged MCP resources")


def _validate_absorb_parser(ms8_home: Path) -> None:
    from ms8.absorb.parser import parse_document

    sample = ms8_home / "absorb smoke 空格.txt"
    sample.write_text("MS8 absorb smoke document\n", encoding="utf-8")
    document = parse_document(sample)
    assert document.parse_status == "parsed", document
    assert document.file_type == ".txt", document
    assert "MS8 absorb smoke document" in document.content_text
    assert len(document.content_hash) == 64
    print("[OK] Absorb text parser")


def _validate_records() -> None:
    from ms8.runtime import ensure_runtime_dirs

    records = ensure_runtime_dirs()["memories"]
    assert records.is_file(), f"missing memory records file: {records}"
    text = records.read_text(encoding="utf-8")
    assert "cross platform release smoke memory" in text, text[-1000:]
    print("[OK] persisted ask record")


def main() -> int:
    _configure_stdio()
    parser = argparse.ArgumentParser(description="Run an isolated installed-wheel smoke test.")
    parser.add_argument(
        "--base-dir",
        type=Path,
        help="Use this test-only root instead of creating a temporary directory.",
    )
    parser.add_argument("--keep", action="store_true", help="Keep the isolated test root.")
    args = parser.parse_args()

    if args.base_dir is None:
        base = Path(tempfile.mkdtemp(prefix="ms8 release smoke 空格 "))
    else:
        base = args.base_dir.resolve()
        if base.exists():
            _cleanup(base)
        base.mkdir(parents=True)

    home = base / "home 空格"
    ms8_home = home / ".ms8"
    data = ms8_home / "data"
    config = ms8_home / "config"
    logs = ms8_home / "logs"
    for directory in (home, data, config, logs):
        directory.mkdir(parents=True, exist_ok=True)

    overrides = {
        "HOME": str(home),
        "USERPROFILE": str(home),
        "MS8_HOME": str(ms8_home),
        "MS8_DATA_DIR": str(data),
        "MS8_CONFIG_DIR": str(config),
        "MS8_LOG_DIR": str(logs),
        "MS8_DOCTOR_ALLOW_DEGRADED": "1",
        "OPENCLAW_MEMORY_SESSION_INGEST_ENABLED": "0",
        "PYTHONUTF8": "1",
        "PYTHONIOENCODING": "utf-8",
    }
    env = os.environ.copy()
    env.update(overrides)
    # In-process validators import the installed package directly, so they must
    # use the same isolated paths as the CLI subprocesses.
    os.environ.update(overrides)

    command = [sys.executable, "-m", "ms8"]
    try:
        _run("help", [*command, "--help"], env)
        _run("version", [*command, "version"], env)
        _run("doctor", [*command, "doctor"], env)
        _validate_packaged_resources()
        _validate_absorb_parser(ms8_home)
        _run(
            "ask write",
            [*command, "ask", "记住 cross platform release smoke memory"],
            env,
        )
        _run(
            "ask search",
            [*command, "ask", "cross platform release smoke", "--limit", "5"],
            env,
        )
        _validate_records()
        _run("clean dry-run", [*command, "clean", "--dry-run"], env)
        _run("reset dry-run", [*command, "reset", "--dry-run"], env)
        _run("uninstall dry-run", [*command, "uninstall", "--dry-run"], env)
    finally:
        if args.keep:
            print(f"[INFO] keeping isolated root: {base}")
        else:
            _cleanup(base)
            print(f"[INFO] cleaned isolated root: {base}")

    print("[OK] cross-platform release smoke completed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
