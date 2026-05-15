from __future__ import annotations

import json
import zipfile
from pathlib import Path

from ms8.runtime import export_support_bundle_runtime


def test_support_bundle_dry_run_lists_files(monkeypatch, tmp_path: Path) -> None:
    root = tmp_path / "ms8_home"
    monkeypatch.setenv("MS8_HOME", str(root))
    (root / "health").mkdir(parents=True, exist_ok=True)
    (root / "health" / "governance_report_latest.json").write_text('{"ok":true}', encoding="utf-8")
    out = export_support_bundle_runtime(dry_run=True)
    assert out["ok"] is True
    assert out["dry_run"] is True
    assert out["count"] >= 1
    assert "health/governance_report_latest.json" in out["files"]


def test_support_bundle_writes_zip_and_redacts(monkeypatch, tmp_path: Path) -> None:
    root = tmp_path / "ms8_home"
    monkeypatch.setenv("MS8_HOME", str(root))
    (root / "health").mkdir(parents=True, exist_ok=True)
    raw = {
        "email": "test@example.com",
        "token": "github_pat_abcdefghijklmnopqrstuvwxyz1234567890",
        "path": "/Users/alice/private/project",
    }
    (root / "health" / "governance_report_latest.json").write_text(json.dumps(raw, ensure_ascii=False), encoding="utf-8")
    out_zip = root / "health" / "bundle_test.zip"
    out = export_support_bundle_runtime(output=str(out_zip), redact=True, dry_run=False)
    assert out["ok"] is True
    assert out_zip.exists()

    with zipfile.ZipFile(out_zip, "r") as zf:
        content = zf.read("health/governance_report_latest.json").decode("utf-8")
        manifest = json.loads(zf.read("bundle_manifest.json").decode("utf-8"))
    assert "[REDACTED_EMAIL]" in content
    assert "[REDACTED_TOKEN]" in content
    assert "/Users/<redacted>" in content
    assert isinstance(manifest.get("files_added", []), list)

