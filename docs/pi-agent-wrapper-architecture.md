# Pi-Agent Wrapper Architecture: Device Design Agent (qlaybot)

> Standalone TypeScript agent wrapping Pi-Agent SDK for quantum device design, orchestrating KLayout MCP server.

**Spec:** `docs/superpowers/specs/2026-03-26-pi-agent-wrapper-design.md`
**Location:** `KlayoutClaw/agent/`
**Approach:** Built from scratch, referencing qdevbot patterns

## 1. Vision

Build a minimal, physicist-thinking agent that orchestrates:
- **KLayout MCP** (`KLAYOUT_MCP_URL`, default port 8765, required) — chip layout design, geometry, GDS operations

Additional MCP servers can be added via config without code changes. All MCP tools are exposed directly (not proxied). Non-KLayout servers are registered but lazy-loaded — their tools are discovered and added to the tool registry on first call to that server, not included in the initial system prompt.

The agent reasons about device physics (transmon qubits, vdW heterostructures, EBL fabrication) and translates that reasoning into concrete design actions.

## 2. Reference Projects

### qdevbot (primary reference)
Three-layer Pi-Agent wrapper for device measurement:
```
qdevBot Layer (custom tools, memory, subagents, planning)
  └── Pi-Agent SDK (@mariozechner/pi-coding-agent)
      └── Pi-Agent Core (agentLoop, streaming, tool execution)
```

Key patterns adopted:
- **Direct Agent + AgentSession construction** (bypass piCreateAgentSession)
- **baseToolsOverride** for custom read/bash/edit/write
- **transformContext hook** for auto-recall memory
- **Workspace markdown files** for domain knowledge (SOUL.md, WORKFLOW.md, TOOLS.md)
- **RPC mode** for E2E testing and benchmark integration

### nanobot (architectural inspiration)
Minimal agent kernel (~4K lines):
- **Config-driven extensibility** (pure data, no hardcoded settings)
- **Bounded iteration limits** (40 main / 15 subagent)
- **Explicit context assembly** (history → memory → skills → bootstrap files)

## 3. Architecture

```
┌──────────────────────────────────────────────────┐
│              Device Design Agent (qlaybot)         │
│                                                    │
│  ┌──────────────────────────────────────────────┐ │
│  │  Agent Layer                                  │ │
│  │  ├── SOUL.md (physicist persona)              │ │
│  │  ├── WORKFLOW.md (design protocol)            │ │
│  │  ├── TOOLS.md (tool usage guide)              │ │
│  │  ├── RULES.md (design rules + constraints)    │ │
│  │  ├── Memory (FTS5: knowledge, procedures,     │ │
│  │  │          preferences, daily log)            │ │
│  │  └── Domain Knowledge (3-tier, deferred)      │ │
│  └──────────────────────────────────────────────┘ │
│                                                    │
│  ┌──────────────────────────────────────────────┐ │
│  │  Pi-Agent SDK                                 │ │
│  │  ├── AgentSession.prompt()                    │ │
│  │  ├── SessionManager (JSONL persistence)       │ │
│  │  └── agentLoop (outer × inner)                │ │
│  └──────────────────────────────────────────────┘ │
│                                                    │
│  ┌──────────────────────────────────────────────┐ │
│  │  Tool Layer                                   │ │
│  │  ├── Base: read, bash (conda), edit, write    │ │
│  │  ├── KLayout native (klayout.native.*)        │ │
│  │  │   ├── klayout.native.execute_script        │ │
│  │  │   ├── klayout.native.screenshot            │ │
│  │  │   └── ... (6 native MCP tools)             │ │
│  │  ├── KLayout domain (klayout.{group}.*)       │ │
│  │  │   ├── klayout.geometry.add_rect            │ │
│  │  │   ├── klayout.display.toggle_layer          │ │
│  │  │   └── ... (typed schemas from skills)      │ │
│  │  └── memory_save / memory_search              │ │
│  └──────────────────────────────────────────────┘ │
│                                                    │
│  ┌──────────────┐  ┌──────────────────────┐       │
│  │ KLayout MCP  │  │ (future MCP servers  │       │
│  │ :8765* [REQ] │  │  lazy-loaded)        │       │
│  └──────────────┘  └──────────────────────┘       │
└──────────────────────────────────────────────────┘
```

`*` The diagram shows the compatibility default; `KLAYOUT_MCP_URL` selects an
alternate endpoint.

## 4. MCP Connection Management

Config-driven MCP connections managed by `MCPManager`. Two client types:

### Custom KLayout Client

