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
        (["ops", "support-bundle", "--dry-run"], "export_support_bundle_runtime"),
        (["ops", "self-check-report"], "self_check_report_runtime"),
        (["ops", "self-repair-report"], "self_repair_report_runtime"),
        (["ops", "self-repair-history", "--limit", "3"], "self_repair_history_runtime"),
        (["ops", "self-repair-rollback", "--operation-id", "op1"], "self_repair_rollback_runtime"),
        (["ops", "dedupe-now"], "repair_duplicates_after_compression"),
        (["ops", "weekly-compress", "--confirm"], "run_weekly_compression"),
        (["ops", "archived-logs", "--limit", "2"], "list_archived_logs_runtime"),
        (["ops", "subagents"], "list_subagents_runtime"),
        (["ops", "subagent-tasks", "--limit", "2"], "list_subagent_tasks_runtime"),
        (["ops", "subagent-task", "--task-id", "t1"], "get_background_subagent_task_runtime"),
        (["ops", "validation-suite"], "run_validation_suite_runtime"),
        (["ops", "backfill-ids"], "backfill_auto_memory_ids_runtime"),
        (["ops", "cleanup-memory"], "cleanup_old_memory_runtime"),
        (["ops", "monitoring-status"], "monitoring_status_runtime"),
        (["ops", "advanced-insight-status"], "advanced_insight_status_runtime"),
        (["ops", "meta-status"], "meta_cognition_status_runtime"),
        (["ops", "meta-run", "--period", "daily"], "run_meta_cognition_runtime"),
        (["ops", "context-blocks"], "get_context_with_blocks_runtime"),
        (["ops", "augmented-context", "--message", "hello"], "get_augmented_context_runtime"),
        (["ops", "github-catalog", "--org", "acme"], "get_github_skill_catalog_runtime"),
        (["ops", "git-history", "--max-count", "2"], "git_history_runtime"),
        (["ops", "git-commit", "--message", "m1"], "git_commit_runtime"),
        (["ops", "built-in-install", "--name", "smoke-skill"], "install_built_in_skill_runtime"),
        (["ops", "built-in-install-all"], "install_all_built_in_skills_runtime"),
        (["ops", "skill-install-file", "--path", "/tmp/x", "--scope", "project"], "install_skill_from_file_runtime"),
        (
            ["ops", "skill-install-search", "--name", "skillx", "--repository", "org/repo"],
            "install_skill_from_github_search_runtime",
        ),
        (["ops", "skill-install-registry", "--skill-id", "sid", "--scope", "project"], "install_skill_from_registry_runtime"),
        (["ops", "git-available"], "is_git_available_runtime"),
        (["ops", "learning-enabled"], "is_learning_enabled_runtime"),
        (["ops", "skill-load-tool", "--name", "toolx"], "load_skill_with_tool_runtime"),
        (["ops", "weekly-compress-preview"], "preview_weekly_compression_runtime"),
        (["ops", "graph-offline-cleanup", "--limit", "3"], "prepare_graph_offline_cleanup_runtime"),
        (["ops", "purge-test-memory"], "purge_test_memory_data_runtime"),
        (["ops", "feedback-rebalance", "--window", "7"], "rebalance_feedback_distribution_runtime"),
        (["ops", "skill-index-refresh"], "refresh_skill_index_runtime"),
        (["ops", "learning-run-pending"], "run_learning_tasks_runtime"),
        (["ops", "shadow-archive-spool"], "shadow_archive_spool_runtime"),
        (["ops", "short-term-restore", "--query", "topic", "--limit", "2"], "restore_short_term_by_topic_runtime"),
        (["ops", "subagent-retry", "--task-id", "tid"], "retry_background_subagent_task_runtime"),
    ],
)
def test_ops_dispatch_matrix(monkeypatch, tmp_path, capsys, argv: list[str], target_func: str) -> None:
    monkeypatch.setenv("MS8_HOME", str(tmp_path / "ms8_home"))
    monkeypatch.setenv("MS8_SHORTCUT_AUTO", "0")
    monkeypatch.setenv("OPENCLAW_MEMORY_SESSION_INGEST_ENABLED", "0")
    monkeypatch.setattr(cli, "_read_labs_enabled", lambda: True)
    monkeypatch.setattr(cli, target_func, lambda **kwargs: {"ok": True, "fn": target_func, "kwargs": kwargs})
    rc = cli.main(argv)
    out = capsys.readouterr().out
    assert rc == 0
    payload = _last_json_payload(out)
    assert payload["fn"] == target_func
