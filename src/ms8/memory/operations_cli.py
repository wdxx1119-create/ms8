"""Explicit operational CLI for MS8 memory-ledger-v1.

Every command requires an explicit workspace. Read, rebuild, lifecycle mutation,
and production migration paths remain opt-in and fail closed. Destructive actions
are dry-run by default and require exact confirmation tokens.
"""

from __future__ import annotations

import argparse
import json
from collections.abc import Mapping, Sequence
from dataclasses import asdict
from pathlib import Path
from typing import Any

from .application.legacy_migration import PreparedMigration, prepare_legacy_migration
from .application.lifecycle import LifecycleMutationError, MemoryLifecycleService
from .application.production_migration import (
    BackupTarget,
    ProductionMigrationController,
    ProductionMigrationError,
)
from .application.projection_recovery import ProjectionRecoveryError, ProjectionRecoveryService
from .application.projection_service import ProjectionCoordinator
from .application.replay import replay_transactions
from .compat.memory_service import (
    LedgerCompatibilityError,
    LedgerCompatibilityPaths,
    build_ledger_memory_compatibility_adapter,
)
from .domain.ledger import GENESIS_HASH, canonical_json
from .domain.models import Actor, Claim
from .infrastructure.durable_io import atomic_write_bytes
from .infrastructure.fts_projection import FtsProjectionAdapter
from .infrastructure.graph_projection import GraphProjectionAdapter
from .infrastructure.jsonl_ledger import JsonlRecordStore
from .infrastructure.search_projection import SearchProjectionAdapter
from .infrastructure.sqlite_projection_adapter import SQLiteProjectionAdapter
from .infrastructure.vector_projection import VectorProjectionAdapter
from .ports.record_store import HeadMismatchError, LedgerIntegrityError
from .runtime_format import (
    LEDGER_V1_RUNTIME_FORMAT,
    RuntimeFormatManifest,
    RuntimeFormatManifestError,
    evaluate_runtime_format,
    load_runtime_format_manifest,
)


class LedgerOperationsError(RuntimeError):
    """Raised when a guarded ledger operation cannot proceed safely."""


def _workspace(value: str) -> Path:
    raw = str(value or "").strip()
    if not raw:
        raise LedgerOperationsError("--workspace is required")
    return Path(raw).expanduser().resolve()


def _paths(workspace: Path) -> LedgerCompatibilityPaths:
    return LedgerCompatibilityPaths(
        runtime_manifest=workspace / "memory" / "runtime-format.json",
        ledger_root=workspace / "memory" / "ledger-v1",
        sqlite_projection=workspace / "memory" / "projections" / "memory.sqlite3",
        search_projection=workspace / "memory" / "projections" / "search.json",
        graph_projection=workspace / "memory" / "projections" / "graph.json",
        fts_projection=workspace / "memory" / "projections" / "fts.json",
        vector_projection=workspace / "memory" / "projections" / "vector.json",
    )


def _runtime(workspace: Path) -> tuple[LedgerCompatibilityPaths, JsonlRecordStore, ProjectionCoordinator]:
    paths = _paths(workspace)
    store = JsonlRecordStore(paths.ledger_root)
    if paths.fts_projection is None or paths.vector_projection is None:
        raise LedgerOperationsError("full ledger projection paths are required")
    coordinator = ProjectionCoordinator(
        store,
        (
            SQLiteProjectionAdapter(paths.sqlite_projection),
            SearchProjectionAdapter(paths.search_projection),
            FtsProjectionAdapter(paths.fts_projection),
            VectorProjectionAdapter(paths.vector_projection),
            GraphProjectionAdapter(paths.graph_projection),
        ),
    )
    return paths, store, coordinator


def _atomic_write(path: Path, data: bytes) -> None:
    atomic_write_bytes(path, data)


