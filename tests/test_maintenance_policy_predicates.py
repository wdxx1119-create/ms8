from __future__ import annotations

from ms8.engine_core import maintenance_policy as mp


def test_basic_predicates_thresholds() -> None:
    assert mp.should_compress_memory({"memory_md_lines": 600}) is True
    assert mp.should_compress_memory({"memory_md_lines": 100}) is False
    assert mp.should_cleanup_test_pollution({"test_pollution_ratio": 0.2}) is True
    assert mp.should_cleanup_test_pollution({"test_pollution_ratio": 0.01}) is False
    assert mp.should_cleanup_test_memories({"auto_check_test_residual_count": 1}) is True
    assert mp.should_backfill_record_ids({"missing_record_ids": 3}) is True
    assert mp.should_repair_semantic_cache({"semantic_dense_missing": 50}) is True
    assert mp.should_rebalance_feedback({"feedback_dominant_ratio": 0.95}) is True


def test_batch_review_and_threshold_navigation() -> None:
    assert mp.should_trigger_batch_review({"review_backlog_pending": 100}) is True
    assert (
        mp.should_trigger_batch_review(
            {
                "review_backlog_pending": 55,
                "review_backlog_pending_soft_threshold": 50,
                "review_backlog_stale_hours": 30,
                "review_backlog_stale_hours_threshold": 24,
            }
        )
        is True
    )
    assert mp.should_trigger_batch_review({"review_backlog_pending": 10}) is False

    assert mp.should_generate_threshold_suggestions({"feedback_recent_count": 200}) is True
    assert (
        mp.should_auto_navigate_threshold_suggestions(
            {
                "threshold_auto_navigate_enabled": True,
                "threshold_suggestion_pending_count": 2,
                "threshold_suggestion_pending_min_age_minutes": 10,
                "threshold_suggestion_pending_oldest_age_minutes": 20,
            }
        )
        is True
    )
    assert (
        mp.should_auto_navigate_threshold_suggestions(
            {
                "threshold_auto_navigate_enabled": False,
                "threshold_suggestion_pending_count": 2,
            }
        )
        is False
    )


def test_kg_tiering_and_shadow_predicates() -> None:
    assert mp.should_batch_extract_kg({"kg_batch_extract_pending_signal": True}) is True
    assert mp.should_trigger_tiering({"tiering_candidate_count": 1, "tiering_candidate_threshold": 1}) is True
    assert mp.should_auto_seal({"write_fail_consecutive": 3, "write_fail_recent_30s": 2}) is True
    assert mp.should_auto_seal({"auto_seal_triggered_today": 5, "auto_seal_daily_limit": 5}) is False
    assert mp.should_auto_replay({"shadow_sealed": False, "shadow_spool_pending": 2}) is True
    assert mp.should_auto_recover({"shadow_sealed": True, "shadow_sealed_hours": 2.0, "write_fail_consecutive": 0}) is True
    assert mp.should_reset_checkpoint({"shadow_checkpoint_mismatch": True, "shadow_seal_events_24h": 5}) is True
    assert mp.should_shadow_drill({"shadow_drill_hours_since_last": 24 * 8}) is True
    assert mp.should_replay_shadow_spool({"shadow_sealed": False, "shadow_spool_pending": 3}) is True
    assert mp.should_archive_shadow_spool({"shadow_spool_replayed": 50, "shadow_spool_archive_threshold": 40}) is True
    assert mp.should_self_heal_shadow({"shadow_corrupt_line_count": 3}) is True
    assert mp.should_sync_shadow_backup({"shadow_backup_hours_since_last": 25, "shadow_backup_interval_hours": 24}) is True


