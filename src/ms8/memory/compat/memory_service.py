"""Opt-in, read-only compatibility adapter for ledger-v1 query/context surfaces.

The adapter never enables ledger-v1 by itself. Construction requires all three gates:

1. ``memory_ledger_v1.enabled`` in the supplied configuration;
2. an authoritative runtime-format manifest selecting ``ledger-v1``;
3. the existing ``MS8_MEMORY_LEDGER_V1`` environment flag.

When explicitly selected, failures are fail-closed and must not fall back to legacy
reads or writes. Existing legacy routes remain unchanged when the adapter is absent.
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ..application.conflicts import describe_recorded_conflict
from ..application.projection_service import ProjectionCoordinator
from ..application.replay import replay_transactions
from ..application.retrieval_context import ContextAssembler, RetrievalEngine, RetrievalHit, RetrievalRequest
from ..infrastructure.fts_projection import FtsProjectionAdapter
from ..infrastructure.graph_projection import GraphProjectionAdapter
from ..infrastructure.jsonl_ledger import JsonlRecordStore
from ..infrastructure.search_projection import SearchProjectionAdapter
from ..infrastructure.sqlite_projection_adapter import SQLiteProjectionAdapter
from ..infrastructure.vector_projection import VectorProjectionAdapter
from ..retrieval import (
    HYBRID_RETRIEVAL_ENV_FLAG,
    HYBRID_RETRIEVAL_PROFILE,
    HybridRetrievalRuntime,
    HybridRuntimeConfig,
    HybridRuntimePaths,
)
from ..runtime_format import (
    LEDGER_V1_RUNTIME_FORMAT,
    evaluate_runtime_format,
    load_runtime_format_manifest,
)


class LedgerCompatibilityError(RuntimeError):
    """Raised when an explicitly requested ledger compatibility route is unsafe."""


@dataclass(frozen=True, slots=True)
class LedgerCompatibilityPaths:
    runtime_manifest: Path
    ledger_root: Path
    sqlite_projection: Path
    search_projection: Path
    graph_projection: Path
    fts_projection: Path | None = None
    vector_projection: Path | None = None
    embedding_projection: Path | None = None


@dataclass(slots=True)
class LedgerMemoryCompatibilityAdapter:
    """Expose ledger retrieval/context with legacy-compatible primary response fields."""

    retrieval_engine: RetrievalEngine
    context_assembler: ContextAssembler
    paths: LedgerCompatibilityPaths
    manifest_generation: int
    migration_id: str
    ledger_head: str
    hybrid_runtime: HybridRetrievalRuntime | None = None
    retrieval_profile: str = "legacy"

    @staticmethod
    def _legacy_row(hit: RetrievalHit) -> dict[str, object]:
        return {
            "id": hit.claim_id,
            "text": hit.text,
            "normalized_text": hit.text,
            "category": hit.predicate,
            "status": hit.current_status,
            "source": hit.source_event_id,
            "score": hit.score,
            "scope": hit.scope,
            "realm_id": hit.realm_id,
            "authority": hit.authority,
            "sensitivity": hit.sensitivity,
            "confidence": hit.confidence,
            "can_recall": hit.can_recall,
            "can_inject": hit.can_inject,
            "can_act_on": hit.can_act_on,
            "provenance": {
                "claim_id": hit.claim_id,
                "source_event_id": hit.source_event_id,
                "evidence_ids": list(hit.evidence_ids),
                "decision_ids": list(hit.decision_ids),
                "conflict_ids": list(hit.conflict_ids),
            },
            "conflicts": [dict(item) for item in hit.conflicts],
            "ranking_explanation": list(hit.ranking_explanation),
            "matched_terms": list(hit.matched_terms),
        }

    def _request(
        self,
        text: str,
        *,
        limit: int,
        recorded_as_of: str | None = None,
        observed_as_of: str | None = None,
        valid_at: str | None = None,
        realm_id: str | None = None,
        scope: str | None = None,
    ) -> RetrievalRequest:
        return RetrievalRequest(
            text=text,
            limit=max(1, int(limit)),
            recorded_as_of=recorded_as_of,
            observed_as_of=observed_as_of,
            valid_at=valid_at,
            realm_id=realm_id,
            scope=scope,
        )

    def _trace(self, result: Any) -> dict[str, object]:
        return {
            "provider": "ledger-v1",
            "candidate_source": result.candidate_source,
            "retrieval_profile": self.retrieval_profile,
            "ledger_head": result.ledger_head,
            "last_sequence": result.last_sequence,
            "manifest_generation": self.manifest_generation,
            "migration_id": self.migration_id,
            "policy_filter": dict(result.policy_trace),
        }

    def _decorate_hybrid(self, out: dict[str, Any]) -> dict[str, Any]:
        gateway = out.get("retrieval_gateway")
        if isinstance(gateway, dict):
            gateway["manifest_generation"] = self.manifest_generation
            gateway["migration_id"] = self.migration_id
        return out

    def query(
        self,
        text: str,
        top_k: int = 5,
        *,
        purpose: str = "recall",
        explain: bool = False,
        recorded_as_of: str | None = None,
        observed_as_of: str | None = None,
        valid_at: str | None = None,
        realm_id: str | None = None,
        scope: str | None = None,
    ) -> dict[str, Any]:
        query = str(text or "").strip()
        if self.retrieval_profile == HYBRID_RETRIEVAL_PROFILE:
            if self.hybrid_runtime is None:
                raise LedgerCompatibilityError("hybrid-v1 runtime is not available")
            return self._decorate_hybrid(
                self.hybrid_runtime.query(
                    query,
                    top_k,
                    purpose=purpose,
                    explain=explain,
                    recorded_as_of=recorded_as_of,
                    observed_as_of=observed_as_of,
                    valid_at=valid_at,
                    realm_id=realm_id,
                    scope=scope,
                )
            )
        result = self.retrieval_engine.retrieve(
            self._request(
                query,
                limit=top_k,
                recorded_as_of=recorded_as_of,
                observed_as_of=observed_as_of,
                valid_at=valid_at,
                realm_id=realm_id,
                scope=scope,
            )
        )
        rows = [self._legacy_row(hit) for hit in result.hits]
        return {
            "ok": True,
            "query": query,
            "count": len(rows),
            "results": rows,
            "retrieval_gateway": self._trace(result),
            "ledger_v1": result.to_dict(),
        }

    def context(
        self,
        text: str,
        limit: int = 5,
        *,
        explain: bool = False,
        recorded_as_of: str | None = None,
        observed_as_of: str | None = None,
        valid_at: str | None = None,
        realm_id: str | None = None,
        scope: str | None = None,
    ) -> dict[str, Any]:
        query = str(text or "").strip()
        if self.retrieval_profile == HYBRID_RETRIEVAL_PROFILE:
            if self.hybrid_runtime is None:
                raise LedgerCompatibilityError("hybrid-v1 runtime is not available")
            return self._decorate_hybrid(
                self.hybrid_runtime.context(
                    query,
                    limit,
                    explain=explain,
                    recorded_as_of=recorded_as_of,
                    observed_as_of=observed_as_of,
                    valid_at=valid_at,
                    realm_id=realm_id,
                    scope=scope,
                )
            )
        retrieval = self.retrieval_engine.retrieve(
            self._request(
                query,
                limit=limit,
                recorded_as_of=recorded_as_of,
                observed_as_of=observed_as_of,
                valid_at=valid_at,
                realm_id=realm_id,
                scope=scope,
            )
        )
        assembled = self.context_assembler.assemble(retrieval)
        selected_ids = {item.claim_id for item in assembled.selected}
        memories = [self._legacy_row(hit) for hit in retrieval.hits if hit.claim_id in selected_ids]
        trace = self._trace(retrieval)
        context_payload = {
            "context": assembled.context,
            "memory_context": assembled.context,
            "memories": memories,
            "citations": list(assembled.citations),
            "conflict_warnings": list(assembled.conflict_warnings),
            "skipped_reasons": dict(assembled.skipped_reasons),
            "retrieval_gateway": trace,
        }
        return {
            "ok": True,
            "query": query,
            "context": context_payload,
            "retrieval_gateway": trace,
            "expression_mode": {},
            "system_prompt_extra": "",
            "context_with_expression": assembled.context,
            "recommended_actions": [],
            "ledger_v1": {
                "retrieval": retrieval.to_dict(),
                "assembly": assembled.to_dict(),
            },
        }

    def explain(self, claim_id: str) -> dict[str, Any]:
        normalized = str(claim_id or "").strip()
        self.retrieval_engine.projection_coordinator.require_ready_for_query()
        state = replay_transactions(self.retrieval_engine.record_store.iterate())
        view = state.claims.get(normalized)
        if view is None:
            return {
                "ok": False,
                "status": "not_found",
                "reason": "claim_not_found",
                "claim_id": normalized,
            }
        governance = {"can_recall": True, "can_inject": False, "can_act_on": False}
        for decision_id in view.decision_ids:
            decision = state.decisions.get(decision_id)
            configured = decision.policy.get("governance") if decision is not None else None
            if not isinstance(configured, Mapping):
                continue
            for field_name in tuple(governance):
                value = configured.get(field_name)
                if isinstance(value, bool):
                    governance[field_name] = value
        if not governance["can_recall"]:
            return {
                "ok": False,
                "status": "forbidden",
                "reason": "recall_not_allowed",
                "claim_id": normalized,
            }
        source_event = state.memory_events.get(view.claim.created_from_event_id)
        evidence = [
            item.to_dict()
            for _, item in sorted(state.evidence.items())
            if item.claim_id == normalized
        ]
        decisions = [
            state.decisions[decision_id].to_dict()
            for decision_id in view.decision_ids
            if decision_id in state.decisions
        ]
        conflicts = []
        for conflict_id, payload in sorted(state.conflicts.items()):
            raw_ids = payload.get("claim_ids", ())
            if isinstance(raw_ids, (list, tuple)) and normalized in {str(value) for value in raw_ids}:
                conflicts.append(describe_recorded_conflict(state, conflict_id))
        return {
            "ok": True,
            "provider": "ledger-v1",
            "read_only": True,
            "claim_id": normalized,
            "ledger_head": state.ledger_head,
            "last_sequence": state.last_sequence,
            "manifest_generation": self.manifest_generation,
            "migration_id": self.migration_id,
            "claim": view.claim.to_dict(),
            "current_status": view.current_status,
            "governance": governance,
            "source_event": source_event.to_dict() if source_event is not None else None,
            "evidence": evidence,
            "decisions": decisions,
            "conflicts": conflicts,
        }

    def status(self) -> dict[str, object]:
        readiness = self.retrieval_engine.projection_coordinator.status()
        return {
            "provider": "ledger-v1",
            "read_only": True,
            "retrieval_profile": self.retrieval_profile,
            "hybrid_ready": self.hybrid_runtime is not None,
            "manifest_generation": self.manifest_generation,
            "migration_id": self.migration_id,
            "ledger_head": self.ledger_head,
            "ready_for_query": readiness.ready_for_query,
            "reason_codes": list(readiness.reason_codes),
            "projection_names": [item.name for item in readiness.freshness],
        }


def _configured_path(workspace: Path, config: Mapping[str, Any], key: str, default: str) -> Path:
    raw = config.get(key, default)
    candidate = Path(str(raw)).expanduser()
    if not candidate.is_absolute():
        candidate = workspace / candidate
    return candidate.resolve()


def _enabled_flag(value: object) -> bool:
    return str(value or "").strip().casefold() in {"1", "true", "yes", "on"}


def build_ledger_memory_compatibility_adapter(
    config: Mapping[str, Any] | None,
    workspace: Path,
    *,
    environ: Mapping[str, str] | None = None,
) -> LedgerMemoryCompatibilityAdapter | None:
    """Build the adapter only when config, manifest, and environment all authorize it."""

    root = config if isinstance(config, Mapping) else {}
    raw_section = root.get("memory_ledger_v1", {})
    section = raw_section if isinstance(raw_section, Mapping) else {}
    if section.get("enabled") is not True:
        return None

    retrieval_profile = str(section.get("retrieval_profile") or "legacy").strip().casefold()
    if retrieval_profile not in {"legacy", HYBRID_RETRIEVAL_PROFILE}:
        raise LedgerCompatibilityError(
            f"unsupported ledger-v1 retrieval profile: {retrieval_profile or '<empty>'}"
        )
    environment = environ if environ is not None else os.environ
    if retrieval_profile == HYBRID_RETRIEVAL_PROFILE and not _enabled_flag(
        environment.get(HYBRID_RETRIEVAL_ENV_FLAG)
    ):
        raise LedgerCompatibilityError(
            f"hybrid-v1 retrieval profile requires {HYBRID_RETRIEVAL_ENV_FLAG}"
        )

    resolved_workspace = Path(workspace).expanduser().resolve()
    paths = LedgerCompatibilityPaths(
        runtime_manifest=_configured_path(
            resolved_workspace,
            section,
            "runtime_manifest",
            "memory/runtime-format.json",
        ),
        ledger_root=_configured_path(
            resolved_workspace,
            section,
            "ledger_root",
            "memory/ledger-v1",
        ),
        sqlite_projection=_configured_path(
            resolved_workspace,
            section,
            "sqlite_projection",
            "memory/projections/memory.sqlite3",
        ),
        search_projection=_configured_path(
            resolved_workspace,
            section,
            "search_projection",
            "memory/projections/search.json",
        ),
        graph_projection=_configured_path(
            resolved_workspace,
            section,
            "graph_projection",
            "memory/projections/graph.json",
        ),
        fts_projection=_configured_path(
            resolved_workspace,
            section,
            "fts_projection",
            "memory/projections/fts.json",
        ),
        vector_projection=_configured_path(
            resolved_workspace,
            section,
            "vector_projection",
            "memory/projections/vector.json",
        ),
        embedding_projection=_configured_path(
            resolved_workspace,
            section,
            "embedding_projection",
            "memory/projections/embedding.json",
        ),
    )

    manifest = load_runtime_format_manifest(paths.runtime_manifest)
    decision = evaluate_runtime_format(manifest, environment)
    if decision.selected_format != LEDGER_V1_RUNTIME_FORMAT or not decision.allowed:
        raise LedgerCompatibilityError(f"ledger-v1 compatibility route is not authorized: {decision.reason}")

    store = JsonlRecordStore(paths.ledger_root)
    verification = store.verify()
    if not verification.valid:
        raise LedgerCompatibilityError(
            "ledger-v1 compatibility route rejected an invalid ledger: "
            + ",".join(verification.reason_codes)
        )
    current_head = verification.last_valid_hash
    if current_head is None or current_head != manifest.ledger_head:
        raise LedgerCompatibilityError("ledger-v1 manifest head does not match the authoritative ledger")

    if paths.fts_projection is None or paths.vector_projection is None:
        raise LedgerCompatibilityError("ledger-v1 full projection paths are not configured")
    adapters = (
        SQLiteProjectionAdapter(paths.sqlite_projection),
        SearchProjectionAdapter(paths.search_projection),
        FtsProjectionAdapter(paths.fts_projection),
        VectorProjectionAdapter(paths.vector_projection),
        GraphProjectionAdapter(paths.graph_projection),
    )
    coordinator = ProjectionCoordinator(store, adapters)
    readiness = coordinator.require_ready_for_query()
    if readiness.ledger_head != manifest.ledger_head:
        raise LedgerCompatibilityError("ledger-v1 projection readiness head does not match the runtime manifest")

    token_budget_raw = section.get("context_token_budget", 1200)
    diversity_raw = section.get("max_per_subject_predicate", 2)
    if isinstance(token_budget_raw, bool) or not isinstance(token_budget_raw, int):
        raise LedgerCompatibilityError("context_token_budget must be an integer")
    if isinstance(diversity_raw, bool) or not isinstance(diversity_raw, int):
        raise LedgerCompatibilityError("max_per_subject_predicate must be an integer")

    engine = RetrievalEngine(
        record_store=store,
        projection_coordinator=coordinator,
        search_projection_path=paths.search_projection,
    )
    hybrid_runtime: HybridRetrievalRuntime | None = None
    if retrieval_profile == HYBRID_RETRIEVAL_PROFILE:
        if paths.embedding_projection is None:
            raise LedgerCompatibilityError("hybrid-v1 embedding projection path is not configured")
        raw_hybrid = section.get("hybrid", {})
        if not isinstance(raw_hybrid, Mapping):
            raise LedgerCompatibilityError("memory_ledger_v1.hybrid must be an object")
        hybrid_settings = dict(raw_hybrid)
        hybrid_settings.setdefault("context_budget_tokens", token_budget_raw)
        hybrid_settings.setdefault("max_per_subject_predicate", diversity_raw)
        try:
            hybrid_config = HybridRuntimeConfig.from_mapping(hybrid_settings)
            transactions = tuple(store.iterate())
            hybrid_runtime = HybridRetrievalRuntime(
                replay_transactions(transactions),
                HybridRuntimePaths(
                    search_projection=paths.search_projection,
                    graph_projection=paths.graph_projection,
                    embedding_projection=paths.embedding_projection,
                ),
                config=hybrid_config,
                transactions=transactions,
            )
        except (OSError, RuntimeError, TypeError, ValueError) as exc:
            raise LedgerCompatibilityError(
                f"hybrid-v1 retrieval profile configuration is invalid: {exc}"
            ) from exc
    return LedgerMemoryCompatibilityAdapter(
        retrieval_engine=engine,
        context_assembler=ContextAssembler(
            token_budget=token_budget_raw,
            max_per_subject_predicate=diversity_raw,
        ),
        paths=paths,
        manifest_generation=manifest.generation,
        migration_id=str(manifest.migration_id or ""),
        ledger_head=str(manifest.ledger_head or ""),
        hybrid_runtime=hybrid_runtime,
        retrieval_profile=retrieval_profile,
    )


__all__ = [
    "LedgerCompatibilityError",
    "LedgerCompatibilityPaths",
    "LedgerMemoryCompatibilityAdapter",
    "build_ledger_memory_compatibility_adapter",
]
