from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]


def _run(args: list[str], env: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "ms8", *args],
        capture_output=True,
        text=True,
        env=env,
    )


def _env(tmp_path) -> dict[str, str]:
    env = dict(os.environ)
    env["MS8_HOME"] = str(tmp_path / "ms8_home")
    env["MS8_DESKTOP"] = str(tmp_path / "desktop")
    env["MS8_SHORTCUT_AUTO"] = "0"
    env["MS8_ENGINE_MODE"] = "local"
    env["OPENCLAW_MEMORY_SESSION_INGEST_ENABLED"] = "0"
    env["PYTHONPATH"] = str(PROJECT_ROOT / "src")
    return env


def test_import_ms8() -> None:
    code = "import ms8; print(ms8.__version__)"
    env = dict(os.environ)
    env["PYTHONPATH"] = str(PROJECT_ROOT / "src")
    cp = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, env=env)
    assert cp.returncode == 0
    assert "0.2.0" in cp.stdout


def test_help_and_version(tmp_path) -> None:
    env = _env(tmp_path)
    cp = _run(["--help"], env=env)
    assert cp.returncode == 0

    cp = _run(["version"], env=env)
    assert cp.returncode == 0
    assert "ms8 0.2.0" in cp.stdout


def test_demo_default_and_minimal(tmp_path) -> None:
    env = _env(tmp_path)
    cp = _run(["demo"], env=env)
    assert cp.returncode == 0
    assert "✅ demo completed" in cp.stdout
    assert "retrieved demo memory" in cp.stdout

    cp = _run(["demo", "--scenario", "minimal"], env=env)
    assert cp.returncode == 0
    assert "✅ demo completed" in cp.stdout


def test_demo_stub(tmp_path) -> None:
    env = _env(tmp_path)
    env["MS8_ENV"] = "stub"
    cp = _run(["demo"], env=env)
    assert cp.returncode == 0
    assert "✅ demo completed" in cp.stdout


def test_doctor_and_module_entry(tmp_path) -> None:
    env = _env(tmp_path)
    cp = _run(["doctor"], env=env)
    assert cp.returncode == 0
    assert "Status: healthy" in cp.stdout
    assert "entries" in cp.stdout


def test_dashboard_and_shortcut_commands(tmp_path) -> None:
    env = _env(tmp_path)

    cp = _run(["dashboard"], env=env)
    assert cp.returncode == 0
    assert "MS8 Dashboard" in cp.stdout
    assert "expression-router" in cp.stdout

    cp = _run(["shortcut", "install"], env=env)
    assert cp.returncode == 0
    assert "shortcuts installed" in cp.stdout

    cp = _run(["shortcut", "status"], env=env)
    assert cp.returncode == 0
    assert "MS8.command: yes" in cp.stdout

    cp = _run(["shortcut", "remove"], env=env)
    assert cp.returncode == 0


def test_chinese_search_and_ask(tmp_path) -> None:
    env = _env(tmp_path)
    cp = _run(["ask", "记住 中文搜索优化体验"], env=env)
    assert cp.returncode == 0

    cp = _run(["ask", "中文搜索"], env=env)
    assert cp.returncode == 0
    assert "matches:" in cp.stdout
    assert "中文搜索优化体验" in cp.stdout


def test_engine_status_and_watch_once(tmp_path) -> None:
    env = _env(tmp_path)
    cp = _run(["engine", "status"], env=env)
    assert cp.returncode == 0
    assert "mode:" in cp.stdout

    cp = _run(["watch", "--once", "--interval", "10"], env=env)
    assert cp.returncode == 0
    assert "watch tick:" in cp.stdout


def test_first_run_prints_connect_bootstrap_summary(tmp_path) -> None:
    env = _env(tmp_path)
    cp = _run(["engine", "status"], env=env)
    assert cp.returncode == 0
    assert "[ms8] first-run setup completed." in cp.stdout
    assert "[ms8] auto-connect:" in cp.stdout


def test_doctor_set_risk_thresholds(tmp_path) -> None:
    env = _env(tmp_path)
    cp = _run(
        [
            "doctor",
            "--set-risk",
            "--red-fallback-write-gt",
            "12",
            "--yellow-pending-review-gt",
            "9",
        ],
        env=env,
    )
    assert cp.returncode == 0
    assert "updated governance risk thresholds" in cp.stdout
    cfg = Path(env["MS8_HOME"]) / "config.json"
    data = json.loads(cfg.read_text(encoding="utf-8"))
    red = data["governance_risk"]["red"]
    yellow = data["governance_risk"]["yellow"]
    assert red["fallback_write_count_gt"] == 12
    assert yellow["pending_review_gt"] == 9


def test_lifecycle_commands(tmp_path) -> None:
    env = _env(tmp_path)
    cp = _run(["clean", "--dry-run"], env=env)
    assert cp.returncode == 0
    assert '"operation": "clean"' in cp.stdout

    cp = _run(["reset", "--dry-run"], env=env)
    assert cp.returncode == 0
    assert '"operation": "reset"' in cp.stdout

    cp = _run(["uninstall", "--dry-run"], env=env)
    assert cp.returncode == 0
    assert '"operation": "uninstall"' in cp.stdout

    cp = _run(["uninstall"], env=env)
    assert cp.returncode == 2
    assert "pass --confirm UNINSTALL" in cp.stderr


def test_shadow_recover_requires_confirm_or_dry_run(tmp_path) -> None:
    env = _env(tmp_path)
    cp = _run(["shadow", "recover", "--dry-run"], env=env)
    assert cp.returncode == 0
    assert '"dry_run": true' in cp.stdout

    cp = _run(["shadow", "recover"], env=env)
    assert cp.returncode == 2
    assert "pass --confirm SHADOW_RECOVERY" in cp.stderr


def test_synthetic_rollback_auto_preview(tmp_path) -> None:
    env = _env(tmp_path)
    cp = _run(["labs", "enable"], env=env)
    assert cp.returncode == 0
    cp = _run(["synthetic", "rollback-auto", "--since-hours", "1", "--preview"], env=env)
    assert cp.returncode == 0
    assert '"status"' in cp.stdout