def test_self_check_and_repair_predicates() -> None:
    assert mp.should_run_self_check_l1({"self_check_l1_last_run_minutes": 70}) is True
    assert mp.should_run_self_check_l2l3({"self_check_l2l3_last_run_minutes": 24 * 60 + 1}) is True
    assert mp.should_run_self_check_l4({"self_check_l4_last_run_minutes": 7 * 24 * 60 + 1}) is True
    assert (
        mp.should_run_self_repair(
            {"self_repair_enabled": True, "self_check_fail_count": 1, "self_check_error_count": 0}
        )
        is True
    )
    assert (
        mp.should_run_self_repair(
            {
                "self_repair_enabled": True,
                "self_repair_last_run_minutes": 5,
                "self_repair_min_interval_minutes": 10,
                "self_check_last_failed": True,
            }
        )
        is False
    )
    assert mp.should_force_self_check_from_alerts({"alerts_recent_critical": 1}) is True
    assert mp.should_force_self_check_from_alerts({"alerts_recent_error": 2}) is True
    assert mp.should_force_self_check_from_alerts({"alerts_recent_error": 1}) is False


def test_build_policy_actions_contains_expected_actions() -> None:
    stats = {
        "alerts_recent_critical": 1,
        "self_check_l2l3_latest_age_minutes": 24 * 60 + 10,
        "self_check_l1_latest_age_minutes": 60,
        "self_check_l4_latest_age_minutes": 7 * 24 * 60 + 1,
        "self_repair_enabled": True,
        "self_check_fail_count": 1,
        "memory_md_lines": 600,
        "auto_check_test_residual_count": 2,
        "test_pollution_ratio": 0.2,
        "missing_record_ids": 2,
        "write_fail_consecutive": 3,
        "write_fail_recent_30s": 2,
        "shadow_sealed": False,
        "shadow_spool_pending": 3,
        "shadow_checkpoint_mismatch": True,
        "shadow_seal_events_24h": 5,
        "shadow_corrupt_line_count": 1,
        "shadow_drill_hours_since_last": 24 * 8,
        "shadow_spool_replayed": 50,
        "shadow_backup_hours_since_last": 30,
        "semantic_dense_missing": 30,
        "feedback_dominant_ratio": 0.95,
        "review_backlog_pending": 100,
        "kg_batch_extract_pending_signal": True,
        "tiering_candidate_count": 2,
        "feedback_recent_count": 150,
        "threshold_auto_navigate_enabled": True,
        "threshold_suggestion_pending_count": 1,
        "threshold_suggestion_pending_oldest_age_minutes": 20,
        "threshold_suggestion_pending_min_age_minutes": 10,
    }
    actions = mp.build_policy_actions(stats)
    names = [a.action for a in actions]

    assert "self_check_l2l3" in names
    assert "self_check_l4" in names
    assert "self_repair_auto" in names
    assert "trigger_weekly_compression" in names
    assert "cleanup_test_memories" in names
    assert "purge_test_memory_data" in names
    assert "backfill_auto_memory_record_ids" in names
    assert "shadow_auto_seal" in names
    assert "shadow_auto_replay" in names
    assert "shadow_reset_checkpoint" in names
    assert "shadow_replay_spool" in names
    assert "shadow_startup_self_heal" in names
    assert "shadow_recovery_drill" in names
    assert "shadow_archive_spool" in names
    assert "shadow_sync_verified_backup" in names
    assert "repair_semantic_cache" in names
    assert "rebalance_feedback_distribution" in names
    assert "trigger_batch_review" in names
    assert "trigger_batch_extract_kg" in names
    assert "trigger_memory_tiering" in names
    assert "generate_threshold_suggestions" in names
    assert "auto_navigate_threshold_suggestions" in names

    priorities = [a.priority for a in actions]
    assert priorities == sorted(priorities)


def test_build_policy_actions_review_reason_stale_path() -> None:
    actions = mp.build_policy_actions(
        {
            "review_backlog_pending": 55,
            "review_backlog_pending_threshold": 80,
            "review_backlog_pending_soft_threshold": 50,
            "review_backlog_stale_hours": 30,
            "review_backlog_stale_hours_threshold": 24,
        }
    )
    review_actions = [a for a in actions if a.action == "trigger_batch_review"]
    assert review_actions
    assert review_actions[0].reason in {"review_backlog_stale", "review_backlog_guard"}
