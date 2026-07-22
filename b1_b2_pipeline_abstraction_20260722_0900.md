# B-1 + B-2: Pipeline/Step Abstraction + Frontend Event Handling

**Date:** 2026-07-22  
**Route:** B-1 + B-2 from Vermes Harness Baseline Audit (revised 2026-07-22)  
**Commits:** 006a91727, c8ae4ae4a  
**Status:** ✅ Complete, pushed to origin/main

## Objective

Create a lightweight Pipeline/Step abstraction for multi-stage agent workflows (B-1) and enable frontend rendering of pipeline progress events (B-2), replacing hardcoded 6-stage loops in ScholarForge blueprint.

## B-1: Pipeline Abstraction (agent/pipeline.py, 230 lines)

### Design
- **Pipeline**: ordered sequence of Stages with SSE event emission
- **Stage**: dataclass with name, agent_cls, label, section_kwarg, depth_kwarg, post_hooks
- **PipelineConfig**: checkpoint flag, continue_from (resume from stage)
- Declarative: caller provides `make_agent` callback, pipeline manages iteration/events

### Features
- SSE-ready events: `stage_event` (start/done/error), `checkpoint_event`, `done_event`, `thinking_event`
- Checkpoint between stages (configurable, not after last)
- `continue_from` resume support
- Fail-open: stage errors emit error event but don't crash pipeline
- Client disconnect detection via `is_disconnected` callback
- Post-stage hooks (e.g., save_outline, save_section_content)
- Section/depth kwarg passthrough to agent.run()
- stage_labels override for checkpoint messages

### ScholarForge Migration
- Replaced hardcoded 6-stage loop (~130 lines) with Pipeline abstraction (~95 lines)
- SSE event format unchanged: stage start/done, checkpoint, done
- Post-stage hooks: `_outline_hook` (save outline to DB), `_writing_hook` (save full paper)
- continue_from context restoration preserved
- Citation replacement logic preserved after pipeline completion

### Tests (26 new, tests/agent/test_pipeline.py)
- Event helpers (5 tests)
- Stage dataclass (2 tests)
- Pipeline basics: stage_names, start_index, empty, invalid (5 tests)
- Pipeline run: basic 2-stage, checkpoint, no-checkpoint-after-last, continue_from, fail-open, disconnect, hooks, hook-failure, extra-kwargs, section/depth passthrough, papers-count, labels-override, empty-pipeline (14 tests)

## B-2: Frontend Event Handling

### chat-transport.js
- Added `stage` → `onStage` and `checkpoint` → `onCheckpoint` SSE event routing
- 6 new lines in event dispatch switch

### chat.js store
- `onStage`: tracks `pipelineStages` array on assistant message (start/done/error status)
- `onCheckpoint`: stores checkpoint data on assistant message
- Clear checkpoint on `onDone`
- Enables any pipeline-based agent to show stage progress in chat without additional UI work

### Frontend Build
- Vite build successful (486KB JS + 77KB CSS)
- Synced to hermes_cli/web_dist/

## Test Results
- **146 passed / 0 failed** (26 pipeline + 16 facade + 9 cross_session + 11 prune + 24 prune-c2 + 29 rag + 31 hybrid = 146)
- Zero regression on all existing tests

## Files Changed
- **New:** `agent/pipeline.py` (230 lines), `tests/agent/test_pipeline.py` (13KB, 26 tests)
- **Modified:** `hermes_cli/scholarforge/blueprint.py` (pipeline branch migrated), `frontend/src/services/chat-transport.js` (+6 lines), `frontend/src/stores/chat.js` (+35 lines), `vermes-backend.spec` (+1 hiddenimport), `vermes-gui.spec` (+1 hiddenimport)
- **Total:** 5 files changed, 731 insertions(+), 90 deletions(-) (B-1) + 9 files changed, 107 insertions(+), 71 deletions(-) (B-2)

## Key Decisions
- Pipeline is a thin orchestrator: doesn't own context or agent instantiation, delegates to caller-provided callbacks
- SSE event format kept identical to existing ScholarForge format for backward compatibility
- ScholarForge frontend UI not built (positioned as pure backend toolset); instead, pipeline events are handled generically in chat layer for any future pipeline-based agent
