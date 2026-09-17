# Pi-Agent Wrapper Development Guide (qlaybot)

> Guidelines for developing the device design Pi-Agent wrapper. Covers project structure, coding conventions, testing patterns, and integration requirements.

**Spec:** `docs/superpowers/specs/2026-03-26-pi-agent-wrapper-design.md`

## 1. Project Structure

```
KlayoutClaw/agent/
├── src/
│   ├── cli.ts                  # Entry point (interactive/JSON modes)
│   ├── rpc.ts                  # JSON-RPC entry point (stdin/stdout, for E2E tests)
│   ├── agent.ts                # createDesignSession(), baseToolsOverride
│   ├── index.ts                # Public API exports
│   ├── config.ts               # Config loading + defaults
│   ├── context.ts              # Workspace markdown loaders
│   │
│   ├── tools/                  # Tool implementations
│   │   ├── index.ts            # assembleTools() — base + KLayout + custom
│   │   ├── read.ts             # File reading
│   │   ├── bash.ts             # Shell exec (conda run -n base prefix)
│   │   ├── edit.ts             # File editing
│   │   ├── write.ts            # File writing
│   │   ├── memory.ts           # memory_save + memory_search
│   │   └── klayout/            # Domain tools (refactored from skills)
│   │       ├── index.ts        # Register all domain tool groups
│   │       ├── geometry.ts     # add_rect, add_polygon, add_path, create_cell, add_instance
│   │       ├── display.ts      # toggle_layer, show_only
│   │       ├── image.ts        # add_image, list_images, remove_image
│   │       ├── visual.ts       # capture
│   │       └── nanodevice.ts   # flakedetect, gdsalign, routing tools
│   │
│   ├── mcp/                    # MCP connection layer
│   │   ├── manager.ts          # MCPManager — connect, discover, route
│   │   ├── klayout-client.ts   # Custom KLayout HTTP client
│   │   ├── transport.ts        # HTTP JSON-RPC transport
│   │   └── types.ts            # MCPConnection, ToolSchema types
│   │
│   ├── memory/                 # Memory persistence
│   │   ├── index.ts            # MemoryManager
│   │   ├── auto-recall.ts      # transformContext hook
│   │   └── parser.ts           # Entry parser
│   │
│   ├── prompts/                # System prompt construction
│   │   ├── index.ts            # buildSystemPrompt()
│   │   └── sections/           # Modular prompt sections
│   │
│   └── tui/                    # Terminal UI (React + Ink)
│       ├── render.ts
│       ├── reducer.ts
│       └── components/
│
├── workspace/                  # Domain knowledge (→ ~/.qlaybot/workspace/)
│   ├── SOUL.md                 # Physicist persona + design principles
│   ├── WORKFLOW.md             # Design protocol
│   ├── TOOLS.md                # MCP tool usage guide
│   ├── RULES.md                # Design rules + constraints
│   └── knowledge/              # Three-tier domain knowledge (placeholder)
│       ├── core/               # Tier 1: physics fundamentals (placeholder)
│       ├── recipes/            # Tier 2: lab-specific (placeholder)
│       └── learned/            # Tier 3: agent-accumulated (placeholder)
│
├── tests/                      # Test suite (vitest)
│   ├── test-agent.ts           # Session creation, prompt building
│   ├── test-mcp.ts             # MCP connection + routing
│   ├── test-tools.ts           # Tool dispatch + domain tool schemas
│   ├── test-memory.ts          # FTS5 search, auto-recall
│   └── test-e2e.ts             # RPC mode E2E (requires servers)
│
├── package.json
├── tsconfig.json
├── CLAUDE.md                   # Dev instructions for Claude agents
└── README.md
```

## 2. Agent Session Creation

Direct Agent + AgentSession construction for full control:

```typescript
// src/agent.ts (conceptual)
import { Agent, AgentSession } from "@mariozechner/pi-coding-agent";

export async function createDesignSession(options: SessionOptions) {
  const config = loadConfig();
  const mcpManager = new MCPManager(config.mcp);
  await mcpManager.connectAll();  // KLayout eagerly; other servers registered but lazy-loaded

  const tools = assembleTools({
    baseToolsOverride: { read, bash, edit, write },
    mcpTools: mcpManager.allTools(),  // dot-namespaced (3-level for KLayout)
    customTools: [
      memorySave(memoryManager),
      memorySearch(memoryManager),
    ],
  });

  const systemPrompt = buildSystemPrompt({
    mode: PromptMode.Full,
    workspaceDir: config.workspace,
    mcpToolNames: mcpManager.allToolNames(),  // KLayout tools only
  });

  const agent = new Agent({ tools, systemPrompt, model: config.model });
  const session = new AgentSession(agent, {
    transformContext: autoRecallHook(memoryManager),
  });

  return session;
}
```