def _write_manifest(path: Path, manifest: RuntimeFormatManifest, expected: RuntimeFormatManifest) -> None:
    if load_runtime_format_manifest(path) != expected:
        raise LedgerOperationsError("runtime-format manifest changed during operation")
    _atomic_write(path, (canonical_json(manifest.to_dict()) + "\n").encode("utf-8"))
    if load_runtime_format_manifest(path) != manifest:
        raise LedgerOperationsError("runtime-format manifest update verification failed")


def _authorized_runtime(workspace: Path) -> tuple[
    LedgerCompatibilityPaths,
    JsonlRecordStore,
    ProjectionCoordinator,
    RuntimeFormatManifest,
]:
    paths, store, coordinator = _runtime(workspace)
    manifest = load_runtime_format_manifest(paths.runtime_manifest)
    decision = evaluate_runtime_format(manifest)
    if manifest.active_format != LEDGER_V1_RUNTIME_FORMAT or not decision.allowed:
        raise LedgerOperationsError(f"ledger-v1 runtime is not authorized: {decision.reason}")
    verification = store.verify()
    if not verification.valid:
        raise LedgerIntegrityError("ledger verification failed: " + ",".join(verification.reason_codes))
    current_head = verification.last_valid_hash or GENESIS_HASH
    if manifest.ledger_head != current_head:
        raise LedgerOperationsError("runtime manifest head does not match authoritative ledger")
    return paths, store, coordinator, manifest


def _load_json(path: Path) -> Any:
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise LedgerOperationsError(f"unable to read JSON: {path}") from exc


def _load_jsonl(path: Path) -> tuple[dict[str, Any], ...]:
    rows: list[dict[str, Any]] = []
    try:
        lines = Path(path).read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeDecodeError) as exc:
        raise LedgerOperationsError(f"unable to read JSONL: {path}") from exc
    for line_number, line in enumerate(lines, start=1):
        if not line.strip():
            continue
        try:
            payload = json.loads(line)
        except json.JSONDecodeError as exc:
            raise LedgerOperationsError(f"invalid JSONL record at line {line_number}") from exc
        if not isinstance(payload, dict):
            raise LedgerOperationsError(f"JSONL record at line {line_number} must be an object")
        rows.append(payload)
    return tuple(rows)


def _backup_targets(values: Sequence[str], source_jsonl: Path | None = None) -> tuple[BackupTarget, ...]:
    targets: list[BackupTarget] = []
    if source_jsonl is not None:
        targets.append(BackupTarget("legacy_source", source_jsonl.resolve()))
    for raw in values:
        name, separator, path_text = str(raw).partition("=")
        if not separator or not name.strip() or not path_text.strip():
            raise LedgerOperationsError("--backup-target must use NAME=PATH")
        targets.append(BackupTarget(name.strip(), Path(path_text).expanduser().resolve()))
    names = [item.name for item in targets]
    if len(names) != len(set(names)):
        raise LedgerOperationsError("backup target names must be unique")
    return tuple(targets)


def _migration_controller(
    workspace: Path,
    *,
    backup_targets: Sequence[BackupTarget],
) -> tuple[ProductionMigrationController, JsonlRecordStore, ProjectionCoordinator]:
    paths, store, coordinator = _runtime(workspace)
    controller = ProductionMigrationController(
        runtime_manifest_path=paths.runtime_manifest,
        backup_root=workspace / "memory" / "migration-backups",
        record_store=store,
        projection_coordinator=coordinator,
        backup_targets=backup_targets,
    )
    return controller, store, coordinator


def _prepared(source: Path, migration_id: str, recorded_at: str) -> tuple[tuple[dict[str, Any], ...], PreparedMigration]:
    rows = _load_jsonl(source)
    prepared = prepare_legacy_migration(rows, migration_id=migration_id, recorded_at=recorded_at)
    return rows, prepared


def _adapter(workspace: Path):
    return build_ledger_memory_compatibility_adapter(
        {"memory_ledger_v1": {"enabled": True}},
        workspace,
    )


