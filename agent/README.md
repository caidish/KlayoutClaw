# qlaybot — Device Design Agent

Standalone TypeScript agent (v0.4.3) wrapping Pi-Agent SDK for quantum device design, orchestrating KLayout via MCP.

## Features

- **Physicist-thinking design**: reasons about device physics before drawing geometry (SOUL.md / TOOLS.md / RULES.md workspace)
- **KLayout MCP integration**: ~10 native tools (discovered from the KLayout server) + 20 typed domain tools (5 geometry, 2 display, 3 image, 1 visual, 9 nanodevice) that generate pya code executed through `execute_script`
- **Dual-mode commands**: 10 slash commands work in shell (`qlaybot model`), TUI (`/model`), and RPC (`prompt` with `/` prefix)
- **Interactive Config Panel**: bare `/config`, `/model`, `/mcp` open a 3-tab Ink panel for live settings / model picker / MCP server & tool management; `/config setup` re-runs the guided wizard
- **Planning mode**: modal sandbox restricts tools to a read-only allowlist (`read`, `klayout_native_get_layout_info`, `klayout_native_screenshot`, `memory_save`, `memory_search`, `delegate`) and blocks everything else
- **Subagent delegation**: config-driven role subagents with `delegate` tool, per-role token + turn budgets, concurrent execution, and a TUI inspector panel
- **Background tasks**: long-running operations (auto-route, flake detection, GDS save) run asynchronously with cancel support
- **Context compaction**: 3-phase `transformContext` pipeline (tool-result pruner → state-loader → auto-recall) plus manual `/compact`, with KLayout-state XML extraction preserved across sessions
- **Hybrid memory search**: SQLite FTS5 + optional embedding vector search + Haiku reranker with 4 configurable modes (`fts5`, `fts5+rerank`, `fts5+vector+rerank`, `vector+rerank`); categories: knowledge / procedures / preferences / log
- **Polished Ink TUI**: markdown rendering with syntax-highlighted code, decomposed message components (UserMessage, AssistantMessage with ToolPanel/ThinkingIndicator), interactive panels (WorkspaceBar, BackgroundBar, ConfigPanel, SubagentPanel), StreamingBar with live metrics
- **RPC mode**: JSON-RPC on stdin/stdout with 8 event types for E2E testing and integration
- **Auto-launch**: detects and starts KLayout automatically (macOS / Linux / Windows) with exponential-backoff polling (1s → 2s → 4s → 8s)

## Quick Start

```bash
npm install
npm run build
export ANTHROPIC_API_KEY=your_key_here
npm start
```

### Global CLI (optional)

```bash
npm link
qlaybot              # works from any directory
```

## Modes

```bash
npm start                         # Interactive TUI (default)
npm start -- -m "add a rect"      # Single-shot JSON mode
npm start -- --mode rpc           # RPC mode for E2E testing
npm start -- --plain              # Plain readline (no Ink)
```

## CLI

```bash
qlaybot                               # Interactive TUI
qlaybot -m "add a rectangle"          # Single-shot JSON mode
qlaybot --mode rpc                    # RPC mode (stdin/stdout JSON-RPC)
qlaybot --plain                       # Plain readline

# Commands (shell mode — also available as /command in TUI)
qlaybot model [show|set|list]         # Model management
qlaybot mcp [status|tools|reconnect]  # MCP server management
qlaybot config [show|set|reset]       # Config management
qlaybot context                       # Workspace context info
qlaybot memory [show|search|clear]    # Memory management
qlaybot compact [instructions]        # Context compaction
qlaybot tasks                         # Background task status
qlaybot onboard                       # First-time setup (~/.qlaybot/)
qlaybot uninstall                     # Remove ~/.qlaybot/
qlaybot help [<command>]              # Help

# Options
--model <provider/modelId>            # Override default model
--thinking <level>                    # off|minimal|low|medium|high|xhigh
--cwd <path>                          # Working directory
```

## TUI Shortcuts