## 3. MCP Tool Routing

### KLayout Client (Custom HTTP Transport)

The KLayout MCP server uses HTTP JSON-RPC, requiring a custom client that handles both native MCP tools and domain tools:

```typescript
// src/mcp/klayout-client.ts (conceptual)
class KLayoutMCPClient {
  // Connect via HTTP JSON-RPC transport
  async connect(url: string): Promise<void>;

  // Discover 6 native tools from server → klayout.native.*
  async discoverNativeTools(): Promise<Tool[]>;

  // Load typed tool schemas from src/tools/klayout/ → klayout.{group}.*
  registerDomainTools(): Tool[];

  // Route: domain tools generate pya code + call execute_script
  //        native tools forward directly to MCP server
  async callTool(name: string, args: any): Promise<any>;
}
```

### MCPManager

Routes all tool calls by parsing the dot-namespaced name:

```typescript
// src/mcp/manager.ts (conceptual)
class MCPManager {
  private klayout: KLayoutMCPClient;
  private servers: Map<string, MCPConnection>;  // non-KLayout servers

  // "klayout.geometry.add_rect" → KLayoutMCPClient
  // "other_server.some_tool" → standard client
  async callTool(namespacedName: string, args: any) {
    if (namespacedName.startsWith("klayout.")) {
      return this.klayout.callTool(namespacedName, args);
    }
    const dotIndex = namespacedName.indexOf(".");
    const serverKey = namespacedName.slice(0, dotIndex);
    const toolName = namespacedName.slice(dotIndex + 1);
    return this.servers.get(serverKey)!.callTool(toolName, args);
  }

  // KLayout tools: 3-level (klayout.native.*, klayout.geometry.*, ...)
  // Other servers: 2-level ({server_key}.{tool_name})
  allTools(): Tool[] { ... }
}
```

**Adding a new MCP server** = one entry in `~/.qlaybot/config/mcp.json`. Config keys map to runtime namespace prefixes: the key (minus any `_mcp` suffix) becomes the tool prefix (e.g., `klayout_mcp` → `klayout.*`). Tools auto-discovered and namespaced on first call (lazy-loaded). No code changes.

## 4. Domain Tools

Each existing KlayoutClaw skill script is refactored into a typed tool with explicit JSON schema, defined in `src/tools/klayout/`. The implementation generates pya code and executes it via `klayout.native.execute_script`.

Example — `klayout.geometry.add_rect`:

```json
{
  "name": "klayout.geometry.add_rect",
  "description": "Add a rectangle to a cell",
  "inputSchema": {
    "type": "object",
    "properties": {
      "cell": { "type": "string" },
      "layer": { "type": "integer" },
      "datatype": { "type": "integer" },
      "x1": { "type": "number" },
      "y1": { "type": "number" },
      "x2": { "type": "number" },
      "y2": { "type": "number" }
    },
    "required": ["cell", "layer", "datatype", "x1", "y1", "x2", "y2"]
  }
}
```

Domain tool groups: `geometry`, `display`, `image`, `visual`, `nanodevice_flakedetect`, `nanodevice_gdsalign`, `nanodevice_routing`.

## 5. Interaction History Auto-Save

The agent auto-saves all interactions to `/tmp/KLayoutClaw_History/`:

```
/tmp/KLayoutClaw_History/
├── sessions/
│   └── {session_id}/
│       ├── transcript.jsonl    # Full conversation: prompts, tool calls, results
│       ├── tool_calls/         # Individual tool call logs (name, args, result, duration)
│       └── metadata.json       # Session config snapshot
└── latest -> sessions/{most_recent}
```

Every turn is appended to `transcript.jsonl` immediately — partial sessions are recoverable after crashes. Useful for debugging agent behavior, replaying sessions, and auditing tool usage.

## 6. Testing Patterns

### Tier 1: Unit Tests (no servers, mocked MCPManager)

