from __future__ import annotations

import os
import shutil
import sys
import warnings
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_PATH = PROJECT_ROOT / "src"
if str(SRC_PATH) not in sys.path:
    # Ensure tests always exercise the in-repo code, not stale site-packages copies.
    sys.path.insert(0, str(SRC_PATH))


def _relax_tree_permissions(root: Path) -> None:
    if not root.exists():
        return
    for path in sorted(root.rglob("*"), reverse=True):
        try:
            if path.is_dir():
                os.chmod(path, 0o700)
            else:
                os.chmod(path, 0o600)
        except Exception:
            continue
    try:
        os.chmod(root, 0o700)
    except Exception:
        pass


def _safe_prune(path: Path) -> None:
    _relax_tree_permissions(path)
    try:
        shutil.rmtree(path, ignore_errors=False)
    except Exception:
        shutil.rmtree(path, ignore_errors=True)


def pytest_configure() -> None:
    # Keep pytest output signal-focused in sandboxed environments.
    warnings.filterwarnings(
        "ignore",
        category=pytest.PytestWarning,
        message=r"\(rm_rf\).*",
    )


@pytest.fixture(scope="session", autouse=True)
def _prune_stale_pytest_garbage_dirs(tmp_path_factory: pytest.TempPathFactory):
    tmp_root = tmp_path_factory.getbasetemp().parent
    if tmp_root.exists():
        for garbage in tmp_root.glob("garbage-*"):
            _safe_prune(garbage)
    yield
    if tmp_root.exists():
        for garbage in tmp_root.glob("garbage-*"):
            _safe_prune(garbage)
