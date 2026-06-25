# Local Windows Split Inventory

Date: `2026-06-25`
Source workspace: `/Users/s/Documents/New project`
Baseline: `origin/main` at `6e0025d`
Purpose: freeze the mixed local workspace state before separating macOS mainline and Windows development.

## Local Additions Not Present On `origin/main`

### 1. `project_memory` absorb submodule

Paths:

- `src/ms8/absorb/project_memory/`
- `docs/PROJECT_MEMORY.md`
- `tests/test_project_memory_cli.py`
- `tests/test_watch.py`

Observed local capability surface:

- project registration: `init`, `list`
- scan/index/build/submit/search/status/doctor/watch
- per-project service install/remove/status
- multi-project service install-all/remove-all/status-all
- build-state and watch-state persistence
- project summary submission into main memory through MS8 governance

### 2. Cross-platform service management surface

Paths:

- `src/ms8/service_platform.py`
- `src/ms8/service.py`

Observed local capability surface:

- service backend abstraction instead of macOS-only `launchd` helper
- Windows scheduler-oriented status/error normalization
- project-memory service orchestration
- batch service management for registered projects

### 3. Lightweight runtime health helper

Path:

- `src/ms8/runtime_health.py`

Observed local capability surface:

- standalone runtime-dir/bootstrap helper
- lightweight memory counting/backup/cleanup/recent-activity helpers

### 4. Watch / doctor / runtime health standardization

Paths:

- `src/ms8/watch.py`
- `src/ms8/doctor.py`
- `src/ms8/runtime.py`
- `src/ms8/engine_core/self_improvement.py`

Observed local capability surface:

- normalized self-check snapshot output in `watch`
- doctor follow-up actions (`watch next`, `watch also`)
- shadow runtime summary and propagation into governance health
- empty validation suite no longer reports success

### 5. Auto-memory session sync hardening

Path:

- `src/ms8/engine_core/auto_memory.py`

Observed local capability surface:

- cross-process session sync lock
- stale lock recovery
- checkpoint persistence
- richer sync summary payloads

### 6. `ask` integrated project-memory lookup

Path:

- `src/ms8/ask.py`

Observed local capability surface:

- `ms8 ask` can surface `project_memory` matches in addition to main memory and absorb chunk matches

## Local Drift That Must Not Be Treated As Windows Feature

These files diverged from `origin/main`, but the drift is not a Windows portability feature and should be reviewed separately before any carry-over:

- `src/ms8/connect/mcp_server/mcp_server.py`
- `src/ms8/connect/mcp_server/memory_service_interface.py`
- `src/ms8/connect/mcp_server/stdio_server.py`

Reason:

- `origin/main` already contains MCP full-memory read tools.
- The current mixed local workspace regressed that surface by removing or narrowing `memory_catalog`, `memory_list`, `memory_get`, and `memory_search`.
- Windows split work must start from clean `origin/main` and keep those already-published capabilities intact.

## Local Noise To Exclude From Any Commit / Migration

- `.ms8_home/`
- `src/ms8/absorb/project_memory/__pycache__/`

## Recommended Migration Set For Windows Workspace

Initial carry-over candidates from the mixed workspace into the new Windows workspace:

- `src/ms8/absorb/project_memory/`
- `docs/PROJECT_MEMORY.md`
- `src/ms8/runtime_health.py`
- `src/ms8/service_platform.py`
- `src/ms8/service.py`
- `src/ms8/watch.py`
- `src/ms8/doctor.py`
- `src/ms8/runtime.py`
- `src/ms8/engine_core/auto_memory.py`
- `src/ms8/engine_core/self_improvement.py`
- `src/ms8/ask.py`

Initial do-not-carry list:

- `.ms8_home/`
- `src/ms8/absorb/project_memory/__pycache__/`
- `src/ms8/connect/mcp_server/mcp_server.py`
- `src/ms8/connect/mcp_server/memory_service_interface.py`
- `src/ms8/connect/mcp_server/stdio_server.py`

## Working Rule After Split

- macOS mainline stays aligned to clean `origin/main`
- Windows development continues only in the new independent Windows workspace
- the current mixed workspace remains frozen as a reference snapshot until Windows migration is complete