```typescript
// tests/test-tools.ts
describe("domain tool schemas", () => {
  it("all tool groups register with valid inputSchema", () => {
    const tools = registerAllDomainTools();
    for (const tool of tools) {
      expect(tool.name).toMatch(/^klayout\.\w+\.\w+$/);
      expect(tool.inputSchema).toBeDefined();
      expect(tool.inputSchema.type).toBe("object");
    }
  });
});

// tests/test-mcp.ts
describe("tool routing", () => {
  it("routes klayout.native.execute_script to klayout client", async () => {
    const mockManager = createMockMCPManager();
    const result = await mockManager.callTool(
      "klayout.native.execute_script",
      { code: "result = _layout.dbu" }
    );
    expect(result.status).toBe("ok");
  });

  it("routes klayout.geometry.add_rect through domain tool layer", async () => {
    const mockManager = createMockMCPManager();
    const result = await mockManager.callTool(
      "klayout.geometry.add_rect",
      { cell: "TOP", layer: 1, datatype: 0, x1: 0, y1: 0, x2: 100, y2: 25 }
    );
    expect(result.status).toBe("ok");
  });

  it("rejects unknown namespace", async () => {
    await expect(
      mockManager.callTool("unknown.tool", {})
    ).rejects.toThrow();
  });
});
```

### Tier 2: Integration Tests (require servers running)

```typescript
// tests/test-mcp.ts
describe("MCP connections", () => {
  it("connects to KLayout and discovers native + domain tools", async () => {
    const manager = new MCPManager(testConfig);
    await manager.connectAll();
    expect(manager.isConnected("klayout")).toBe(true);
    const tools = manager.allToolNames();
    expect(tools).toContain("klayout.native.execute_script");
    expect(tools).toContain("klayout.geometry.add_rect");
  });

  it("gracefully degrades without optional servers", async () => {
    const manager = new MCPManager(testConfig);
    await manager.connectAll();
    expect(manager.isConnected("klayout")).toBe(true);
  });
});
```

### Tier 3: E2E Tests (full agent loop via RPC)

Tests use JSON-RPC on stdin/stdout. Methods: `initialize`, `prompt`, `shutdown`. Ephemeral sessions per test.

**Test 3a — Basic agent loop:**
1. Send "echo Hello World" → verify echoed output
2. Send "Calculate 1+1" → use LLM-judge to verify correct answer

**Test 3b — MCP connection and tool routing:**
1. Send "Connect to KLayout and create a new layout" → verify correct tool calls in MCP server log

**Test 3c — Session memory:**
1. Send "Remember this password: i_hate_calculus"
2. Start a new session, send "What is the password? State directly `<pwd>content</pwd>`"
3. Parse `<pwd>` tag in response and verify content matches

**Test 3d — Domain tool execution:**
1. Create a layout first
2. Send "Add a 100x25 um rectangle at origin on layer 1/0"
3. Verify via `execute_script` (read-back pya code querying `cell.shapes()`) that the rectangle exists with correct bbox dimensions

**Test 3e — Auto-launch KLayout:**
1. Ensure KLayout is not running (`pkill -x klayout`)
2. Start the agent — it should auto-launch KLayout and connect after polling
3. Send "Create a new layout" → verify it succeeds

**Test 3f — Error recovery (KLayout unreachable):**
1. Stop KLayout MCP server and block auto-launch
2. Start the agent → verify it fails with actionable error message after timeout, not a crash

**Test 3g — Multi-turn conversation:**
1. Send "Create a new layout called test"
2. Send "Add a cell called main"
3. Send "What cells exist?" → verify "main" appears in response

### CI Strategy
- Tier 1: every push (fast, no servers)
- Tier 2: every push (requires servers, but no GUI)
- Tier 3: nightly or manual (requires KLayout)

## 7. Configuration

### Runtime Config (`~/.qlaybot/`)

```
~/.qlaybot/
├── config/
│   ├── model.json              # { "defaultModel": "...", "thinkingLevel": "..." }
│   ├── mcp.json                # MCP server URLs + required/optional flags
│   └── settings.json            # Memory budgets
├── workspace/                  # Copied from agent/workspace/ on first run
├── memory/                     # Persistent memory categories
├── sessions/                   # JSONL session history
└── logs/                       # Agent logs
```

### Model Configuration (model.json)

```json
{
  "defaultModel": "anthropic/claude-sonnet-4-5-20250929",
  "thinkingLevel": "high",
  "providers": {
    "custom-anthropic": {
      "baseUrl": "https://bench.physcai.com/api",
      "apiKey": "your api_key",
      "api": "anthropic-messages",
      "models": [
        {
          "id": "claude-opus-4-6",
          "name": "Claude Opus 4.6",
          "reasoning": true,
          "input": ["text"],
          "cost": { "input": 5, "output": 25, "cacheRead": 0.5, "cacheWrite": 6.25 },
          "contextWindow": 200000,
          "maxTokens": 131072
        },
        {
          "id": "claude-sonnet-4-5",
          "name": "Claude Sonnet 4.5",
          "reasoning": true,
          "input": ["text"],
          "cost": { "input": 3, "output": 15, "cacheRead": 0.3, "cacheWrite": 3.75 },
          "contextWindow": 200000,
          "maxTokens": 65536
        },
        {
          "id": "claude-haiku-4-5",
          "name": "Claude Haiku 4.5",
          "reasoning": true,
          "input": ["text"],
          "cost": { "input": 1, "output": 5, "cacheRead": 0.1, "cacheWrite": 1.25 },
          "contextWindow": 200000,
          "maxTokens": 65536
        }
      ]
    }
  }
}
```

