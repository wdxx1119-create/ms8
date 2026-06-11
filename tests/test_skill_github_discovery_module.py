from __future__ import annotations

import base64
from pathlib import Path

import pytest


def _mk_mod(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    from ms8.engine_core import skill_github_discovery as mod

    cfg = {
        "workspace_dir": str(tmp_path / "workspace"),
        "memory_dir": tmp_path / "memory",
        "settings": {"memory": {"skills_system": {"github_enabled": True, "cache_ttl_hours": 6}}},
    }
    (tmp_path / "memory").mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(mod, "get_config", lambda: cfg)
    return mod


class _Resp:
    def __init__(self, status_code=200, payload=None, headers=None):
        self.status_code = status_code
        self._payload = payload if payload is not None else {}
        self.headers = headers or {}

    def json(self):
        return self._payload


def test_load_runtime_token(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    mod = _mk_mod(monkeypatch, tmp_path)
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    g = mod.GitHubSkillDiscovery(github_token=None)
    assert g._load_runtime_token() is None

    monkeypatch.setenv("GITHUB_TOKEN", "abc")
    assert mod.GitHubSkillDiscovery(github_token=None)._load_runtime_token() == "abc"


def test_cache_and_enabled_toggle(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    mod = _mk_mod(monkeypatch, tmp_path)
    monkeypatch.setenv("OPENCLAW_MEMORY_DISABLE_GITHUB_SYNC", "1")
    g = mod.GitHubSkillDiscovery(github_token="x")
    g._save_cache([{"name": "cached"}])
    out = g.search_skills(limit=5)
    assert out and out[0]["name"] == "cached"

    payload = g._load_cache()
    assert g._cache_valid(payload) is True


def test_local_skill_fallback(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    mod = _mk_mod(monkeypatch, tmp_path)
    g = mod.GitHubSkillDiscovery(github_token="x")
    d = Path(g.workspace_dir) / ".skills" / "alpha"
    d.mkdir(parents=True, exist_ok=True)
    res = g._local_skill_fallback(query="alp", limit=3)
    assert res and res[0]["source"] == "local_workspace"


def test_request_rate_limit_and_retry(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    mod = _mk_mod(monkeypatch, tmp_path)
    g = mod.GitHubSkillDiscovery(github_token="x")

    seq = iter(
        [
            _Resp(status_code=500),
            _Resp(status_code=200, payload={"ok": True}),
        ]
    )
    monkeypatch.setattr(g.session, "get", lambda *_a, **_k: next(seq))
    r = g._request("http://x")
    assert r and r.status_code == 200

    rr = _Resp(status_code=403, headers={"X-RateLimit-Reset": "9999999999"})
    monkeypatch.setattr(g.session, "get", lambda *_a, **_k: rr)
    blocked = g._request("http://x")
    assert blocked and blocked.status_code == 403
    assert g.rate_limited_until is not None


def test_parse_and_filter_helpers(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    mod = _mk_mod(monkeypatch, tmp_path)
    g = mod.GitHubSkillDiscovery(github_token="x")

    content = """---
name: s
description: d
tags: [python, test]
---
body
"""
    meta = g._parse_skill_frontmatter(content)
    assert meta and meta["name"] == "s"
    assert g._parse_skill_frontmatter("no-front") is None

    skills = [
        {"name": "python-skill", "description": "x", "tags": ["tool"], "stars": 2},
        {"name": "java", "description": "y", "tags": ["python"], "stars": 5},
    ]
    assert len(g._filter_by_query(skills, "python")) == 2
    assert len(g._filter_by_tags(skills, ["tool"])) == 1
    assert g._sort_skills(skills, "name")[0]["name"] == "java"
    assert g._sort_skills(skills, "stars")[0]["stars"] == 5


def test_get_skill_metadata_and_repo_search(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    mod = _mk_mod(monkeypatch, tmp_path)
    g = mod.GitHubSkillDiscovery(github_token="x")

    fm = """---
name: alpha
description: hello
tags: [a]
---
"""
    b64 = base64.b64encode(fm.encode("utf-8")).decode("utf-8")

    def _req(url, params=None):  # noqa: ARG001
        if "/contents/" in url:
            return _Resp(200, {"content": b64, "html_url": "https://h"})
        return _Resp(200, {})

    monkeypatch.setattr(g, "_request", _req)
    meta = g._get_skill_metadata("o", "r", "skills/alpha", {"stargazers_count": 7, "html_url": "repo"})
    assert meta and meta["repository"] == "o/r" and meta["stars"] == 7

    # _search_repo_skills using root and skills dirs
    repo_info = {"stargazers_count": 20, "html_url": "repo"}
    skills_dir = [{"type": "dir", "path": "skills/a", "name": "a"}]
    root_dir = [{"name": "SKILL.md", "path": "SKILL.md"}]

    def _session_get(url, timeout=None):  # noqa: ARG001
        if url.endswith("/repos/o/r"):
            return _Resp(200, repo_info)
        if url.endswith("/repos/o/r/contents/skills"):
            return _Resp(200, skills_dir)
        return _Resp(200, root_dir)

    monkeypatch.setattr(g.session, "get", _session_get)
    monkeypatch.setattr(g, "_get_skill_metadata", lambda *_a, **_k: {"name": "alpha", "tags": []})
    out = g._search_repo_skills("o/r", min_stars=1)
    assert out and out[0]["name"] == "alpha"


def test_search_skills_paths_and_catalog(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    mod = _mk_mod(monkeypatch, tmp_path)
    g = mod.GitHubSkillDiscovery(github_token="x")
    monkeypatch.setattr(g, "skill_repos", ["o/r"])
    monkeypatch.setattr(
        g,
        "_search_repo_skills",
        lambda *a, **k: [{"name": "n1", "description": "python", "tags": ["x"], "category": "dev", "stars": 11, "repository": "o/r"}],
    )
    monkeypatch.setattr(g, "_search_github_code", lambda *a, **k: [])
    out = g.search_skills(query="python", tags=["x"], limit=5)
    assert out and out[0]["name"] == "n1"

    catalog = g.get_skill_catalog()
    assert catalog["total_skills"] >= 1
    trending = g.get_trending_skills(days=7, limit=3)
    assert trending and trending[0]["stars"] >= 10


def test_recommendations_and_rate_limited_cached(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    mod = _mk_mod(monkeypatch, tmp_path)
    g = mod.GitHubSkillDiscovery(github_token="x")
    g._save_cache([{"name": "c1"}])
    g.rate_limited_until = 9999999999
    cached = g.search_skills(limit=3)
    assert cached and cached[0]["name"] == "c1"

    monkeypatch.setattr(
        g,
        "search_skills",
        lambda query=None, limit=10, **k: [
            {"repository": "o/r", "name": "a", "description": "python api", "tags": ["python"]},
            {"repository": "o/r", "name": "a", "description": "python api", "tags": ["python"]},
            {"repository": "o/r2", "name": "b", "description": "infra", "tags": ["ops"]},
        ],
    )
    rec = g.get_skill_recommendations("python api", limit=2)
    assert rec and rec[0]["relevance_score"] >= rec[-1]["relevance_score"]

