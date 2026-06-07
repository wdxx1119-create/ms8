from __future__ import annotations

import json
from pathlib import Path

import pytest

from ms8.absorb import incremental_processor
from ms8.absorb.fs_watcher import handle_event
from ms8.absorb.health import absorb_health_summary
from ms8.absorb.incremental_processor import process_pending
from ms8.absorb.kg import extract_absorb_knowledge_graph, kg_extract_health
from ms8.absorb.repository import (
    count_status,
    list_chunks_by_status,
    list_quarantine,
    repository_integrity,
    search_chunks,
)
from ms8.absorb.reviewer import (
    approve_all,
    approve_chunk,
    auto_submit_by_tier,
    export_review_items,
    reject_all,
    reject_chunk,
    restore_rejected_chunk,
    rollback_auto_writes,
    submit_chunk,
)
from ms8.absorb.scope import add_allowed_root, auto_write_tier, set_auto_submit_summaries, set_auto_write_tier
from ms8.absorb.search import rebuild_search_index
from ms8.absorb.spotlight_bootstrap import bootstrap_authorized_roots
from ms8.ask import run_ask
from ms8.runtime import ensure_runtime_dirs


def test_absorb_rescan_ingest_local_index(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("MS8_HOME", str(tmp_path / ".ms8"))
    monkeypatch.setenv("MS8_CONFIG_DIR", str(tmp_path / ".ms8" / "config"))
    root = tmp_path / "docs"
    root.mkdir()
    (root / "python.md").write_text("Python project notes for local memory search.", encoding="utf-8")

    assert add_allowed_root(root)["ok"] is True
    scan = bootstrap_authorized_roots()
    assert scan["indexed"] == 1

    ingest = process_pending()
    assert ingest["processed"] == 1
    counts = count_status()
    assert counts["files"].get("LOCAL_INDEXED") == 1
    matches = search_chunks("Python", limit=5)
    assert len(matches) == 1
    assert "python.md" in matches[0]["canonical_path"]


def test_absorb_quarantine_metadata_has_no_raw_secret(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("MS8_HOME", str(tmp_path / ".ms8"))
    monkeypatch.setenv("MS8_CONFIG_DIR", str(tmp_path / ".ms8" / "config"))
    root = tmp_path / "docs"
    root.mkdir()
    secret = "api_key = sk_test_123456789ABCDEFG"
    (root / "secret.txt").write_text(f"Do not store this {secret}", encoding="utf-8")

    assert add_allowed_root(root)["ok"] is True
    assert bootstrap_authorized_roots()["indexed"] == 1
    ingest = process_pending()
    assert ingest["results"][0]["quarantined"] == 1
    assert len(list_quarantine()) == 1

    qfiles = list((tmp_path / ".ms8" / "absorb" / "quarantine").glob("*.json"))
    assert len(qfiles) == 1
    payload = json.loads(qfiles[0].read_text(encoding="utf-8"))
    assert "sk_test_123456789ABCDEFG" not in json.dumps(payload)
    assert payload["decision"] == "quarantine"


def test_absorb_review_approve_and_reject(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("MS8_HOME", str(tmp_path / ".ms8"))
    monkeypatch.setenv("MS8_CONFIG_DIR", str(tmp_path / ".ms8" / "config"))
    root = tmp_path / "docs"
    root.mkdir()
    (root / "pii.txt").write_text("Contact user at test@example.com", encoding="utf-8")

    assert add_allowed_root(root)["ok"] is True
    assert bootstrap_authorized_roots()["indexed"] == 1
    ingest = process_pending()
    assert ingest["results"][0]["pending_review"] == 1
    pending = list_chunks_by_status(("PENDING_REVIEW",), limit=5)
    chunk_id = pending[0]["chunk_id"]

    approved = approve_chunk(chunk_id)
    assert approved["ok"] is True
    rejected = reject_chunk(chunk_id)
    assert rejected["ok"] is True
    restored = restore_rejected_chunk(chunk_id)
    assert restored["ok"] is True
    assert restored["status"] == "restored"
    already = approve_chunk(chunk_id)
    assert already["ok"] is True
    assert approve_chunk(chunk_id)["status"] == "already_approved"


def test_absorb_large_file_is_filtered_before_hash(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("MS8_HOME", str(tmp_path / ".ms8"))
    monkeypatch.setenv("MS8_CONFIG_DIR", str(tmp_path / ".ms8" / "config"))
    root = tmp_path / "docs"
    root.mkdir()
    big = root / "big.md"
    big.write_text("x" * 32, encoding="utf-8")
    monkeypatch.setattr(incremental_processor, "MAX_FILE_BYTES", 8)
    monkeypatch.setattr(
        incremental_processor,
        "calculate_hash",
        lambda _path: (_ for _ in ()).throw(AssertionError("hash should not run for oversized files")),
    )

    assert add_allowed_root(root)["ok"] is True
    assert bootstrap_authorized_roots()["indexed"] == 1
    result = process_pending()["results"][0]
    assert result["status"] == "FILTERED"
    assert result["reason"] == "file_too_large"


def test_absorb_local_index_uses_governed_preview(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("MS8_HOME", str(tmp_path / ".ms8"))
    monkeypatch.setenv("MS8_CONFIG_DIR", str(tmp_path / ".ms8" / "config"))
    root = tmp_path / "docs"
    root.mkdir()
    (root / "safe.md").write_text("raw preview should be replaced", encoding="utf-8")
    monkeypatch.setattr(
        incremental_processor,
        "run_absorb_governance",
        lambda _chunk, _meta: {
            "decision": "local_index",
            "risk_level": "low",
            "matched_rule": "",
            "redacted_preview": "[SAFE_PREVIEW]",
            "reason": "test",
        },
    )

    assert add_allowed_root(root)["ok"] is True
    assert bootstrap_authorized_roots()["indexed"] == 1
    assert process_pending()["processed"] == 1
    chunks = list_chunks_by_status(("LOCAL_INDEXED",), limit=5)
    assert chunks[0]["text_preview"] == "[SAFE_PREVIEW]"


def test_absorb_review_bulk_dry_run_and_apply(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("MS8_HOME", str(tmp_path / ".ms8"))
    monkeypatch.setenv("MS8_CONFIG_DIR", str(tmp_path / ".ms8" / "config"))
    root = tmp_path / "docs"
    root.mkdir()
    (root / "pii1.txt").write_text("Email one test1@example.com", encoding="utf-8")
    (root / "pii2.txt").write_text("Email two test2@example.com", encoding="utf-8")

    assert add_allowed_root(root)["ok"] is True
    assert bootstrap_authorized_roots()["indexed"] == 2
    ingest = process_pending()
    assert ingest["processed"] == 2
    assert len(list_chunks_by_status(("PENDING_REVIEW",), limit=10)) == 2

    preview = approve_all(limit=10)
    assert preview["status"] == "dry_run"
    assert preview["count"] == 2
    assert len(list_chunks_by_status(("PENDING_REVIEW",), limit=10)) == 2

    applied = approve_all(limit=10, apply=True)
    assert applied["ok"] is True
    assert applied["count"] == 2
    assert len(list_chunks_by_status(("PENDING_REVIEW",), limit=10)) == 0


def test_absorb_review_reject_all_and_export(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("MS8_HOME", str(tmp_path / ".ms8"))
    monkeypatch.setenv("MS8_CONFIG_DIR", str(tmp_path / ".ms8" / "config"))
    root = tmp_path / "docs"
    root.mkdir()
    (root / "pii.txt").write_text("Contact test@example.com", encoding="utf-8")

    assert add_allowed_root(root)["ok"] is True
    assert bootstrap_authorized_roots()["indexed"] == 1
    assert process_pending()["processed"] == 1

    exported = export_review_items(limit=10)
    assert exported["ok"] is True
    assert exported["count"] == 1
    preview = reject_all(reason="bulk_test", limit=10)
    assert preview["status"] == "dry_run"
    applied = reject_all(reason="bulk_test", limit=10, apply=True)
    assert applied["ok"] is True
    assert len(list_chunks_by_status(("PENDING_REVIEW",), limit=10)) == 0


def test_absorb_auto_write_tier_compatibility(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("MS8_HOME", str(tmp_path / ".ms8"))
    monkeypatch.setenv("MS8_CONFIG_DIR", str(tmp_path / ".ms8" / "config"))

    assert auto_write_tier() == "OFF"
    assert set_auto_submit_summaries(True)["auto_write_tier"] == "SUMMARY_ONLY"
    assert auto_write_tier() == "SUMMARY_ONLY"
    assert set_auto_write_tier("REVIEWED_ONLY")["auto_write_tier"] == "REVIEWED_ONLY"
    assert set_auto_submit_summaries(False)["auto_write_tier"] == "OFF"


def test_absorb_low_risk_chunk_auto_write_respects_dry_run_and_cap(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("MS8_HOME", str(tmp_path / ".ms8"))
    monkeypatch.setenv("MS8_CONFIG_DIR", str(tmp_path / ".ms8" / "config"))
    root = tmp_path / "docs"
    root.mkdir()
    (root / "one.md").write_text("low risk auto write phrase one", encoding="utf-8")
    (root / "two.md").write_text("low risk auto write phrase two", encoding="utf-8")

    written: list[str] = []
    monkeypatch.setattr("ms8.runtime.write_memory", lambda text, source="local_file": written.append(text) or {"id": f"rec_{len(written)}", "text": text, "source": source})
    assert add_allowed_root(root)["ok"] is True
    assert bootstrap_authorized_roots()["indexed"] == 2
    assert process_pending()["processed"] == 2
    assert set_auto_write_tier("LOW_RISK_CHUNKS")["ok"] is True

    preview = auto_submit_by_tier(limit=10, daily_cap=1)
    assert preview["status"] == "dry_run"
    assert preview["count"] == 1
    assert written == []

    applied = auto_submit_by_tier(limit=10, daily_cap=1, apply=True)
    assert applied["status"] == "applied"
    assert applied["count"] == 1
    assert len(written) == 1
    capped = auto_submit_by_tier(limit=10, daily_cap=1, apply=True)
    assert capped["status"] == "cap_reached"


def test_absorb_reviewed_only_auto_write_requires_approval(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("MS8_HOME", str(tmp_path / ".ms8"))
    monkeypatch.setenv("MS8_CONFIG_DIR", str(tmp_path / ".ms8" / "config"))
    root = tmp_path / "docs"
    root.mkdir()
    (root / "pii.txt").write_text("Email reviewer test@example.com", encoding="utf-8")

    written: list[str] = []
    monkeypatch.setattr("ms8.runtime.write_memory", lambda text, source="local_file": written.append(text) or {"id": f"rec_{len(written)}", "text": text, "source": source})
    assert add_allowed_root(root)["ok"] is True
    assert bootstrap_authorized_roots()["indexed"] == 1
    assert process_pending()["processed"] == 1
    assert set_auto_write_tier("REVIEWED_ONLY")["ok"] is True
    assert auto_submit_by_tier(limit=10)["count"] == 0

    chunk_id = list_chunks_by_status(("PENDING_REVIEW",), limit=1)[0]["chunk_id"]
    assert approve_chunk(chunk_id)["ok"] is True
    preview = auto_submit_by_tier(limit=10)
    assert preview["count"] == 1
    applied = auto_submit_by_tier(limit=10, apply=True)
    assert applied["ok"] is True
    assert len(written) == 1


def test_absorb_auto_write_never_submits_quarantined_secret(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("MS8_HOME", str(tmp_path / ".ms8"))
    monkeypatch.setenv("MS8_CONFIG_DIR", str(tmp_path / ".ms8" / "config"))
    root = tmp_path / "docs"
    root.mkdir()
    (root / "secret.txt").write_text("api_key = sk_test_123456789ABCDEFG", encoding="utf-8")

    written: list[str] = []
    monkeypatch.setattr("ms8.runtime.write_memory", lambda text, source="local_file": written.append(text) or {"id": f"rec_{len(written)}", "text": text, "source": source})
    assert add_allowed_root(root)["ok"] is True
    assert bootstrap_authorized_roots()["indexed"] == 1
    assert process_pending()["processed"] == 1
    assert len(list_quarantine()) == 1
    assert set_auto_write_tier("LOW_RISK_CHUNKS")["ok"] is True

    preview = auto_submit_by_tier(limit=10, daily_cap=10)
    assert preview["count"] == 0
    applied = auto_submit_by_tier(limit=10, daily_cap=10, apply=True)
    assert applied["count"] == 0
    assert written == []


def test_absorb_auto_write_rollback_revokes_main_record(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("MS8_HOME", str(tmp_path / ".ms8"))
    monkeypatch.setenv("MS8_CONFIG_DIR", str(tmp_path / ".ms8" / "config"))
    root = tmp_path / "docs"
    root.mkdir()
    (root / "safe.md").write_text("rollback auto write safe phrase", encoding="utf-8")

    assert add_allowed_root(root)["ok"] is True
    assert bootstrap_authorized_roots()["indexed"] == 1
    assert process_pending()["processed"] == 1
    assert set_auto_write_tier("LOW_RISK_CHUNKS")["ok"] is True
    applied = auto_submit_by_tier(limit=10, daily_cap=10, apply=True)
    assert applied["count"] == 1

    preview = rollback_auto_writes(since_hours=1)
    assert preview["status"] == "dry_run"
    assert preview["count"] == 1
    assert preview["summary"]["records_to_revoke"] == 1
    assert preview["summary"]["files_affected"] == 1
    assert preview["summary"]["affected_files"][0].endswith("safe.md")

    out = rollback_auto_writes(since_hours=1, apply=True)
    assert out["status"] == "applied"
    assert out["summary"]["records_to_revoke"] == 1
    assert out["record_result"]["revoked"] == 1

    records = ensure_runtime_dirs()["memories"].read_text(encoding="utf-8")
    assert '"source_system": "absorb"' in records
    assert '"status": "revoked"' in records
    chunk_id = preview["planned"][0]["chunk_id"]
    chunks = list_chunks_by_status(("LOCAL_INDEXED",), limit=10)
    assert any(item["chunk_id"] == chunk_id for item in chunks)


def test_absorb_rollback_does_not_revoke_non_absorb_record(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("MS8_HOME", str(tmp_path / ".ms8"))
    monkeypatch.setenv("MS8_CONFIG_DIR", str(tmp_path / ".ms8" / "config"))
    records = ensure_runtime_dirs()["memories"]
    records.parent.mkdir(parents=True, exist_ok=True)
    records.write_text(
        "\n".join(
            [
                json.dumps(
                    {
                        "id": "rec_absorb",
                        "text": "absorb record",
                        "normalized_text": "absorb record",
                        "source": "absorb",
                        "category": "general",
                        "status": "accepted",
                        "meta": {"admission": "test", "source_system": "absorb"},
                    },
                    ensure_ascii=False,
                ),
                json.dumps(
                    {
                        "id": "rec_manual",
                        "text": "manual record",
                        "normalized_text": "manual record",
                        "source": "ask",
                        "category": "general",
                        "status": "accepted",
                        "meta": {"admission": "test"},
                    },
                    ensure_ascii=False,
                ),
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    from ms8.absorb.repository import log_event

    log_event("auto_write", "/tmp/a.md", "submitted_to_ms8", json.dumps({"record_id": "rec_absorb", "chunk_id": "chunk_a", "source_system": "absorb"}))
    log_event("auto_write", "/tmp/b.md", "submitted_to_ms8", json.dumps({"record_id": "rec_manual", "chunk_id": "chunk_b", "source_system": "absorb"}))

    out = rollback_auto_writes(since_hours=1, apply=True)
    assert out["record_result"]["revoked"] == 1
    rows = [json.loads(line) for line in records.read_text(encoding="utf-8").splitlines()]
    by_id = {row["id"]: row for row in rows}
    assert by_id["rec_absorb"]["status"] == "revoked"
    assert by_id["rec_manual"]["status"] == "accepted"


def test_absorb_repository_health_reports_wal_and_storage(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("MS8_HOME", str(tmp_path / ".ms8"))
    monkeypatch.setenv("MS8_CONFIG_DIR", str(tmp_path / ".ms8" / "config"))

    repo = repository_integrity()
    assert repo["db_exists"] is True
    assert repo["db_readable"] is True
    assert repo["db_writable"] is True
    assert repo["journal_mode"] == "wal"
    assert repo["integrity_check"] == "ok"
    assert repo["quarantine_writable"] is True

    health = absorb_health_summary()
    assert health["repository"]["journal_mode"] == "wal"
    assert health["repository"]["events_growth_risk"] == "green"
    assert health["index_consistency"]["risk"] == "green"


def test_absorb_log_event_rotates_large_events_file(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("MS8_HOME", str(tmp_path / ".ms8"))
    monkeypatch.setenv("MS8_CONFIG_DIR", str(tmp_path / ".ms8" / "config"))
    from ms8.absorb.repository import EVENTS_ROTATE_BYTES, events_path, log_event

    events = events_path()
    events.parent.mkdir(parents=True, exist_ok=True)
    events.write_text("x" * EVENTS_ROTATE_BYTES, encoding="utf-8")
    log_event("scan", "/tmp/rotate.md", "indexed")
    rotated = events.parent / "events.1.jsonl"
    assert rotated.exists()
    assert events.exists()
    assert "rotate.md" in events.read_text(encoding="utf-8")


def test_absorb_governance_detects_encrypted_private_key() -> None:
    from ms8.absorb.governance import run_absorb_governance

    out = run_absorb_governance(
        "-----BEGIN ENCRYPTED PRIVATE KEY-----abc-----END ENCRYPTED PRIVATE KEY-----",
        {},
    )
    assert out["decision"] == "quarantine"
    assert out["risk_level"] == "high"
    assert "private_key" in str(out["matched_rule"])


def test_absorb_submit_chunk_uses_runtime_write(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("MS8_HOME", str(tmp_path / ".ms8"))
    monkeypatch.setenv("MS8_CONFIG_DIR", str(tmp_path / ".ms8" / "config"))
    root = tmp_path / "docs"
    root.mkdir()
    (root / "safe.md").write_text("safe submit chunk phrase", encoding="utf-8")
    assert add_allowed_root(root)["ok"] is True
    assert bootstrap_authorized_roots()["indexed"] == 1
    assert process_pending()["processed"] == 1
    chunk_id = search_chunks("safe submit", limit=1)[0]["chunk_id"]

    result = submit_chunk(chunk_id)
    assert result["ok"] is True
    assert result["status"] == "submitted"


def test_absorb_watcher_event_indexes_file(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("MS8_HOME", str(tmp_path / ".ms8"))
    monkeypatch.setenv("MS8_CONFIG_DIR", str(tmp_path / ".ms8" / "config"))
    root = tmp_path / "docs"
    root.mkdir()
    note = root / "watch.md"
    note.write_text("watchdog absorb note", encoding="utf-8")
    assert add_allowed_root(root)["ok"] is True

    event = type("Event", (), {"src_path": str(note), "event_type": "created", "is_directory": False})()
    result = handle_event(event)
    assert result["ok"] is True
    assert result["status"] == "LOCAL_INDEXED"
    assert search_chunks("watchdog")


def test_ask_reads_absorb_index(tmp_path: Path, monkeypatch, capsys) -> None:
    monkeypatch.setenv("MS8_HOME", str(tmp_path / ".ms8"))
    monkeypatch.setenv("MS8_CONFIG_DIR", str(tmp_path / ".ms8" / "config"))
    root = tmp_path / "docs"
    root.mkdir()
    (root / "ask.md").write_text("absorb ask bridge unique phrase", encoding="utf-8")
    assert add_allowed_root(root)["ok"] is True
    assert bootstrap_authorized_roots()["indexed"] == 1
    assert process_pending()["processed"] == 1

    assert run_ask("unique phrase", limit=5) == 0
    out = capsys.readouterr().out
    assert "[absorb:.md]" in out
    assert "absorb ask bridge" in out


def test_absorb_search_uses_whoosh_after_rebuild(tmp_path: Path, monkeypatch) -> None:
    pytest.importorskip("whoosh")
    monkeypatch.setenv("MS8_HOME", str(tmp_path / ".ms8"))
    monkeypatch.setenv("MS8_CONFIG_DIR", str(tmp_path / ".ms8" / "config"))
    root = tmp_path / "docs"
    root.mkdir()
    (root / "whoosh.md").write_text("whoosh absorb bridge unique phrase", encoding="utf-8")

    assert add_allowed_root(root)["ok"] is True
    assert bootstrap_authorized_roots()["indexed"] == 1
    assert process_pending()["processed"] == 1
    assert rebuild_search_index()["ok"] is True

    from ms8.absorb.search import search_chunks as enhanced_search

    matches = enhanced_search("whoosh bridge", limit=3)
    assert matches
    assert matches[0]["search_backend"] == "whoosh"


def test_absorb_kg_extract_dry_run_keeps_evidence_and_skips_write(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("MS8_HOME", str(tmp_path / ".ms8"))
    monkeypatch.setenv("MS8_CONFIG_DIR", str(tmp_path / ".ms8" / "config"))
    root = tmp_path / "docs"
    root.mkdir()
    (root / "kg.md").write_text("Knowledge graph absorb evidence phrase", encoding="utf-8")

    assert add_allowed_root(root)["ok"] is True
    assert bootstrap_authorized_roots()["indexed"] == 1
    assert process_pending()["processed"] == 1

    class FakeKG:
        def ingest_memory(self, *args, **kwargs):  # pragma: no cover - dry-run must not call this
            raise AssertionError("dry-run should not write to KG")

    preview = extract_absorb_knowledge_graph(limit=10, kg=FakeKG())
    assert preview["status"] == "dry_run"
    assert preview["count"] == 1
    planned = preview["planned"][0]
    assert planned["memory_ref"].startswith("absorb:file_")
    assert planned["chunk_id"].startswith("chunk_file_")
    assert planned["canonical_path"].endswith("kg.md")
    assert preview["results"] == []


def test_absorb_kg_extract_apply_uses_low_risk_chunks_without_llm(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("MS8_HOME", str(tmp_path / ".ms8"))
    monkeypatch.setenv("MS8_CONFIG_DIR", str(tmp_path / ".ms8" / "config"))
    root = tmp_path / "docs"
    root.mkdir()
    (root / "safe.md").write_text("Safe local document entity relation", encoding="utf-8")
    (root / "pii.md").write_text("Contact reviewer at reviewer@example.com", encoding="utf-8")

    assert add_allowed_root(root)["ok"] is True
    assert bootstrap_authorized_roots()["indexed"] == 2
    assert process_pending()["processed"] == 2

    calls: list[dict[str, object]] = []

    class FakeKG:
        def ingest_memory(self, memory_ref, content, source="", title="", use_llm=None, force=False):
            calls.append(
                {
                    "memory_ref": memory_ref,
                    "content": content,
                    "source": source,
                    "title": title,
                    "use_llm": use_llm,
                    "force": force,
                }
            )
            return {"status": "success", "entities": 1, "relations": 0}

    applied = extract_absorb_knowledge_graph(limit=10, apply=True, force=True, kg=FakeKG())
    assert applied["status"] == "applied"
    assert applied["count"] == 1
    assert len(calls) == 1
    assert calls[0]["memory_ref"].startswith("absorb:file_")
    assert calls[0]["source"] == "absorb"
    assert calls[0]["title"] == "safe.md"
    assert calls[0]["use_llm"] is False
    assert calls[0]["force"] is True
    assert "reviewer@example.com" not in str(calls[0]["content"])
    health = kg_extract_health(limit=10)
    assert health["pending_candidates"] == 1
    assert health["applied_total"] == 1
    assert health["status_counts"]["success"] == 1
