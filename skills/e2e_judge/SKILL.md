---
name: klayoutclaw:e2e-judge
description: Run agentic E2E tests for KlayoutClaw. Gives Claude Code natural-language tasks, lets it autonomously choose MCP tools, independently verifies layout state via direct MCP calls, and judges outcomes with gpt-5-mini. Use when the user wants to run agentic tests, validate KlayoutClaw, or benchmark agent performance.
---

# Agentic E2E Test for KlayoutClaw

End-to-end agentic test suite. Each test gives Claude Code a natural-language task (not a hardcoded command), lets the agent autonomously decide which MCP tools to call, then independently verifies the layout state and judges the outcome with an LLM.

## Prerequisites

- KLayout running with KlayoutClaw plugin (`KLAYOUT_MCP_URL`, default `http://127.0.0.1:8765/mcp`)
- `claude` CLI on PATH (Claude Code)
- Network access to `http://api.physcai.com` (LLM judge)

## Architecture

```
Per test:
1. [Optional] Reset layout via direct MCP call
2. run_agent(task) → claude --print --mcp-config → AgentResult(transcript)
3. run_verification(fn, client) → VerificationResult(checks, passed)
4. Compose context = transcript + verification
5. judge(context, assertion) → JudgeVerdict(passed, reasoning, confidence)
```

| File | Role |
|------|------|
| `harness.py` | Invokes `claude --print --mcp-config` with NL prompts |
| `verifier.py` | Independent MCP state checks (cells, shapes, layers, files) |
| `verifier_phase5.py` | Phase 5 verification functions (19 functions for pipeline/eval tests) |
| `judge.py` | LLM judge — gpt-5-mini via api.physcai.com |
| `conftest.py` | AgenticTestCase/Result dataclasses, formatting |
| `run_tests.py` | 15 agentic tests across 6 groups (Phase 1-4) |
| `run_tests_phase5.py` | 26 agentic tests across 9 groups (Phase 5: autonomous pipeline) |

## Scripts

```bash
# Phase 1-4 tests (15 tests)
python scripts/run_tests.py                  # run all 15 tests
python scripts/run_tests.py --test T2.1      # single test
python scripts/run_tests.py --group recon    # one group
python scripts/run_tests.py --json           # JSON output
python scripts/run_tests.py --list           # list tests

# Phase 5 tests (26 tests)
python scripts/run_tests_phase5.py                        # run all 26 tests
python scripts/run_tests_phase5.py --test T0.1            # single test
python scripts/run_tests_phase5.py --group preflight      # one group
python scripts/run_tests_phase5.py --json                 # JSON output
python scripts/run_tests_phase5.py --list                 # list tests
python scripts/run_tests_phase5.py --nightly              # include optional nightly tests (T10.2)
```

## Test Groups (41 tests total)

### Phase 1-4 (15 tests via run_tests.py)

| Group | Tests | What it tests |
|-------|-------|---------------|
| recon | T0.1-T0.2 | Agent explores tools, inspects layout state |
| layout | T1.1-T1.3 | Agent creates layouts and cells from NL specs |
| geometry | T2.1-T2.3 | Agent draws rectangles, polygons, paths from descriptions |
| compose | T3.1-T3.3 | Agent performs multi-step: create cell + instance, multi-layer, query + modify |
| export | T4.1-T4.2 | Agent saves GDS and takes screenshots |
| device | T5.1-T5.2 | Agent designs complete Hall bar from high-level spec |

### Phase 5 (26 tests via run_tests_phase5.py)

| Group | Tests | What it tests |
|-------|-------|---------------|
| preflight | T0.1-T0.3 | Tool/skill discovery triad, skill doc reading, validate_pixel_size |
| inspection | T1.1-T1.3 | Per-layer shape audit, pixel gate valid/invalid handling |
| evaluate | T2.1-T2.3 | evaluate_design DRC mode, score mode, graceful error on missing ref |
| hallbar | T3.1-T3.3 | Hall bar full flow on synthetic flake, topgate-off variant, deliverables package |
| pipeline | T4.1-T4.3 | Pipeline step ordering (QUERY+VALIDATE only), auto_route layer strings, array layer map |
| discovery | T6.1-T6.3 | Skill inventory, hallbar Step 0 checklist, E2E gate plan extraction |
| layout_deep | T7.1-T7.3 | Per-layer metrics, recursive cell hierarchy, text+polygon semantics |
| evaluate_adv | T9.1-T9.3 | DRC advanced analysis, score mode advanced, dual-mode consistency report |
| full_pipeline | T10.1(-T10.2) | Hall bar on precomputed contours, full E2E pipeline (T10.2 nightly only) |

## Key difference from coding tests

**Coding test:** `python add_rect.py CELL 1 0 -50 -25 50 25` → check stdout
**Agentic test:** "Draw a 100x50um rectangle on layer 1/0" → agent decides how → verify layout state

The agent is free to use `execute_script`, skill scripts, or any combination. We only verify the **outcome**, not the method.

## Configuration

| Variable | Default | Description |
|----------|---------|-------------|
| `E2E_AGENT_MODEL` | `sonnet` | Claude model for agent invocations |
| `LLM_JUDGE_API_BASE` | `http://api.physcai.com` | Judge API endpoint |
| `LLM_JUDGE_API_KEY` | *(built-in)* | Judge API key |
| `LLM_JUDGE_MODEL` | `gpt-5-mini` | Judge model |
| `KLAYOUT_MCP_URL` | `http://127.0.0.1:8765/mcp` | MCP server URL |