| Key | Action |
|-----|--------|
| `Ctrl+T` | Toggle tool detail + thinking expansion |
| `Ctrl+W` | Toggle workspace panel |
| `Ctrl+G` | Toggle background task panel |
| `Ctrl+A` | Cursor to start of line |
| `Ctrl+E` | Cursor to end of line |
| `Ctrl+D` | Delete forward |
| `↑/↓` | Command history / panel navigation |
| `Tab` | Cycle command completion |
| `Escape` | Abort agent / close panel |

## Commands

| Command | Subcommands | Description |
|---------|------------|-------------|
| `/model` | `show`, `set <provider/id>`, `list` | Show/switch active model; persists to `config/model.json`. Bare `/model` opens the Config Panel on the Models tab. |
| `/mcp` | `status`, `tools [server]`, `reconnect [server]` | MCP server management. Bare `/mcp` opens the Config Panel on the MCP tab. |
| `/config` | `show`, `set <key> <value>`, `reset`, `setup` | Persisted config management. Bare `/config` opens the Config Panel (Settings tab). `/config setup` backs up `config/` and re-runs the wizard. Settable keys include `thinking`, `model`, `memory.maxResults`, `memory.maxEntries`, `search.mode`, `search.minRerank`, `search.rerankMinScore`, `search.rerankMaxTokens`, `embedding.baseUrl`, `embedding.apiKey`, `embedding.model`, `embedding.dimensions`, `embedding.similarityThreshold`. |
| `/context` | — | Workspace file listing with sizes and live context-window usage |
| `/memory` | `show [category]`, `search <query>`, `clear <category>` | Memory inspection and deletion. Categories: `knowledge`, `procedures`, `preferences`, `log`. |
| `/plan` | `enter` (default), `exit`, `status` | Planning mode control (sandbox) |
| `/compact` | `[instructions]` | Trigger context compaction with optional custom instructions |
| `/tasks` | — | Background task status + cancel |
| `/help` | `[command]` | Command help |
| `/exit` | — | Graceful shutdown (TUI-only) |

## Architecture

```
qlaybot v0.4.3
  ├── Agent Layer
  │   ├── SOUL.md (physicist persona)
  │   ├── TOOLS.md / RULES.md (tool guide + design constraints)
  │   │   (E2E workflow lives in skills/nanodevice_e2e_design/SKILL.md)
  │   ├── Memory (SQLite FTS5 + embeddings: knowledge, procedures, preferences, log)
  │   └── Subagent Layer (role resolver, runner, tool factory, transcript)
  ├── Command Layer
  │   ├── CommandRegistry (10 handlers including /compact)
  │   ├── PlanManager + sandbox (allowlist) + onStateChange observer
  │   └── BackgroundTaskManager (async execution + cancel)
  ├── TUI Layer (Ink/React)
  │   ├── App.tsx (root, keyboard shortcuts, auto-compaction, focus state machine)
  │   ├── Messages: UserMessage, AssistantMessage, SystemMessage
  │   ├── Display: ThinkingIndicator, ToolPanel, MarkdownText
  │   ├── Input: InputBox (cursor + history + tab completion + ghost hints), CompletionList
  │   ├── Bars: StreamingBar, ErrorBanner, StatusBar
  │   ├── Panels: WorkspaceBar, BackgroundBar, ConfigPanel (3 tabs), SubagentPanel
  │   └── Foundation: theme.ts, markdown.ts, auto-compact.ts
  ├── Compaction Layer
  │   ├── tool-result-pruner (keep last N, prune large old results)
  │   ├── state-extractor (XML tags → workspace/compaction/ files)
  │   ├── state-loader (inject <compaction-state> into context)
  │   └── prompt-loader (COMPACT.md or KLayout-domain fallback)
  ├── Search Layer (v0.4)
  │   ├── FTS5 (SQLite porter unicode61)
  │   ├── VectorSearch (cosine over stored embeddings)
  │   ├── Reranker (Haiku cross-encoder + cache)
  │   └── 4 dispatch modes: fts5 | fts5+rerank | fts5+vector+rerank | vector+rerank
  ├── Pi-Agent SDK
  │   ├── AgentSession.prompt()
  │   ├── transformContext: pruner → stateLoader → autoRecall
  │   └── agentLoop (outer × inner) + prompt_too_long recovery
  ├── Tool Layer
  │   ├── Base: read, bash, edit, write (sandbox-wrapped)
  │   ├── KLayout native: klayout_native_* (discovered from MCP server)
  │   ├── KLayout domain: klayout_{group}_* (~20 typed tools → pya via execute_script)
  │   ├── Subagent: delegate (role-scoped, budgeted)
  │   └── Custom: memory_save, memory_search, background_status, background_result
  └── MCP Connections
      ├── KLayout MCP (KLAYOUT_MCP_URL, default :8765) [required, auto-launched with backoff]
      └── (additional servers lazy-loaded via mcp.json)
```

