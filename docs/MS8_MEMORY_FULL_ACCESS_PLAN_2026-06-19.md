# MS8 Memory Full Access Plan

Date: `2026-06-19`
Branch: `codex/ms8-memory-full-access-plan`
Status: `draft`

## Background

Current MS8 already has a non-trivial main memory path:

- `runtime.write_memory`
- `runtime.read_memories`
- `runtime.search_memories`
- `runtime.search_memories_detailed`
- `ask` as a lightweight save/search entry
- `graph_*` runtime functions
- `review_*` and threshold workflows
- MCP-side `memory_service_interface.profile("recent")`

The gap is not "memory capability missing from zero". The gap is that MS8 does not yet provide a stable, high-level "full memory access" workflow for agent or operator use on top of those existing capabilities.

Observed gaps:

- `ms8 ask` is optimized for lightweight recall, not for complete browsing or precise inspection.
- There is no stable first-class command for listing memories by time/source/category/status.
- There is no first-class command for fetching one memory by `id`.
- In practice, confirming writes may require opening the underlying JSONL file directly.
- Graph capabilities are not a substitute for memory inspection, and may be unavailable in some runtimes.

This makes MCP-side memory operations incomplete: we can write memory, and we do have partial read paths, but we cannot yet reliably inspect the full memory surface in a structured, first-class, operator-friendly way.

## Planning Constraint

This planning branch is documentation-only.

- We may read existing MS8 source to understand the current capability surface.
- We must not modify MS8 source code in this planning phase.
- The design must avoid re-implementing functionality that already exists in runtime, graph, review, or MCP profile surfaces.

## Goal

Design a minimal but solid MS8 memory inspection surface that supports:

1. Listing recent memories in a stable format.
2. Filtering memories by key fields such as `source`, `status`, `category`, and text query.
3. Getting a single memory by `id`.
4. Providing a clear CLI surface that can later be exposed cleanly to MCP and LAN-controlled workflows.

This is not a "memory database redesign". It is a narrow access layer over existing persisted memory records and existing runtime capabilities.

## Non-Goals

- No new long-term storage engine.
- No speculative UI/dashboard redesign.
- No broad graph redesign.
- No automatic relation authoring in this phase.
- No arbitrary remote shell exposure through LAN.
- No duplicate memory pipeline parallel to existing runtime/engine storage.
- No source modification during this planning phase.

## Target User Stories

1. As an operator, I can run `ms8 memory list --limit 50` and see recent persisted memories.
2. As an agent, I can inspect one memory by `id` without reading JSONL files directly.
3. As a maintainer, I can filter by `source` to confirm whether a specific workflow already wrote a summary or plan.
4. As a debugging tool, I can search by text and quickly validate whether a memory was actually stored in the current runtime.
5. As a future MCP caller, I can depend on a stable machine-readable output contract.

## Expected Features

### P0: Core Read Surface

- `ms8 memory list`
- `ms8 memory get --id <id>`
- `ms8 memory search <query>`

Expected flags:

- `--limit`
- `--source`
- `--status`
- `--category`
- `--format json|table`

Expected behavior:

- Defaults to current runtime only.
- Must be defined as a thin facade over current runtime-backed memory data.
- Must prefer existing runtime read/search behavior where it is already sufficient.
- Returns stable structured fields, at minimum:
  - `id`
  - `text`
  - `source`
  - `status`
  - `category`
  - `created_at`
  - `scope`
  - `authority`

### P1: Operator Filtering and Verification

- `ms8 memory list --source codex:windows_mainline_summary`
- `ms8 memory list --source codex:windows_next_week_plan`
- `ms8 memory search "Windows 适配主线"`
- `ms8 memory get --id <id>`

Expected behavior:

- Supports stable verification after write.
- Avoids requiring direct file inspection for common workflows.
- Machine-readable JSON output must be sufficient for MCP consumption.

### P2: MCP-Ready Integration Layer

- Ensure the command surface is narrow and stable enough to expose through MS8 MCP.
- Reuse or extend current MCP memory-facing surfaces where possible, especially the existing profile/recent behavior, instead of creating a second disconnected read model.
- If MCP has explicit tool registration for CLI-backed operations, add matching memory-read tools only after the CLI/runtime contract stabilizes.
- If MCP currently wraps CLI or runtime calls indirectly, align output with that wrapper instead of inventing a second schema.

## Proposed CLI Shape

```bash
ms8 memory list --limit 20
ms8 memory list --source codex:windows_mainline_summary --format json
ms8 memory list --status accepted --category general
ms8 memory get --id aaebd54f-c048-4928-a157-720d1b29e8f5
ms8 memory search "watch project_memory ask"
```

## Implementation Plan

### Step 1: Map Existing Read Surfaces First

Before proposing implementation changes, explicitly map what already exists:

- `runtime.read_memories`
- `runtime.search_memories`
- `runtime.search_memories_detailed`
- `ask`
- `graph_search_entities_runtime`
- `review_list_runtime`
- MCP `profile("recent")`

Output of this step:

- A gap table of "already available", "available but not operator-friendly", and "missing".
- A decision on the thinnest viable exposure layer.

### Step 2: Establish a Thin Read Facade

If gaps remain after Step 1, add or specify a small read facade that exposes:

- `list_memories(...)`
- `get_memory_by_id(...)`
- `search_memories(...)`

Requirements:

