from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def replace_once(path: str, old: str, new: str) -> None:
    target = ROOT / path
    text = target.read_text(encoding="utf-8")
    count = text.count(old)
    if count != 1:
        raise RuntimeError(f"expected one match in {path}, found {count}: {old[:80]!r}")
    target.write_text(text.replace(old, new, 1), encoding="utf-8")


def replace_many(path: str, old: str, new: str, expected: int) -> None:
    target = ROOT / path
    text = target.read_text(encoding="utf-8")
    count = text.count(old)
    if count != expected:
        raise RuntimeError(f"expected {expected} matches in {path}, found {count}: {old[:80]!r}")
    target.write_text(text.replace(old, new), encoding="utf-8")


# Runtime typing and strict configuration validation.
replace_once(
    "src/ms8/memory/retrieval/runtime.py",
    "from .context_assembly import MMRConfig, build_agent_context\n",
    "from .candidate_sources import CandidateSource\nfrom .context_assembly import MMRConfig, build_agent_context\n",
)
replace_once(
    "src/ms8/memory/retrieval/runtime.py",
    "    RetrievalPurpose,\n    TimeCoordinates,\n",
    "    RetrievalPlan,\n    RetrievalPurpose,\n    TimeCoordinates,\n",
)
replace_once(
    "src/ms8/memory/retrieval/runtime.py",
    '''def _required_positive_int(value: object, field_name: str, default: int) -> int:\n    if value is None:\n        return default\n    if isinstance(value, bool) or not isinstance(value, int) or value < 1:\n        raise ValueError(f"{field_name} must be a positive integer")\n    return value\n''',
    '''def _required_positive_int(value: object, field_name: str, default: int) -> int:\n    candidate = default if value is None else value\n    if isinstance(candidate, bool) or not isinstance(candidate, int) or candidate < 1:\n        raise ValueError(f"{field_name} must be a positive integer")\n    return candidate\n\n\ndef _strict_bool(value: object, field_name: str, default: bool = False) -> bool:\n    candidate = default if value is None else value\n    if not isinstance(candidate, bool):\n        raise TypeError(f"{field_name} must be a boolean")\n    return candidate\n''',
)
replace_once(
    "src/ms8/memory/retrieval/runtime.py",
    '''    def retrieve(self, _plan: object, _eligible: EligibleClaims) -> tuple[CandidateHit, ...]:\n        raise RuntimeError("embedding provider is not configured")\n''',
    '''    def retrieve(\n        self,\n        _plan: RetrievalPlan,\n        _eligible: EligibleClaims,\n    ) -> tuple[CandidateHit, ...]:\n        raise RuntimeError("embedding provider is not configured")\n''',
)
replace_once(
    "src/ms8/memory/retrieval/runtime.py",
    "    def _vector_source(self) -> object:\n",
    "    def _vector_source(self) -> CandidateSource:\n",
)
replace_once(
    "src/ms8/memory/retrieval/runtime.py",
    '''            allow_remote=bool(embedding.get("allow_remote", False)),\n''',
    '''            allow_remote=_strict_bool(\n                embedding.get("allow_remote"),\n                "hybrid.embedding.allow_remote",\n            ),\n''',
)
replace_once(
    "src/ms8/memory/retrieval/runtime.py",
    "    def _sources(self) -> tuple[object, ...]:\n",
    "    def _sources(self) -> tuple[CandidateSource, ...]:\n",
)
replace_once(
    "src/ms8/memory/retrieval/runtime.py",
    '''        candidates = run_candidate_sources(\n            cast(Sequence[Any], self._sources()),\n            planning.plan,\n            eligibility.eligible,\n        )\n''',
    '''        candidates = run_candidate_sources(\n            self._sources(),\n            planning.plan,\n            eligibility.eligible,\n        )\n''',
)
replace_once(
    "src/ms8/memory/retrieval/runtime.py",
    '''            "fusion": {\n                "config_schema": execution.fusion.config_schema,\n                "candidate_count": execution.fusion.candidate_count,\n                "source_names": list(execution.fusion.source_names),\n                "ranked": [\n                    self._ranked_dict(item) for item in execution.fusion.ranked_claims\n                ],\n            },\n''',
    '''            "fusion": {\n                "config_schema": execution.fusion.config_schema,\n                "candidate_count": execution.fusion.candidate_count,\n                "source_names": list(execution.fusion.source_names),\n            },\n            "reranking": {\n                "ranked": [\n                    self._ranked_dict(item) for item in execution.fusion.ranked_claims\n                ],\n            },\n''',
)
replace_once(
    "src/ms8/memory/retrieval/runtime.py",
    "        skipped = Counter()\n",
    "        skipped: Counter[str] = Counter()\n",
)