The KLayout MCP server uses HTTP JSON-RPC (no SSE/stdio), requiring a custom transport. The custom client registers both **native MCP tools** (6 tools auto-discovered from the server) and **domain tools** — typed tool schemas refactored from existing KlayoutClaw skills, defined in `src/tools/klayout/`.

```
KLayoutMCPClient
  ├── connect(url)                → HTTP JSON-RPC transport
  ├── discoverNativeTools()       → native MCP tools (6)
  ├── registerDomainTools()       → load typed tool schemas from src/tools/klayout/
  ├── callTool(name, args)        → route to execute_script or native tool
  └── healthCheck()               → verify KLayout is responsive
```

Domain tools generate pya code and execute it via `execute_script`. Each existing skill script becomes a typed tool with explicit JSON schema.

### Standard MCP Client

All other MCP servers use the standard MCP client SDK (SSE/stdio transports). Tools are namespaced as `{server_key}.{tool_name}` (2-level).

### MCPManager

```
MCPManager
  ├── servers: Map<server_key, MCPConnection>
  ├── callTool("klayout.geometry.add_rect", args)
  │   → route to KLayoutMCPClient or standard client
  ├── allTools() → flat list with namespaced names
  └── connectAll() → connect klayout (required); other servers registered but lazy-loaded
```

### Startup Behavior

1. Try to connect KLayout MCP via custom client.
2. If unavailable, auto-launch KLayout with platform detection:
   - macOS: `open /Applications/klayout.app`
   - Linux: `klayout &` (assumes `klayout` is on PATH)
   - Windows: `start klayout`
3. Poll with exponential backoff (1s → 2s → 4s, max 30s total), then reconnect.
4. If still unreachable after timeout, fail with actionable error message.
5. Register native MCP tools + domain tools.
6. Other MCP servers are registered but lazy-loaded — their tools are discovered on first call, not included in the initial system prompt.

**Extensibility:** Adding a new MCP server = one config entry. Config keys map to runtime namespace prefixes: the key (minus any `_mcp` suffix) becomes the tool prefix (e.g., `klayout_mcp` → `klayout.*`). No code changes.

## 5. Tool Naming & Inventory

### Naming Convention (3-level for KLayout, 2-level for others)

KLayout tools use 3-level dot-namespaced names: `klayout.{group}.{tool}`.

- **Native MCP tools**: `klayout.native.{tool}` — the 6 tools exposed by the KLayout MCP server
- **Domain tools**: `klayout.{group}.{tool}` — typed schemas refactored from existing skills

Other MCP servers use 2-level: `{server_key}.{tool_name}`.

### Native KLayout Tools (klayout.native.*)

| Namespaced Tool | Purpose |
|-----------------|---------|
| `klayout.native.create_layout` | Create new layout + top cell |
| `klayout.native.execute_script` | Run arbitrary pya code in KLayout |
| `klayout.native.save_layout` | Save as GDS2 or OASIS |
| `klayout.native.get_layout_info` | Layout summary (cells, layers, dbu) |
| `klayout.native.screenshot` | Capture viewport as PNG |
| `klayout.native.auto_route` | Autoroute pin pairs with ordered-loop assignment + sequential pathfinding (subprocess) |
| `klayout.native.evaluate_design` | Nanodevice DRC + metric evaluation (subprocess) |

### Domain Tool Groups (klayout.{group}.*)

Refactored from existing KlayoutClaw skills into typed tool schemas defined in `src/tools/klayout/`:

| Group | Tools | Source Skill |
|-------|-------|-------------|
| `klayout.geometry.*` | `add_rect`, `add_polygon`, `add_path`, `create_cell`, `add_instance` | `skills/geometry/` |
| `klayout.display.*` | `toggle_layer`, `show_only` | `skills/display/` |
| `klayout.image.*` | `add_image`, `list_images`, `remove_image` | `skills/image/` |
| `klayout.visual.*` | `capture` | `skills/visual/` |
| `klayout.nanodevice_flakedetect.*` | flake detection pipeline tools | `skills/nanodevice_flakedetect/` |
| `klayout.nanodevice_gdsalign.*` | GDS alignment tools | `skills/nanodevice_gdsalign/` |
| `klayout.nanodevice_routing.*` | routing tools | `skills/nanodevice_routing/` |

Domain tools generate pya code and execute it via `klayout.native.execute_script`.

> **Note:** `e2e_judge` remains a Claude Code skill under `.claude/skills/` — it is not an agent tool.

## 6. Workflow Protocol

### Device Design Workflow (3 phases)