def _doctor(workspace: Path) -> dict[str, object]:
    paths, store, coordinator = _runtime(workspace)
    manifest = load_runtime_format_manifest(paths.runtime_manifest)
    decision = evaluate_runtime_format(manifest)
    verification = store.verify()
    current_head = verification.last_valid_hash or GENESIS_HASH
    reasons: list[str] = []
    projection_ready = False
    projection_reasons: tuple[str, ...] = ()
    if manifest.active_format == LEDGER_V1_RUNTIME_FORMAT:
        if not decision.allowed:
            reasons.append(decision.reason)
        if not verification.valid:
            reasons.extend(f"ledger:{item}" for item in verification.reason_codes)
        if manifest.ledger_head != current_head:
            reasons.append("manifest_head_mismatch")
        if verification.valid:
            status = coordinator.status()
            projection_ready = status.ready_for_query
            projection_reasons = status.reason_codes
            if not status.ready_for_query:
                reasons.extend(f"projection:{item}" for item in status.reason_codes)
    else:
        reasons.append("legacy_runtime_active")
    return {
        "ok": not reasons or reasons == ["legacy_runtime_active"],
        "status": "healthy" if not reasons else ("inactive" if reasons == ["legacy_runtime_active"] else "degraded"),
        "workspace": str(workspace),
        "active_format": manifest.active_format,
        "manifest_generation": manifest.generation,
        "manifest_head": manifest.ledger_head,
        "ledger_valid": verification.valid,
        "ledger_head": current_head,
        "last_sequence": verification.last_sequence or 0,
        "projection_ready": projection_ready,
        "projection_reason_codes": list(projection_reasons),
        "ledger_v1_flag_enabled": decision.ledger_v1_flag_enabled,
        "reason_codes": reasons,
        "read_only": True,
    }


def _mutation_confirmation(action: str, target: str, expected_head: str) -> str:
    return f"MUTATE_LEDGER_V1:{action}:{target}:{expected_head}"


def _mutation_plan(args: argparse.Namespace, store: JsonlRecordStore) -> dict[str, object]:
    verification = store.verify()
    expected = str(args.expected_head or "").strip()
    current_head = verification.last_valid_hash or GENESIS_HASH
    if expected != current_head:
        raise HeadMismatchError(f"expected head {expected}, current head {current_head}")
    state = replay_transactions(store.iterate())
    action = str(args.action)
    target = str(getattr(args, "claim_id", "") or "").strip()
    if action == "resolve-conflict":
        target = str(args.conflict_id or "").strip()
        if target not in state.conflicts:
            raise LifecycleMutationError(f"unknown conflict: {target}")
    else:
        view = state.claims.get(target)
        if view is None:
            raise LifecycleMutationError(f"unknown claim: {target}")
        if view.current_status in {"superseded", "revoked", "expired"}:
            raise LifecycleMutationError(f"claim {target} is already terminal: {view.current_status}")
    replacement: Claim | None = None
    if action in {"correct", "supersede"}:
        payload = _load_json(Path(args.replacement_json))
        if not isinstance(payload, Mapping):
            raise LedgerOperationsError("replacement JSON must contain an object")
        replacement = Claim.from_dict(payload)
    return {
        "ok": True,
        "dry_run": not bool(args.apply),
        "action": action,
        "target": target,
        "expected_head": expected,
        "replacement": replacement.to_dict() if replacement is not None else None,
        "required_confirmation": _mutation_confirmation(action, target, expected),
    }


