# qlaybot Agent — Dev Instructions

## Quick Start
```bash
cd KlayoutClaw/agent
npm install
npm run build
npm test
npm start  # Interactive TUI
npm link   # Global CLI (optional)
```

## Architecture
- Pi-Agent SDK wrapper with direct Agent + AgentSession construction
- Custom KLayout MCP client (HTTP JSON-RPC via `KLAYOUT_MCP_URL`, default `:8765`)
- Domain tools generate pya code, executed via `execute_script`
- Tool names use underscores (Anthropic API requirement): `klayout_geometry_add_rect`
- Memory via SQLite FTS5 (better-sqlite3) with auto-recall transformContext hook + optional vector search and reranking
- **Search modes** (v0.4): `fts5`, `fts5+rerank`, `fts5+vector+rerank`, `vector+rerank`
- Memory budget limits (configurable max entries + file size per category)
- Ink/React TUI with useReducer state machine (25+ action types)
- **CommandRegistry** — unified `/command` handling across shell, TUI, RPC
- **PlanManager** — modal plan mode with KLayout-aware sandbox + `onStateChange` observer
- **BackgroundTaskManager** — async execution with `cancel()` support
- **Context Compaction** — 3-phase transformContext: pruner → stateLoader → autoRecall
- **Theme system** — centralized chalk-based color definitions
- **Markdown rendering** — pi-tui + cli-highlight for syntax-highlighted code blocks

## Key Files
- `src/agent.ts` — `createDesignSession()`, 3-phase transformContext pipeline, `compact()` method
- `src/commands/` — CommandRegistry + 10 command handlers (model, mcp, config, context, memory, plan, tasks, compact, help, exit)
- `src/planning/` — PlanManager + sandbox tool wrapper + onStateChange observer
- `src/background/` — BackgroundTaskManager with cancel() + "cancelled" status
- `src/compaction/` — tool-result-pruner, state-extractor, state-loader, prompt-loader
- `src/mcp/manager.ts` — MCPManager routes tool calls, auto-launches KLayout
- `src/mcp/klayout-client.ts` — Custom HTTP JSON-RPC client + domain tool registry
- `src/tools/klayout/` — Domain tool schemas with pya code generation
- `src/tools/background.ts` — background_status + background_result tools
- `src/memory/index.ts` — MemoryManager with FTS5 search + budget + clear()
- `src/memory/embedder.ts` — Embedding client (OpenAI-compatible API, configurable model/dimensions)
- `src/memory/vector-search.ts` — Vector similarity search over SQLite-stored embeddings
- `src/memory/reranker.ts` — Cross-encoder reranker for search result fusion and scoring
- `src/types/v04-contracts.ts` — v0.4 type contracts (search modes, config schemas, embedder types)
- `src/tui/theme.ts` — Centralized chalk color definitions
- `src/tui/markdown.ts` — Markdown → ANSI renderer
- `src/tui/auto-compact.ts` — Pure `shouldAutoCompact()` function
- `src/tui/commands.ts` — Tab completion matching for `/` commands
- `src/tui/workspace.ts` — Workspace file listing + integrity check
- `src/tui/hooks/` — useInputBuffer (cursor-aware editing), useCommandHistory (↑/↓ navigation)
- `src/tui/components/` — 14 Ink components:
  - App.tsx (root + keyboard shortcuts + auto-compaction)
  - MessageList, UserMessage, AssistantMessage, SystemMessage
  - ThinkingIndicator, ToolPanel, MarkdownText
  - InputBox (cursor + history + tab completion), CompletionList
  - StreamingBar, ErrorBanner, StatusBar
  - WorkspaceBar, BackgroundBar
- `src/rpc.ts` — JSON-RPC server (initialize, prompt, get_session_info, dispose, shutdown)
- `workspace/` — Domain knowledge (SOUL, TOOLS, RULES)
- `workspace/compaction/COMPACT.md` — KLayout-domain compaction instruction template

## Commands (v0.4)
| Command | Description |
|---------|-------------|
| `/model [show\|set\|list]` | Model management |
| `/mcp [status\|tools\|reconnect]` | MCP server management |
| `/config [show\|set\|reset\|setup]` | Config management (setup: interactive guided config) |
| `/context` | Workspace context info |
| `/memory [show\|search\|clear]` | Memory management |
| `/plan [enter\|exit\|status]` | Planning mode (sandbox) |
| `/tasks` | Background task status |
| `/compact [instructions]` | Trigger context compaction |
| `/help [command]` | Help |
| `/exit` | Graceful shutdown (TUI) |

