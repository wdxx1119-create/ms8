# Memory Module Map (Low-Risk Logical Taxonomy)

This file defines a logical module taxonomy for the `memory` domain without changing any physical file paths.

## Top-Level Principle
- Keep three macro domains unchanged: `memory`, `security`, `connect`.
- Apply only logical grouping inside `memory`.
- Do not move `.py` files in this phase.
- Existing imports and runtime paths remain fully compatible.

## Memory Subdomains

### 1) engine
Core runtime and storage orchestration.
- `core.py`
- `config.py`
- `file_store.py`
- `sqlite_store.py`
- `memory_blocks.py`
- `working_memory.py`
- `utils.py`

### 2) ingestion
Input capture, admission, classification, extraction.
- `auto_memory.py`
- `priority_engine.py`
- `memory_section_parser.py`
- `app/pipeline/*`
- `app/rules/*`
- `app/classifier/*`
- `app/extractors/*`

### 3) retrieval
Search, ranking, context assembly.
- `whoosh_search.py`
- `semantic_search.py`
- `context_material.py`
- `context_understanding.py`
- `app/memory/search.py`
- `app/memory/indexer.py`

### 4) knowledge
Knowledge graph, relation logic, synthesis.
- `knowledge_graph.py`
- `knowledge_rules.py`
- `knowledge_arbitration.py`
- `knowledge_feedback.py`
- `synthetic_memory.py`

### 5) governance
Learning loop, quality governance, self-adjustment.
- `governance.py`
- `learning.py`
- `self_improvement.py`
- `enhanced_self_improvement.py`
- `pattern_recognition.py`
- `meta_cognition.py`

### 6) ops
Operations, health, maintenance, safeguards.
- `monitoring.py`
- `metrics_contract.py`
- `maintenance_manager.py`
- `maintenance_policy.py`
- `file_write_guard.py`

### 7) extensions
Optional or integration-oriented capabilities.
- `local_llm.py`
- `git_utils.py`
- `subagents.py`
- `enhanced_subagents.py`
- `skills.py`
- `built_in_skills.py`
- `agent_skills_standard.py`
- `skill_github_discovery.py`
- `skill_marketplace.py`
- `skill_search_index.py`

### 8) tests
All test modules.
- `tests/*`
- related `app` test modules

## Data Layout (already applied)
Runtime data is organized under workspace memory directories:
- `memory/daily`
- `memory/state`
- `memory/logs`
- `memory/reports`
- `memory/db`
- `memory/index`
- `memory/agents`

Compatibility is preserved through symlinks at legacy paths.

## Migration Policy
- Phase 1 (current): logical taxonomy only (this file + comments).
- Phase 2: selective physical migration behind compatibility exports.
- Phase 3: remove compatibility layer only after full regression pass.