def _apply_mutation(args: argparse.Namespace, workspace: Path) -> dict[str, object]:
    paths, store, coordinator, manifest = _authorized_runtime(workspace)
    plan = _mutation_plan(args, store)
    if not args.apply:
        return plan
    required = str(plan["required_confirmation"])
    if str(args.confirm or "") != required:
        raise LedgerOperationsError("exact mutation confirmation token is required")

    snapshot = store.snapshot()
    previous_manifest_bytes = paths.runtime_manifest.read_bytes()
    service = MemoryLifecycleService(store)
    actor = Actor(kind=str(args.actor_kind), id=str(args.actor_id))
    reason = str(args.reason)
    recorded_at = str(args.recorded_at)
    expected_head_hash = str(args.expected_head)
    try:
        if args.action in {"revoke", "forget", "expire"}:
            method = getattr(service, str(args.action))
            result = method(
                target_claim_id=str(args.claim_id),
                actor=actor,
                reason=reason,
                recorded_at=recorded_at,
                expected_head_hash=expected_head_hash,
            )
        elif args.action in {"correct", "supersede"}:
            payload = _load_json(Path(args.replacement_json))
            if not isinstance(payload, Mapping):
                raise LedgerOperationsError("replacement JSON must contain an object")
            replacement = Claim.from_dict(payload)
            method = getattr(service, str(args.action))
            result = method(
                target_claim_id=str(args.claim_id),
                replacement=replacement,
                actor=actor,
                reason=reason,
                recorded_at=recorded_at,
                expected_head_hash=expected_head_hash,
            )
        elif args.action == "resolve-conflict":
            result = service.resolve_conflict(
                conflict_id=str(args.conflict_id),
                winning_claim_id=str(args.winning_claim_id),
                claim_ids=tuple(args.claim_ids),
                actor=actor,
                reason=reason,
                recorded_at=recorded_at,
                expected_head_hash=expected_head_hash,
            )
        else:
            raise LedgerOperationsError(f"unsupported mutation action: {args.action}")

        build = coordinator.rebuild_all()
        target_manifest = RuntimeFormatManifest(
            schema=manifest.schema,
            active_format=manifest.active_format,
            generation=manifest.generation + 1,
            updated_at=str(args.recorded_at),
            previous_format=manifest.previous_format,
            migration_id=manifest.migration_id,
            ledger_head=result.new_head,
        )
        _write_manifest(paths.runtime_manifest, target_manifest, manifest)
        ready = coordinator.require_ready_for_query()
        if ready.ledger_head != result.new_head or build.ledger_head != result.new_head:
            raise LedgerOperationsError("projection rebuild did not bind to the new ledger head")
    except (OSError, RuntimeError, TypeError, ValueError) as exc:
        try:
            current = store.verify().last_valid_hash or GENESIS_HASH
            store.restore_snapshot(snapshot.path, expected_head=current, dry_run=False)
            coordinator.rebuild_all()
            _atomic_write(paths.runtime_manifest, previous_manifest_bytes)
        except (OSError, RuntimeError, TypeError, ValueError) as rollback_exc:
            raise LedgerOperationsError(
                f"mutation failed and rollback also failed: {rollback_exc}"
            ) from exc
        raise

    return {
        **plan,
        "dry_run": False,
        "applied": True,
        "transaction_id": result.transaction_id,
        "sequence": result.sequence,
        "previous_head": result.previous_head,
        "new_head": result.new_head,
        "decision_ids": list(result.decision_ids),
        "result_claim_id": result.result_claim_id,
        "manifest_generation": target_manifest.generation,
        "projection_ready": True,
    }


def _run_read(args: argparse.Namespace, workspace: Path) -> dict[str, object]:
    adapter = _adapter(workspace)
    if adapter is None:
        raise LedgerCompatibilityError("ledger-v1 compatibility adapter was not enabled")
    if args.command == "status":
        return {"ok": True, **adapter.status()}
    if args.command == "query":
        return adapter.query(
            args.text,
            args.limit,
            recorded_as_of=args.recorded_as_of or None,
            observed_as_of=args.observed_as_of or None,
            valid_at=args.valid_at or None,
            realm_id=args.realm_id or None,
            scope=args.scope or None,
        )
    if args.command == "context":
        return adapter.context(
            args.text,
            args.limit,
            recorded_as_of=args.recorded_as_of or None,
            observed_as_of=args.observed_as_of or None,
            valid_at=args.valid_at or None,
            realm_id=args.realm_id or None,
            scope=args.scope or None,
        )
    if args.command == "explain":
        return adapter.explain(args.claim_id)
    raise LedgerOperationsError(f"unsupported read command: {args.command}")