# Compatibility adapter: explicit profile gate and hybrid runtime delegation.
replace_once(
    "src/ms8/memory/compat/memory_service.py",
    "from __future__ import annotations\n\nfrom collections.abc import Mapping\n",
    "from __future__ import annotations\n\nimport os\nfrom collections.abc import Mapping\n",
)
replace_once(
    "src/ms8/memory/compat/memory_service.py",
    '''from ..runtime_format import (\n    LEDGER_V1_RUNTIME_FORMAT,\n    evaluate_runtime_format,\n    load_runtime_format_manifest,\n)\n''',
    '''from ..retrieval import (\n    HYBRID_RETRIEVAL_ENV_FLAG,\n    HYBRID_RETRIEVAL_PROFILE,\n    HybridRetrievalRuntime,\n    HybridRuntimeConfig,\n    HybridRuntimePaths,\n)\nfrom ..runtime_format import (\n    LEDGER_V1_RUNTIME_FORMAT,\n    evaluate_runtime_format,\n    load_runtime_format_manifest,\n)\n''',
)
replace_once(
    "src/ms8/memory/compat/memory_service.py",
    '''    fts_projection: Path | None = None\n    vector_projection: Path | None = None\n''',
    '''    fts_projection: Path | None = None\n    vector_projection: Path | None = None\n    embedding_projection: Path | None = None\n''',
)
replace_once(
    "src/ms8/memory/compat/memory_service.py",
    '''    migration_id: str\n    ledger_head: str\n''',
    '''    migration_id: str\n    ledger_head: str\n    hybrid_runtime: HybridRetrievalRuntime | None = None\n    retrieval_profile: str = "legacy"\n''',
)
replace_once(
    "src/ms8/memory/compat/memory_service.py",
    '''    def _trace(self, result: Any) -> dict[str, object]:\n        return {\n            "provider": "ledger-v1",\n            "candidate_source": result.candidate_source,\n            "ledger_head": result.ledger_head,\n            "last_sequence": result.last_sequence,\n            "manifest_generation": self.manifest_generation,\n            "migration_id": self.migration_id,\n            "policy_filter": dict(result.policy_trace),\n        }\n''',
    '''    def _trace(self, result: Any) -> dict[str, object]:\n        return {\n            "provider": "ledger-v1",\n            "candidate_source": result.candidate_source,\n            "retrieval_profile": self.retrieval_profile,\n            "ledger_head": result.ledger_head,\n            "last_sequence": result.last_sequence,\n            "manifest_generation": self.manifest_generation,\n            "migration_id": self.migration_id,\n            "policy_filter": dict(result.policy_trace),\n        }\n\n    def _decorate_hybrid(self, out: dict[str, Any]) -> dict[str, Any]:\n        gateway = out.get("retrieval_gateway")\n        if isinstance(gateway, dict):\n            gateway["manifest_generation"] = self.manifest_generation\n            gateway["migration_id"] = self.migration_id\n        return out\n''',
)
replace_once(
    "src/ms8/memory/compat/memory_service.py",
    '''    def query(\n        self,\n        text: str,\n        top_k: int = 5,\n        *,\n        recorded_as_of: str | None = None,\n        observed_as_of: str | None = None,\n        valid_at: str | None = None,\n        realm_id: str | None = None,\n        scope: str | None = None,\n    ) -> dict[str, Any]:\n        query = str(text or "").strip()\n        result = self.retrieval_engine.retrieve(\n''',
    '''    def query(\n        self,\n        text: str,\n        top_k: int = 5,\n        *,\n        purpose: str = "recall",\n        explain: bool = False,\n        recorded_as_of: str | None = None,\n        observed_as_of: str | None = None,\n        valid_at: str | None = None,\n        realm_id: str | None = None,\n        scope: str | None = None,\n    ) -> dict[str, Any]:\n        query = str(text or "").strip()\n        if self.retrieval_profile == HYBRID_RETRIEVAL_PROFILE:\n            if self.hybrid_runtime is None:\n                raise LedgerCompatibilityError("hybrid-v1 runtime is not available")\n            return self._decorate_hybrid(\n                self.hybrid_runtime.query(\n                    query,\n                    top_k,\n                    purpose=purpose,\n                    explain=explain,\n                    recorded_as_of=recorded_as_of,\n                    observed_as_of=observed_as_of,\n                    valid_at=valid_at,\n                    realm_id=realm_id,\n                    scope=scope,\n                )\n            )\n        result = self.retrieval_engine.retrieve(\n''',
)
replace_once(
    "src/ms8/memory/compat/memory_service.py",
    '''    def context(\n        self,\n        text: str,\n        limit: int = 5,\n        *,\n        recorded_as_of: str | None = None,\n        observed_as_of: str | None = None,\n        valid_at: str | None = None,\n        realm_id: str | None = None,\n        scope: str | None = None,\n    ) -> dict[str, Any]:\n        query = str(text or "").strip()\n        retrieval = self.retrieval_engine.retrieve(\n''',
    '''    def context(\n        self,\n        text: str,\n        limit: int = 5,\n        *,\n        explain: bool = False,\n        recorded_as_of: str | None = None,\n        observed_as_of: str | None = None,\n        valid_at: str | None = None,\n        realm_id: str | None = None,\n        scope: str | None = None,\n    ) -> dict[str, Any]:\n        query = str(text or "").strip()\n        if self.retrieval_profile == HYBRID_RETRIEVAL_PROFILE:\n            if self.hybrid_runtime is None:\n                raise LedgerCompatibilityError("hybrid-v1 runtime is not available")\n            return self._decorate_hybrid(\n                self.hybrid_runtime.context(\n                    query,\n                    limit,\n                    explain=explain,\n                    recorded_as_of=recorded_as_of,\n                    observed_as_of=observed_as_of,\n                    valid_at=valid_at,\n                    realm_id=realm_id,\n                    scope=scope,\n                )\n            )\n        retrieval = self.retrieval_engine.retrieve(\n''',
)
replace_once(
    "src/ms8/memory/compat/memory_service.py",
    '''            "read_only": True,\n            "manifest_generation": self.manifest_generation,\n''',
    '''            "read_only": True,\n            "retrieval_profile": self.retrieval_profile,\n            "hybrid_ready": self.hybrid_runtime is not None,\n            "manifest_generation": self.manifest_generation,\n''',
)
replace_once(
    "src/ms8/memory/compat/memory_service.py",
    '''def _configured_path(workspace: Path, config: Mapping[str, Any], key: str, default: str) -> Path:\n    raw = config.get(key, default)\n    candidate = Path(str(raw)).expanduser()\n    if not candidate.is_absolute():\n        candidate = workspace / candidate\n    return candidate.resolve()\n''',
    '''def _configured_path(workspace: Path, config: Mapping[str, Any], key: str, default: str) -> Path:\n    raw = config.get(key, default)\n    candidate = Path(str(raw)).expanduser()\n    if not candidate.is_absolute():\n        candidate = workspace / candidate\n    return candidate.resolve()\n\n\ndef _enabled_flag(value: object) -> bool:\n    return str(value or "").strip().casefold() in {"1", "true", "yes", "on"}\n''',
)
replace_once(
    "src/ms8/memory/compat/memory_service.py",
    '''    if section.get("enabled") is not True:\n        return None\n\n    resolved_workspace = Path(workspace).expanduser().resolve()\n''',
    '''    if section.get("enabled") is not True:\n        return None\n\n    retrieval_profile = str(section.get("retrieval_profile") or "legacy").strip().casefold()\n    if retrieval_profile not in {"legacy", HYBRID_RETRIEVAL_PROFILE}:\n        raise LedgerCompatibilityError(\n            f"unsupported ledger-v1 retrieval profile: {retrieval_profile or '<empty>'}"\n        )\n    environment = environ if environ is not None else os.environ\n    if retrieval_profile == HYBRID_RETRIEVAL_PROFILE and not _enabled_flag(\n        environment.get(HYBRID_RETRIEVAL_ENV_FLAG)\n    ):\n        raise LedgerCompatibilityError(\n            f"hybrid-v1 retrieval profile requires {HYBRID_RETRIEVAL_ENV_FLAG}"\n        )\n\n    resolved_workspace = Path(workspace).expanduser().resolve()\n''',
)
replace_once(
    "src/ms8/memory/compat/memory_service.py",
    '''        vector_projection=_configured_path(\n            resolved_workspace,\n            section,\n            "vector_projection",\n            "memory/projections/vector.json",\n        ),\n    )\n\n    manifest = load_runtime_format_manifest(paths.runtime_manifest)\n    decision = evaluate_runtime_format(manifest, environ)\n''',
    '''        vector_projection=_configured_path(\n            resolved_workspace,\n            section,\n            "vector_projection",\n            "memory/projections/vector.json",\n        ),\n        embedding_projection=_configured_path(\n            resolved_workspace,\n            section,\n            "embedding_projection",\n            "memory/projections/embedding.json",\n        ),\n    )\n\n    manifest = load_runtime_format_manifest(paths.runtime_manifest)\n    decision = evaluate_runtime_format(manifest, environment)\n''',
)
replace_once(
    "src/ms8/memory/compat/memory_service.py",
    '''    engine = RetrievalEngine(\n        record_store=store,\n        projection_coordinator=coordinator,\n        search_projection_path=paths.search_projection,\n    )\n    return LedgerMemoryCompatibilityAdapter(\n''',
    '''    engine = RetrievalEngine(\n        record_store=store,\n        projection_coordinator=coordinator,\n        search_projection_path=paths.search_projection,\n    )\n    hybrid_runtime: HybridRetrievalRuntime | None = None\n    if retrieval_profile == HYBRID_RETRIEVAL_PROFILE:\n        if paths.embedding_projection is None:\n            raise LedgerCompatibilityError("hybrid-v1 embedding projection path is not configured")\n        raw_hybrid = section.get("hybrid", {})\n        if not isinstance(raw_hybrid, Mapping):\n            raise LedgerCompatibilityError("memory_ledger_v1.hybrid must be an object")\n        hybrid_settings = dict(raw_hybrid)\n        hybrid_settings.setdefault("context_budget_tokens", token_budget_raw)\n        hybrid_settings.setdefault("max_per_subject_predicate", diversity_raw)\n        try:\n            hybrid_config = HybridRuntimeConfig.from_mapping(hybrid_settings)\n            hybrid_runtime = HybridRetrievalRuntime(\n                replay_transactions(store.iterate()),\n                HybridRuntimePaths(\n                    search_projection=paths.search_projection,\n                    graph_projection=paths.graph_projection,\n                    embedding_projection=paths.embedding_projection,\n                ),\n                config=hybrid_config,\n            )\n        except (OSError, RuntimeError, TypeError, ValueError) as exc:\n            raise LedgerCompatibilityError(\n                f"hybrid-v1 retrieval profile configuration is invalid: {exc}"\n            ) from exc\n    return LedgerMemoryCompatibilityAdapter(\n''',
)
replace_once(
    "src/ms8/memory/compat/memory_service.py",
    '''        migration_id=str(manifest.migration_id or ""),\n        ledger_head=str(manifest.ledger_head or ""),\n    )\n''',
    '''        migration_id=str(manifest.migration_id or ""),\n        ledger_head=str(manifest.ledger_head or ""),\n        hybrid_runtime=hybrid_runtime,\n        retrieval_profile=retrieval_profile,\n    )\n''',
)

