from __future__ import annotations

from pathlib import Path

from ms8.engine_core import git_utils as gu


class _FakeDiffItem:
    def __init__(self, path: str):
        self.a_path = path


class _FakeIndex:
    def __init__(self, diff_items=None, raise_on_add: bool = False):
        self._diff_items = diff_items or []
        self._raise_on_add = raise_on_add
        self.added = []
        self.commits = []

    def diff(self, _other):
        return self._diff_items

    def add(self, files):
        if self._raise_on_add:
            raise RuntimeError("add failed")
        self.added.extend(files)

    def commit(self, msg):
        self.commits.append(msg)


class _FakeCommit:
    def __init__(self):
        self.hexsha = "abcdef123456"
        self.message = "test commit\n"
        self.author = "dev"
        from datetime import datetime, timezone

        self.committed_datetime = datetime.now(timezone.utc)
        self.stats = type("_S", (), {"files": {"a": {}, "b": {}}})()


class _FakeRepo:
    def __init__(self, index=None, untracked=None, heads=None):
        self.index = index or _FakeIndex()
        self.untracked_files = untracked or []
        self.heads = heads or ["main"]

    def iter_commits(self, max_count=10):
        return [_FakeCommit() for _ in range(min(max_count, 2))]


def _cfg(tmp_path: Path):
    ws = tmp_path / "ws"
    mem = ws / "memory"
    ws.mkdir(parents=True, exist_ok=True)
    mem.mkdir(parents=True, exist_ok=True)
    return {
        "workspace_dir": ws,
        "memory_dir": mem,
        "settings": {
            "memory": {
                "git": {"enabled": True, "repo_path": ".", "auto_commit": True},
            }
        },
    }


def test_get_memory_files_filters_paths(tmp_path: Path, monkeypatch):
    cfg = _cfg(tmp_path)
    ws = cfg["workspace_dir"]
    mem = cfg["memory_dir"]
    (ws / "MEMORY.md").write_text("x", encoding="utf-8")
    (ws / "config.yaml").write_text("x", encoding="utf-8")
    (mem / "auto_memory_records.jsonl").write_text("x", encoding="utf-8")
    (mem / "a.log").write_text("x", encoding="utf-8")
    (mem / "archive").mkdir(parents=True, exist_ok=True)
    (mem / "archive" / "old.json").write_text("x", encoding="utf-8")

    monkeypatch.setattr(gu, "get_config", lambda: cfg)
    monkeypatch.setattr(gu, "GIT_AVAILABLE", False)
    gm = gu.GitMemoryManager()
    files = gm._get_memory_files()
    assert "MEMORY.md" in files
    assert "config.yaml" in files
    assert "memory/auto_memory_records.jsonl" in files
    assert "memory/a.log" not in files
    assert "memory/archive/old.json" not in files


def test_has_changes_detects_diff_and_untracked(tmp_path: Path, monkeypatch):
    cfg = _cfg(tmp_path)
    monkeypatch.setattr(gu, "get_config", lambda: cfg)
    monkeypatch.setattr(gu, "GIT_AVAILABLE", True)

    gm = gu.GitMemoryManager()
    gm.repo = _FakeRepo(
        index=_FakeIndex(diff_items=[_FakeDiffItem("memory/auto_memory_records.jsonl")]),
        untracked=["MEMORY.md"],
    )
    monkeypatch.setattr(gm, "_get_memory_files", lambda: ["memory/auto_memory_records.jsonl", "MEMORY.md"])
    assert gm.has_changes() is True


def test_commit_if_needed_paths(tmp_path: Path, monkeypatch):
    cfg = _cfg(tmp_path)
    monkeypatch.setattr(gu, "get_config", lambda: cfg)
    monkeypatch.setattr(gu, "GIT_AVAILABLE", True)
    gm = gu.GitMemoryManager()
    idx = _FakeIndex()
    gm.repo = _FakeRepo(index=idx)
    monkeypatch.setattr(gm, "has_changes", lambda: True)
    monkeypatch.setattr(gm, "_get_memory_files", lambda: ["MEMORY.md"])

    assert gm.commit_if_needed("msg") is True
    assert idx.added == ["MEMORY.md"]
    assert idx.commits == ["msg"]

    gm.repo = _FakeRepo(index=_FakeIndex(raise_on_add=True))
    monkeypatch.setattr(gm, "has_changes", lambda: True)
    assert gm.commit_if_needed("msg") is False


def test_history_available_and_unavailable(tmp_path: Path, monkeypatch):
    cfg = _cfg(tmp_path)
    monkeypatch.setattr(gu, "get_config", lambda: cfg)
    monkeypatch.setattr(gu, "GIT_AVAILABLE", True)
    gm = gu.GitMemoryManager()
    gm.repo = _FakeRepo()
    hist = gm.get_commit_history(max_count=2)
    assert len(hist) == 2
    assert set(hist[0].keys()) == {"hash", "message", "author", "date", "files_changed"}
    assert gm.is_available() is True

    monkeypatch.setattr(gu, "GIT_AVAILABLE", False)
    assert gm.get_commit_history() == []