## Keyboard Shortcuts (TUI)
| Shortcut | Action |
|----------|--------|
| Ctrl+T | Toggle tool detail + thinking expansion |
| Ctrl+W | Toggle workspace panel |
| Ctrl+G | Toggle background task panel |
| Ctrl+A | Cursor to start of line |
| Ctrl+E | Cursor to end of line |
| Ctrl+D | Delete forward |
| ↑/↓ | Command history / panel navigation |
| Tab | Cycle command completion |
| Escape | Abort agent / close panel |

## Testing
```bash
npm test                         # All tests (auto-detects environment)
npm run test:unit                # Unit tests only (588 tests, 12 files)
npm run test:integration         # Integration tests (95 tests, 3 files)
npm run test:e2e                 # E2E tests (14 tests, needs KLayout + API key)
npm test -- --reporter=verbose   # Verbose output
QLAYBOT_NIGHTLY=1 npm test       # Include 5g long session test
QLAYBOT_E2E=0 npm test           # Skip E2E tests
```

E2E tests need `ANTHROPIC_API_KEY` set + KLayout MCP at `KLAYOUT_MCP_URL` (default `:8765`).

### Test Suite (697 tests, 16 files)

**Unit (588 tests, 12 files):**
- **test-unit.ts** (106): config, tools, memory, commands, planning, background, MCP, reducer, theme, markdown, hooks, workspace, shortcuts, auto-compact
- **test-components.ts** (51): all 14 TUI components (leaf + composite)
- **test-compaction.ts** (15): pruner, extractor, loader, config, integration
- **test-search.ts** (57): embedder, vector-search, reranker, search mode integration, config validation
- **test-config-tui.ts** (105): config TUI reducer, state machine, settings persistence
- **test-config-tui-components.ts** (76): config panel components, form inputs, validation
- **test-contracts.ts** (28): v0.4 type contracts, schema validation
- **test-subagent.ts** (64): subagent runner, lifecycle, events, delegation
- **test-subagent-components.ts** (42): subagent TUI panel components
- **test-tier1-bugs.ts** (8): critical bug regressions
- **test-tier2-bugs.ts** (6): secondary bug regressions
- **test-reviewer-findings.ts** (30): code review finding regressions

**Integration (95 tests, 3 files):**
- **test-search-integration.ts** (24): end-to-end search pipeline, mode switching, recall integration
- **test-runtime-wiring.ts** (28): session structure, subagent wiring, TUI event bridge
- **test-subagent-e2e.ts** (43): subagent full pipeline, concurrent execution, budget enforcement

**E2E (14 tests, 1 file):**
- **test-e2e.ts** (14): suites 3a-5g (agent loop, MCP, domain tools, multi-turn, autonomy)

## Config
- `ANTHROPIC_API_KEY` env var → apiKey for all providers
- Runtime config at `~/.qlaybot/config/` (model.json, mcp.json, settings.json)
- KLayout MCP at `KLAYOUT_MCP_URL` (default `http://127.0.0.1:8765/mcp`)
- Memory budget: `settings.json` → `memory.budget`
- Compaction: `settings.json` → `compaction` (autoThreshold: 90%, warningThreshold: 70%, toolResultPruning)
- Search (v0.4): `settings.json` → `search.mode`, `search.minRerank`, `search.rerankMinScore`, `search.rerankMaxTokens`
- Embedding (v0.4): `settings.json` → `embedding.baseUrl`, `embedding.apiKey`, `embedding.model`, `embedding.dimensions`, `embedding.similarityThreshold`
- Plan re-injection (issue #24): `settings.json` → `plan.reinjectionInterval` (default `3`). After the agent exits plan mode, the current plan file contents are re-injected as a `<plan-reinjection>` reminder every N successful user turns. Set to `0` to disable. Stop reminders for the current plan with `/plan verify`.

## RPC Events
`ready`, `prompt_start`, `content_delta`, `thinking`, `tool_use`, `tool_result`, `usage_update`, `error`

## RPC Extensions (v0.3)
- `get_session_info` returns `planMode`, `backgroundTasks`, `contextUsage`
- `prompt` with `/` prefix routes to CommandRegistry
- `/compact` triggers compaction via RPC