# Explicit CLI profile and explain controls.
replace_once(
    "src/ms8/cli.py",
    '''    p_memory_ledger.add_argument("--workspace", required=True, help="explicit MS8 workspace path")\n    p_memory_ledger_sub = p_memory_ledger.add_subparsers(dest="memory_ledger_cmd")\n''',
    '''    p_memory_ledger.add_argument("--workspace", required=True, help="explicit MS8 workspace path")\n    p_memory_ledger.add_argument(\n        "--retrieval-profile",\n        choices=["legacy", "hybrid-v1"],\n        default="legacy",\n        help="explicit Ledger-v1 retrieval profile; hybrid-v1 also requires its environment gate",\n    )\n    p_memory_ledger_sub = p_memory_ledger.add_subparsers(dest="memory_ledger_cmd")\n''',
)
replace_once(
    "src/ms8/cli.py",
    '''        _ledger_parser.add_argument("--realm-id", dest="realm_id", default="")\n        _ledger_parser.add_argument("--scope", default="")\n''',
    '''        _ledger_parser.add_argument("--realm-id", dest="realm_id", default="")\n        _ledger_parser.add_argument("--scope", default="")\n        _ledger_parser.add_argument(\n            "--explain",\n            action="store_true",\n            help="include the governed retrieval pipeline trace",\n        )\n        if _ledger_read_command == "query":\n            _ledger_parser.add_argument(\n                "--purpose",\n                choices=["recall", "historical", "review", "audit"],\n                default="recall",\n            )\n''',
)
replace_once(
    "src/ms8/memory/compat/cli.py",
    '''    config: dict[str, Any] = {"memory_ledger_v1": {"enabled": True}}\n''',
    '''    retrieval_profile = str(\n        getattr(args, "retrieval_profile", "legacy") or "legacy"\n    ).strip()\n    config: dict[str, Any] = {\n        "memory_ledger_v1": {\n            "enabled": True,\n            "retrieval_profile": retrieval_profile,\n        }\n    }\n''',
)
replace_once(
    "src/ms8/memory/compat/cli.py",
    '''                str(getattr(args, "text", "") or ""),\n                int(getattr(args, "limit", 5) or 5),\n                recorded_as_of=_optional_text(args, "recorded_as_of"),\n''',
    '''                str(getattr(args, "text", "") or ""),\n                int(getattr(args, "limit", 5) or 5),\n                purpose=str(getattr(args, "purpose", "recall") or "recall"),\n                explain=bool(getattr(args, "explain", False)),\n                recorded_as_of=_optional_text(args, "recorded_as_of"),\n''',
)
replace_once(
    "src/ms8/memory/compat/cli.py",
    '''                str(getattr(args, "text", "") or ""),\n                int(getattr(args, "limit", 5) or 5),\n                recorded_as_of=_optional_text(args, "recorded_as_of"),\n                observed_as_of=_optional_text(args, "observed_as_of"),\n                valid_at=_optional_text(args, "valid_at"),\n                realm_id=_optional_text(args, "realm_id"),\n                scope=_optional_text(args, "scope"),\n            )\n        elif command == "explain":\n''',
    '''                str(getattr(args, "text", "") or ""),\n                int(getattr(args, "limit", 5) or 5),\n                explain=bool(getattr(args, "explain", False)),\n                recorded_as_of=_optional_text(args, "recorded_as_of"),\n                observed_as_of=_optional_text(args, "observed_as_of"),\n                valid_at=_optional_text(args, "valid_at"),\n                realm_id=_optional_text(args, "realm_id"),\n                scope=_optional_text(args, "scope"),\n            )\n        elif command == "explain":\n''',
)