def _run_rebuild(args: argparse.Namespace, workspace: Path) -> dict[str, object]:
    _, store, coordinator, manifest = _authorized_runtime(workspace)
    service = ProjectionRecoveryService(coordinator)
    if args.rebuild_command == "preview":
        return {"ok": True, "operation": "preview", **service.preview().to_dict()}
    if args.rebuild_command == "verify":
        verification = store.verify()
        status = coordinator.status()
        return {
            "ok": verification.valid and status.ready_for_query and status.ledger_head == manifest.ledger_head,
            "operation": "verify",
            "ledger_valid": verification.valid,
            "ledger_head": verification.last_valid_hash or GENESIS_HASH,
            "manifest_head": manifest.ledger_head,
            "projection_ready": status.ready_for_query,
            "reason_codes": list(status.reason_codes),
        }
    result = service.rebuild(
        str(args.expected_head),
        apply=bool(args.apply),
        confirmation=str(args.confirm or ""),
    )
    return {"ok": True, "operation": "apply" if args.apply else "dry-run", **result.to_dict()}


def _run_migration(args: argparse.Namespace, workspace: Path) -> dict[str, object]:
    if args.migrate_command in {"plan", "apply"}:
        source = Path(args.source_jsonl).expanduser().resolve()
        rows, prepared = _prepared(source, str(args.migration_id), str(args.recorded_at))
        if args.migrate_command == "plan":
            return {"ok": prepared.plan.rejected_count == 0, "dry_run": True, "plan": prepared.plan.to_dict()}
        targets = _backup_targets(args.backup_target, source)
        controller, _, _ = _migration_controller(workspace, backup_targets=targets)
        required = f"APPLY_LEDGER_V1:{prepared.plan.migration_id}:{prepared.plan.content_hash}"
        if args.apply and str(args.confirm or "") != required:
            raise LedgerOperationsError("exact migration confirmation token is required")
        apply_result = controller.apply(
            prepared,
            rows,
            updated_at=str(args.recorded_at),
            backup_id=str(args.backup_id),
            dry_run=not bool(args.apply),
        )
        return {
            "ok": True,
            "dry_run": apply_result.dry_run,
            "applied": apply_result.applied,
            "required_confirmation": required,
            "migration_id": apply_result.migration_id,
            "previous_manifest": apply_result.previous_manifest.to_dict(),
            "target_manifest": apply_result.target_manifest.to_dict(),
            "backup_path": str(apply_result.backup.path) if apply_result.backup is not None else None,
            "semantic_verification": (
                asdict(apply_result.semantic_verification)
                if apply_result.semantic_verification is not None
                else None
            ),
        }

    targets = _backup_targets(args.backup_target)
    if not targets:
        raise LedgerOperationsError("rollback requires at least one --backup-target NAME=PATH")
    controller, _, _ = _migration_controller(workspace, backup_targets=targets)
    backup = controller.verify_backup(Path(args.backup_path).expanduser().resolve())
    required = f"ROLLBACK_LEDGER_V1:{backup.backup_id}:{args.expected_head}"
    if args.apply and str(args.confirm or "") != required:
        raise LedgerOperationsError("exact rollback confirmation token is required")
    rollback_result = controller.rollback(
        backup.path,
        expected_head=str(args.expected_head),
        dry_run=not bool(args.apply),
    )
    return {
        "ok": True,
        "dry_run": rollback_result.dry_run,
        "applied": rollback_result.applied,
        "required_confirmation": required,
        "backup_id": rollback_result.backup_id,
        "previous_active_format": rollback_result.previous_active_format,
        "restored_manifest": rollback_result.restored_manifest.to_dict(),
        "restored_ledger_head": rollback_result.restored_ledger_head,
        "restored_sequence": rollback_result.restored_sequence,
    }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="ms8-memory-ledger", description="Guarded MS8 ledger-v1 operations")
    parser.add_argument("--workspace", required=True, help="explicit MS8 workspace root")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("doctor", help="read-only ledger integrity and projection health")
    sub.add_parser("status", help="show authorized ledger-v1 readiness")
    for name in ("query", "context"):
        command = sub.add_parser(name)
        command.add_argument("text")
        command.add_argument("--limit", type=int, default=5)
        command.add_argument("--recorded-as-of", default="")
        command.add_argument("--observed-as-of", default="")
        command.add_argument("--valid-at", default="")
        command.add_argument("--realm-id", default="")
        command.add_argument("--scope", default="")
    explain = sub.add_parser("explain")
    explain.add_argument("claim_id")

    rebuild = sub.add_parser("rebuild", help="preview, verify, or rebuild all disposable projections")
    rebuild_sub = rebuild.add_subparsers(dest="rebuild_command", required=True)
    rebuild_sub.add_parser("preview")
    rebuild_sub.add_parser("verify")
    rebuild_apply = rebuild_sub.add_parser("apply")
    rebuild_apply.add_argument("--expected-head", required=True)
    rebuild_apply.add_argument("--apply", action="store_true", help="perform rebuild; otherwise dry-run")
    rebuild_apply.add_argument("--confirm", default="")

    mutate = sub.add_parser("mutate", help="append an explicit lifecycle decision")
    mutate.add_argument(
        "action",
        choices=["correct", "supersede", "revoke", "forget", "expire", "resolve-conflict"],
    )
    mutate.add_argument("--claim-id", default="")
    mutate.add_argument("--replacement-json", default="")
    mutate.add_argument("--conflict-id", default="")
    mutate.add_argument("--winning-claim-id", default="")
    mutate.add_argument("--claim-ids", action="append", default=[])
    mutate.add_argument("--expected-head", required=True)
    mutate.add_argument("--recorded-at", required=True)
    mutate.add_argument("--reason", required=True)
    mutate.add_argument("--actor-kind", default="user", choices=["user", "reviewer", "mcp_client", "system"])
    mutate.add_argument("--actor-id", default="cli")
    mutate.add_argument("--apply", action="store_true", help="append and rebuild; otherwise dry-run")
    mutate.add_argument("--confirm", default="")

    migrate = sub.add_parser("migrate", help="plan/apply/rollback a production authority migration")
    migrate_sub = migrate.add_subparsers(dest="migrate_command", required=True)
    for name in ("plan", "apply"):
        command = migrate_sub.add_parser(name)
        command.add_argument("--source-jsonl", required=True)
        command.add_argument("--migration-id", required=True)
        command.add_argument("--recorded-at", required=True)
        if name == "apply":
            command.add_argument("--backup-id", required=True)
            command.add_argument("--backup-target", action="append", default=[])
            command.add_argument("--apply", action="store_true", help="perform migration; otherwise dry-run")
            command.add_argument("--confirm", default="")
    rollback = migrate_sub.add_parser("rollback")
    rollback.add_argument("--backup-path", required=True)
    rollback.add_argument("--backup-target", action="append", default=[])
    rollback.add_argument("--expected-head", required=True)
    rollback.add_argument("--apply", action="store_true", help="perform rollback; otherwise dry-run")
    rollback.add_argument("--confirm", default="")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        workspace = _workspace(args.workspace)
        if args.command == "doctor":
            output = _doctor(workspace)
        elif args.command in {"status", "query", "context", "explain"}:
            output = _run_read(args, workspace)
        elif args.command == "rebuild":
            output = _run_rebuild(args, workspace)
        elif args.command == "mutate":
            output = _apply_mutation(args, workspace)
        elif args.command == "migrate":
            output = _run_migration(args, workspace)
        else:
            raise LedgerOperationsError(f"unsupported command: {args.command}")
    except (
        HeadMismatchError,
        LedgerCompatibilityError,
        LedgerIntegrityError,
        LedgerOperationsError,
        LifecycleMutationError,
        ProductionMigrationError,
        ProjectionRecoveryError,
        RuntimeFormatManifestError,
        OSError,
        TypeError,
        ValueError,
    ) as exc:
        output = {"ok": False, "error": str(exc), "error_type": type(exc).__name__}
    print(json.dumps(output, ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if bool(output.get("ok", False)) else 1


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = ["LedgerOperationsError", "main"]