Uses a custom Anthropic-compatible provider proxy. All models support reasoning (extended thinking). Costs match official Anthropic pricing (per 1M tokens).

### MCP Configuration (mcp.json)

```json
{
  "klayout_mcp": { "url": "http://127.0.0.1:8765/mcp", "required": true }
}
```

Additional servers can be added as needed:
```json
{
  "klayout_mcp": { "url": "http://127.0.0.1:8765/mcp", "required": true },
  "other_mcp":   { "url": "http://127.0.0.1:9000/mcp", "required": false }
}
```

## 8. Coding Conventions

### TypeScript
- Strict mode enabled (`"strict": true` in tsconfig.json)
- No `any` types — use proper interfaces
- Async/await throughout (no raw promises)
- Barrel exports via index.ts files

### Tool Implementations
- Base tools in individual files under `src/tools/`
- Domain tools organized by group in `src/tools/klayout/` (geometry.ts, display.ts, etc.)
- Each domain tool has explicit JSON schema (inputSchema) — generates pya code via `execute_script`
- MCP native tools auto-discovered from KLayout server
- Custom tools (memory) have explicit implementations

### Prompt Engineering
- System prompt built from modular sections (`src/prompts/sections/`)
- Domain knowledge in markdown files (`workspace/`)
- Only KLayout tools included in initial system prompt
- Other MCP server tools discovered on-demand (not in prompt)
- Use `PromptMode.Full` for main agent

### Memory
- Four categories: knowledge, procedures, preferences, daily log
- FTS5 full-text search (better-sqlite3)
- Auto-recall via transformContext hook (throttled reindex, 5s min)
- Entries timestamped with tags

## 9. Development Workflow

```bash
# Setup
cd KlayoutClaw/agent
npm install
npm run build

# Development
npm run dev          # watch mode
npm test             # run tests
npm test:watch       # watch tests

# Running
npm start            # interactive TUI mode
npm start -- --mode json   # JSON mode (piped)
npm start -- --mode rpc    # RPC mode (E2E testing)
```

## 10. Key Dependencies

| Package | Purpose |
|---------|---------|
| `@mariozechner/pi-coding-agent` | Pi-Agent SDK |
| `@mariozechner/pi-agent-core` | Core agent loop |
| `@anthropic-ai/sdk` | Anthropic API |
| `better-sqlite3` | SQLite FTS5 for memory |
| `ink` + `@inkjs/ui` | Terminal UI framework |
| `chalk` | Terminal colors |
| `vitest` | Test runner |
| `typescript` | Language |

## 11. Integration Requirements

### Prerequisites
- KLayout running with KlayoutClaw plugin (`KLAYOUT_MCP_URL`, default port 8765) — **required** (auto-launched if not running)
- conda env `base` with: numpy, scipy, scikit-image, opencv

### Startup Behavior
1. Connect KLayout MCP via custom HTTP client.
2. If unavailable, auto-launch KLayout with platform detection:
   - macOS: `open /Applications/klayout.app`
   - Linux: `klayout &`
   - Windows: `start klayout`
3. Poll with exponential backoff (1s → 2s → 4s, max 30s total).
4. If still unreachable, fail with actionable error message.
5. Register native MCP tools + domain tools.
6. Other MCP servers are registered but lazy-loaded on first tool call.

## 12. Lessons from Explorations

### From nanobot
- Keep it minimal. Transparency over abstraction.
- Config-driven extensibility — no hardcoded settings.
- Bounded iteration limits prevent runaway loops.

### From qdevbot
- Direct Agent+AgentSession construction gives full control.
- baseToolsOverride for conda-prefixed bash is essential.
- transformContext hook is the right place for auto-recall.
- RPC mode enables E2E testing and benchmark integration.

### From KlayoutClaw
- execute_script is the power tool — can do anything pya can do.
- auto_route runs as subprocess — heavy computation stays out of main thread.
- Screenshot captures exactly what user sees — essential for visual review.
- Absolute paths required — KLayout CWD is `/`.