# MCP service and tool schema forwarding while preserving legacy response fields.
replace_once(
    "src/ms8/connect/mcp_server/memory_service_interface.py",
    '''        *,\n        recorded_as_of: str | None = None,\n        valid_at: str | None = None,\n        realm_id: str | None = None,\n        scope: str | None = None,\n    ) -> dict[str, Any]:\n''',
    '''        *,\n        purpose: str = "recall",\n        explain: bool = False,\n        recorded_as_of: str | None = None,\n        observed_as_of: str | None = None,\n        valid_at: str | None = None,\n        realm_id: str | None = None,\n        scope: str | None = None,\n    ) -> dict[str, Any]:\n''',
)
replace_once(
    "src/ms8/connect/mcp_server/memory_service_interface.py",
    '''                    query,\n                    top_k,\n                    recorded_as_of=recorded_as_of,\n                    valid_at=valid_at,\n''',
    '''                    query,\n                    top_k,\n                    purpose=purpose,\n                    explain=explain,\n                    recorded_as_of=recorded_as_of,\n                    observed_as_of=observed_as_of,\n                    valid_at=valid_at,\n''',
)
replace_once(
    "src/ms8/connect/mcp_server/memory_service_interface.py",
    '''        *,\n        recorded_as_of: str | None = None,\n        valid_at: str | None = None,\n        realm_id: str | None = None,\n        scope: str | None = None,\n    ) -> dict[str, Any]:\n        query = str(text or "").strip()\n        if self.ledger_requested:\n''',
    '''        *,\n        explain: bool = False,\n        recorded_as_of: str | None = None,\n        observed_as_of: str | None = None,\n        valid_at: str | None = None,\n        realm_id: str | None = None,\n        scope: str | None = None,\n    ) -> dict[str, Any]:\n        query = str(text or "").strip()\n        if self.ledger_requested:\n''',
)
replace_once(
    "src/ms8/connect/mcp_server/memory_service_interface.py",
    '''                    query,\n                    limit,\n                    recorded_as_of=recorded_as_of,\n                    valid_at=valid_at,\n''',
    '''                    query,\n                    limit,\n                    explain=explain,\n                    recorded_as_of=recorded_as_of,\n                    observed_as_of=observed_as_of,\n                    valid_at=valid_at,\n''',
)
replace_once(
    "src/ms8/connect/mcp_server/mcp_server.py",
    '''def _ledger_query_options(params: dict[str, Any]) -> dict[str, str]:\n    options: dict[str, str] = {}\n    for key in ("recorded_as_of", "observed_as_of", "valid_at", "realm_id", "scope"):\n        value = str(params.get(key) or "").strip()\n        if value:\n            options[key] = value\n    return options\n''',
    '''def _ledger_query_options(\n    params: dict[str, Any],\n    *,\n    include_purpose: bool = False,\n) -> dict[str, Any]:\n    options: dict[str, Any] = {}\n    for key in ("recorded_as_of", "observed_as_of", "valid_at", "realm_id", "scope"):\n        value = str(params.get(key) or "").strip()\n        if value:\n            options[key] = value\n    if _as_bool(params.get("explain", False)):\n        options["explain"] = True\n    if include_purpose:\n        purpose = str(params.get("purpose") or "").strip()\n        if purpose:\n            options["purpose"] = purpose\n    return options\n''',
)
replace_once(
    "src/ms8/connect/mcp_server/mcp_server.py",
    '''        options = _ledger_query_options(p)\n        out = svc.query(text, top_k, **options) if options else svc.query(text, top_k)\n''',
    '''        options = _ledger_query_options(p, include_purpose=True)\n        out = svc.query(text, top_k, **options) if options else svc.query(text, top_k)\n''',
)
replace_many(
    "src/ms8/connect/mcp_server/stdio_server.py",
    '''                "scope": {"type": "string"},\n''',
    '''                "scope": {"type": "string"},\n                "explain": {\n                    "type": "boolean",\n                    "default": False,\n                    "description": "Include the governed hybrid retrieval trace when selected.",\n                },\n''',
    3,
)
replace_once(
    "src/ms8/connect/mcp_server/stdio_server.py",
    '''                "top_k": {"type": "integer", "minimum": 1, "maximum": 50, "default": 5},\n                "recorded_as_of": {"type": "string", "description": "Recorded-time cutoff."},\n''',
    '''                "top_k": {"type": "integer", "minimum": 1, "maximum": 50, "default": 5},\n                "purpose": {\n                    "type": "string",\n                    "enum": ["recall", "historical", "review", "audit"],\n                    "default": "recall",\n                },\n                "recorded_as_of": {"type": "string", "description": "Recorded-time cutoff."},\n''',
)

print("Phase 8 integration patch applied")