**Phase 1: Plan**
- Understand the target device (qubit type, materials, dimensions)
- Scout existing layout (if any)
- Design geometry plan with layer assignments
- Validate against fabrication constraints

**Phase 2: Design**
- Create device geometry in KLayout
- Place alignment markers (L5/0)
- Define pin positions for routing
- Visual review with screenshots

**Phase 3: Interact**
- Place bonding pads
- Route leads: inner fine + outer coarse + boundary patches
- Validate routing (no overlaps, all pins connected)
- Save final GDS + generate fabrication report

Each phase has entry criteria, exit criteria, and tool usage patterns (defined in `workspace/WORKFLOW.md`).

### Existing Workflows (inherited from KlayoutClaw skills)
- **Flake Detection:** align → detect → combine → commit → review
- **GDS Alignment:** extract_markers → detect_markers → align_gds → commit_gds

## 7. Domain Knowledge (Deferred)

Three-tier knowledge system deferred until ai-scientist inputs are available. Placeholder directory structure ships with the agent:

```
workspace/knowledge/
├── core/           # Tier 1: Physics fundamentals (placeholder)
├── recipes/        # Tier 2: Lab-specific (placeholder)
└── learned/        # Tier 3: Agent-accumulated (placeholder)
```

> **GH Issue:** Define and populate three-tier domain knowledge with ai-scientist collaboration.

## 8. Safety & Validation

Design rules and fabrication constraints are defined in `workspace/RULES.md` and enforced via the agent's system prompt — the agent validates geometry against these rules before creating it.

### Design Rule Checking
- Minimum feature size per EBL window (inner: 0.1 um, outer: 0.5 um)
- Minimum spacing between features
- Layer assignment validation
- Pin connectivity verification post-routing

### Fabrication Safety
- No overlapping geometry on same layer
- All pins connected after routing
- Bonding pads within write field
- GDS file size reasonable

## 9. Technology Stack

```
Language:    TypeScript (following qdevbot)
Runtime:     Node.js
Agent SDK:   @mariozechner/pi-coding-agent + pi-agent-core
MCP Client:  Custom HTTP transport (KLayout) + standard SDK (others)
Memory:      better-sqlite3 FTS5
TUI:         React + Ink (terminal UI)
Tests:       vitest (unit/integration/E2E via RPC)
Build:       TypeScript compiler
```

## 10. Comparison: qdevbot vs qlaybot

| Aspect | qdevbot (measurement) | qlaybot (design) |
|--------|----------------------|-------------------|
| Domain | Device characterization | Device design |
| MCP Servers | instrMCP only | KLayout MCP (extensible via config) |
| Tool Naming | Flat (instrMCP tools) | Dot-namespaced: 3-level KLayout, 2-level others |
| Primary Output | Measurement data + analysis | GDS layout |
| Workflow | Plan→Prepare→Execute→Analyze | Plan→Design→Interact |
| Safety | Sweep parameter limits | Design rules via RULES.md |
| Domain Knowledge | Flat memory | 3-tier (deferred) |

## 11. Interaction History Auto-Save

All agent interactions are automatically persisted to `/tmp/KLayoutClaw_History/` for debugging, replay, and audit:

```
/tmp/KLayoutClaw_History/
├── sessions/
│   └── {session_id}/
│       ├── transcript.jsonl    # Full conversation: prompts, tool calls, results
│       ├── tool_calls/         # Individual tool call logs
│       │   ├── 001_klayout.native.execute_script.json
│       │   ├── 002_klayout.native.screenshot.json
│       │   └── ...
│       └── metadata.json       # Session start time, model, config snapshot
└── latest -> sessions/{most_recent_session_id}
```

**What gets saved:**
- Every user prompt and agent response
- Every MCP tool call: tool name, arguments, result, duration
- Errors and retries

**Auto-save is continuous** — each turn is appended to `transcript.jsonl` immediately, so partial sessions are recoverable after crashes.

## 12. Out of Scope (Initial Build)

- Context compaction (add when long sessions become an issue)
- Background task manager (add when long-running operations need it)
- Planning mode with approval gates (add when workflows become complex)
- Subagent system with role-based delegation (scout/designer/fabricator — add when single agent hits limits)
- Three-tier domain knowledge content (deferred for ai-scientist collaboration)
- Hybrid memory search with LLM rerank (FTS5 sufficient for MVP)
- Agentic workflow E2E tests — full Plan→Design→Interact chains (define after core is stable)
- Dynamic skill system for external/user-defined skills (existing skills refactored into native typed tools)
- Shared core library with qdevbot (premature abstraction)