- Reuse current runtime path resolution.
- Prefer existing runtime methods over direct file parsing when they already expose the required data.
- Use direct file parsing only for capability gaps such as exact-id lookup if no existing runtime method provides it cleanly.
- Do not duplicate runtime boot logic unnecessarily.
- Keep the read layer independent from graph code.
- Keep it independent from review and graph internals except where those existing outputs are intentionally reused.
- Prefer light parsing over broad `engine_core` imports.

### Step 3: Add CLI Commands

Extend CLI with a new top-level `memory` command group:

- `memory list`
- `memory get`
- `memory search`

Requirements:

- Match current CLI style and argument parsing conventions.
- Support both human-readable output and JSON output.
- Where possible, share output normalization with existing MCP/result normalization shapes.
- Return proper exit codes:
  - `0` for success
  - `1` for runtime/read failure
  - `2` for usage error or missing required args

### Step 4: Add Tests

Tests should cover:

- Listing recent records.
- Filtering by source.
- Looking up an existing id.
- Returning not-found for a missing id.
- Searching by text.
- JSON output shape stability.
- Runtime isolation: reading from current runtime, not a random default directory.
- Reuse-path validation: ensure existing runtime-backed paths are used where designed, rather than silently building a second storage interpretation layer.

### Step 5: Add MCP/Integration Readiness Notes

After CLI is stable:

- Document the command contract for MCP exposure.
- Identify which parts can directly reuse existing MCP service shapes.
- Decide whether MCP should expose:
  - `memory_list`
  - `memory_get`
  - `memory_search`
- Keep schemas aligned with CLI JSON output.

### Step 6: Optional Follow-up

Only after P0/P1 are stable:

- Add pagination or cursor support.
- Add date-range filters.
- Add source prefix filtering.
- Add audit-oriented fields or output presets.

These are explicitly follow-ups, not part of first delivery.

## Code Requirements

- Keep implementation narrow and lightweight.
- Do not redesign memory storage in this task.
- Do not introduce a new god object or a generic "data platform" abstraction.
- Do not couple memory read commands to graph availability.
- Do not duplicate existing runtime read/search functionality under a new abstraction unless there is a clearly documented gap.
- Prefer composition over replacement: wrap existing behavior first, extend only where required.
- Reuse existing runtime path helpers where possible.
- Match existing CLI and result-shape conventions.
- Prefer plain, inspectable Python over clever abstraction.
- Add tests for every new user-facing command path.

## MS8 Integration Requirements

- Must work with the canonical runtime under project-controlled `MS8_HOME`.
- Must not silently fall back to the wrong runtime when the caller has explicitly set runtime env.
- Must produce deterministic JSON output for MCP use.
- Must remain compatible with existing `ask`/runtime write behavior.
- Must not require graph health to be available.
- Must be safe to call from LAN/MCP wrappers later without exposing arbitrary filesystem access.
- Must account for existing MCP `profile("recent")` capability and avoid inventing a conflicting "recent memory" contract.

## Acceptance Criteria

The task is accepted when all of the following are true:

1. `ms8 memory list --limit 20` works in the canonical project runtime.
2. `ms8 memory get --id <known-id>` returns the expected stored memory.
3. `ms8 memory list --source <source>` can confirm a just-written memory without opening JSONL files.
4. `ms8 memory search <query>` can find a known stored summary/plan entry.
5. JSON output is stable and documented enough for MCP consumption.
6. The implementation adds tests covering success and not-found cases.
7. The new commands do not require graph availability.
8. The code path does not introduce broad new runtime/engine coupling.
9. The design clearly documents which existing MS8 capabilities are reused and which gaps require new surface area.
10. The implementation does not introduce a parallel memory-access stack that conflicts with current runtime/MCP behavior.

## Suggested Delivery Split

### Assistant Pass

- Implement the thin read layer.
- Start from the gap table against existing runtime/MCP/graph/review surfaces.
- Add `memory list/get/search` CLI commands.
- Add tests.
- Add a short user-facing doc snippet or help text.

### Review Pass

Codex review should focus on:

- Whether runtime resolution is correct.
- Whether the implementation reuses existing MS8 capability instead of rebuilding it.
- Whether output schema is stable enough for MCP.
- Whether the code stays narrow and avoids over-abstraction.
- Whether the tests cover the real operator use case: verify writes without file inspection.

### Merge Gate

Merge into `main` only after:

- CLI behavior is locally verified.
- Tests pass.
- The command is confirmed to work against project runtime data.
- Review confirms no accidental coupling or scope creep.

## Collaboration Assessment

This division of labor is good.

Why it works:

- The design and scope can be fixed first, which reduces drift.
- The assistant can implement against a concrete contract instead of guessing.
- Review can focus on correctness, simplicity, and architecture boundaries.
- We avoid premature merging of half-defined memory-access behavior into MS8 core.
- We explicitly force the implementation to respect existing MS8 runtime/MCP surfaces.

Main caution:

- The assistant should stay inside this narrow scope.
- The first version should solve "full memory access for inspection" only, not expand into memory governance, graph redesign, or dashboard redesign.

## Final Recommendation

Proceed with a narrow first delivery:

- First map and reuse existing runtime/MCP memory capabilities.
- Build `memory list/get/search`
- Verify against current runtime
- Keep JSON stable
- Review for coupling and simplicity

That gives MS8 an actual full-memory inspection surface without turning the task into another platform rewrite.
