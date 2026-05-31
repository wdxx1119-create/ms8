from __future__ import annotations

import json

import pytest

from ms8 import cli


def _last_json_payload(raw: str) -> dict:
    lines = [line for line in raw.splitlines() if line.strip()]
    for idx in range(len(lines)):
        candidate = "\n".join(lines[idx:])
        try:
            parsed = json.loads(candidate)
        except json.JSONDecodeError:
            continue
        if isinstance(parsed, dict):
            return parsed
    raise AssertionError(f"no json payload found in output: {raw!r}")


@pytest.mark.parametrize(
    ("argv", "target_func"),
    [
        (["security", "status"], "security_status_runtime"),
        (["security", "enable", "--password", "p"], "security_enable_runtime"),
        (["security", "disable", "--password", "p"], "security_disable_runtime"),
        (["security", "unlock", "--password", "p"], "security_unlock_runtime"),
        (["security", "lock"], "security_lock_runtime"),
        (["security", "recover", "--recovery-key", "rk", "--new-password", "np"], "security_recover_runtime"),
        (["shadow", "status"], "shadow_status_runtime"),
        (["shadow", "health"], "shadow_health_runtime"),
        (["shadow", "seal", "--reason", "r", "--level", "hard"], "shadow_seal_runtime"),
        (["shadow", "unseal", "--reason", "u"], "shadow_unseal_runtime"),
        (["shadow", "recover", "--dry-run", "--max-events", "5"], "shadow_recover_runtime"),
        (["skill", "list"], "list_skills_runtime"),
        (["skill", "install", "--url", "https://x", "--scope", "project"], "install_skill_runtime"),
        (["skill", "uninstall", "--name", "n", "--scope", "project"], "uninstall_skill_runtime"),
        (["skill", "search", "q", "--category", "all", "--limit", "3"], "search_skills_runtime"),
        (["skill", "updates"], "skill_updates_runtime"),
        (["skill", "categories"], "skill_categories_runtime"),
        (["skill", "tags"], "skill_tags_runtime"),
        (["skill", "suggest", "m", "--limit", "2"], "skill_suggest_runtime"),
        (
            ["skill", "github-search", "--query", "x", "--category", "all", "--min-stars", "1", "--sort-by", "stars", "--limit", "2"],
            "skill_github_search_runtime",
        ),
        (["skill", "index-stats"], "skill_index_stats_runtime"),
        (["graph", "stats"], "graph_stats_runtime"),
        (["graph", "extract", "--limit", "3", "--force"], "graph_extract_runtime"),
        (["graph", "maintain"], "graph_maint_runtime"),
        (["graph", "repair-access", "--min-access", "2"], "graph_repair_access_runtime"),
        (["graph", "search", "n", "--entity-type", "person", "--limit", "2"], "graph_search_entities_runtime"),
        (
            ["graph", "relations", "--entity-name", "a", "--relation-type", "uses", "--direction", "out", "--limit", "2"],
            "graph_list_relations_runtime",
        ),
        (
            ["graph", "neighbors", "--entity-name", "a", "--depth", "2", "--relation-type", "uses", "--limit", "2"],
            "graph_neighbors_runtime",
        ),
        (["graph", "path", "--start", "a", "--end", "b", "--max-depth", "3"], "graph_path_runtime"),
        (["graph", "timeline", "--days", "7", "--limit", "5"], "graph_timeline_runtime"),
        (["graph", "health"], "graph_health_runtime"),
        (["review", "list"], "review_list_runtime"),
        (["review", "status"], "review_list_runtime"),
        (
            [
                "review",
                "batch",
                "--mode",
                "balanced",
                "--limit",
                "4",
                "--accept-conf-min",
                "0.8",
                "--reject-conf-max",
                "0.2",
                "--per-category-limit",
                "2",
                "--drain-reject-conf-max",
                "0.1",
            ],
            "review_batch_runtime",
        ),
        (["review", "relabel", "--memory-id", "m1", "--category", "decision", "--notes", "n"], "review_relabel_runtime"),
        (["review", "threshold-list"], "threshold_list_runtime"),
        (["review", "threshold-approve", "--approval-id", "a1", "--approver", "u"], "threshold_approve_runtime"),
        (["review", "threshold-reject", "--approval-id", "a1", "--approver", "u", "--reason", "r"], "threshold_reject_runtime"),
        (
            [
                "feedback",
                "record",
                "--memory-id",
                "m1",
                "--category",
                "quality",
                "--signal",
                "good",
                "--helpful",
                "true",
                "--note",
                "n",
                "--source",
                "user",
                "--confidence",
                "0.7",
            ],
            "feedback_record_runtime",
        ),
    ],
)
def test_top_level_dispatch_matrix(monkeypatch, tmp_path, capsys, argv: list[str], target_func: str) -> None:
    monkeypatch.setenv("MS8_HOME", str(tmp_path / "ms8_home"))
    monkeypatch.setenv("MS8_SHORTCUT_AUTO", "0")
    monkeypatch.setattr(cli, "onboarding_status", lambda: {"done": True})
    monkeypatch.setattr(cli, "ensure_shortcuts_once", lambda: None)
    monkeypatch.setattr(cli, target_func, lambda **kwargs: {"ok": True, "fn": target_func, "kwargs": kwargs})
    rc = cli.main(argv)
    out = capsys.readouterr().out
    assert rc == 0
    payload = _last_json_payload(out)
    assert payload["fn"] == target_func


def test_shadow_recover_requires_confirm(monkeypatch, tmp_path, capsys) -> None:
    monkeypatch.setenv("MS8_HOME", str(tmp_path / "ms8_home"))
    monkeypatch.setenv("MS8_SHORTCUT_AUTO", "0")
    monkeypatch.setattr(cli, "onboarding_status", lambda: {"done": True})
    monkeypatch.setattr(cli, "ensure_shortcuts_once", lambda: None)
    rc = cli.main(["shadow", "recover", "--max-events", "3"])
    err = capsys.readouterr().err
    assert rc == 2
    assert "SHADOW_RECOVERY" in err