## Config

Runtime configuration at `~/.qlaybot/`:

| Path | Purpose |
|------|---------|
| `config/model.json` | Default model, thinking level, provider definitions |
| `config/mcp.json` | MCP server URLs, required flag, disabled tools |
| `config/settings.json` | Memory budget, compaction thresholds, MCP timeouts, TUI prefs, `search.*`, `embedding.*`, `subagent.roles` |
| `auth.json`, `models.json` | Auth storage + model registry (written beside `config/`) |
| `workspace/` | Domain knowledge (copied from `agent/workspace/` on onboard) |
| `workspace/compaction/COMPACT.md` | Compaction instruction template |
| `workspace/subagent/` | Subagent role templates |
| `memory/` | Persistent categorized memory + SQLite FTS5 + embeddings index |
| `sessions/` | JSONL session + interaction history |

`ANTHROPIC_API_KEY` (or provider-specific env vars) is picked up as the runtime API key for any matching provider in `config/model.json`.

## Testing

```bash
npm test                              # All tests (auto-skips E2E if env unavailable)
npm run test:unit                     # Unit tests only (588 tests)
npm run test:integration              # Integration tests (95 tests)
npm run test:e2e                      # E2E tests (needs KLayout + API key)
npm test -- --reporter=verbose        # Verbose output
QLAYBOT_NIGHTLY=1 npm test            # Include 5g long session test
QLAYBOT_E2E=0 npm test                # Skip E2E tests only
```

**697 tests across 16 files** — split into unit (588), integration (95), and E2E (14) tiers. See `agent/CLAUDE.md` for the full breakdown.

## v0.4.3 Features

### Plan Mode
A sandbox-gated planning mode with disk-persisted plan files. Enter via the `enter_plan_mode` tool (the agent invokes it proactively for multi-step device design) or by typing `/plan` at the prompt. While in plan mode, only the active plan file is writable — Bash and file writes outside the plan are blocked. Read, `klayout_get_layout_info`, `screenshot`, `route_inspect`, and `memory_search` remain available so the agent can gather evidence while planning. Exit plan mode from the menu (Ctrl+E → Exit Plan Mode) or via `/plan exit`.

### `--verbose` flag
Pass `--verbose` to disable tool-result truncation, print the full system prompt banner at startup, emit per-turn usage stats, and write a full-fidelity project transcript to `<workspace>/qlaybot-transcripts/`. Useful for debugging prompt assembly, long tool outputs, and cross-session replay.

### iTerm2 inline image rendering
When the terminal supports iTerm2's inline-image escape codes, base64 image blocks inside tool results (e.g. `screenshot` output) are rendered directly in the chat stream instead of shown as a base64 blob.

### Transcript truncation (2000-char head+tail)
By default, long tool-result entries in the in-session transcript are truncated to 2000 characters of head + 2000 characters of tail to keep the scrollback readable. Pass `--verbose` to disable truncation and keep the full output.

## License

Part of [KlayoutClaw](https://github.com/caidish/KlayoutClaw).
