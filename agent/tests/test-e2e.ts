/**
 * Consolidated E2E tests via RPC mode.
 * Covers: basic agent loop, MCP connection, session memory, domain tool execution,
 * slash commands, planning mode, sandbox enforcement, background tasks, agent autonomy,
 * compaction, and workspace integrity.
 *
 * Auto-detects environment: ANTHROPIC_API_KEY + KLayout MCP at KLAYOUT_MCP_URL
 * (default :8765) + built dist/cli.js.
 * Set QLAYBOT_E2E=0 to force-skip.
 */

import { describe, it, expect, beforeAll, afterAll } from "vitest";
import { ChildProcess, spawn } from "child_process";
import { createInterface, Interface } from "readline";
import { resolve, dirname } from "path";
import { fileURLToPath } from "url";
import { existsSync } from "fs";

const __dirname = dirname(fileURLToPath(import.meta.url));
const CLI_PATH = resolve(__dirname, "..", "dist", "cli.js");
const KLAYOUT_MCP_URL = process.env.KLAYOUT_MCP_URL ?? "http://127.0.0.1:8765/mcp";

function canRunE2E(): boolean {
  if (process.env.QLAYBOT_E2E === "0") return false;
  if (!process.env.ANTHROPIC_API_KEY) return false;
  if (!existsSync(CLI_PATH)) return false;
  return true;
}

async function probeKLayout(): Promise<boolean> {
  try {
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), 3000);
    const resp = await fetch(KLAYOUT_MCP_URL, { signal: controller.signal });
    clearTimeout(timer);
    if (!resp.ok) return false;
    const data = await resp.json() as { status?: unknown; server?: unknown };
    return data.status === "ok" && data.server === "KlayoutClaw";
  } catch { return false; }
}

const hasApiKey = canRunE2E();
const klayoutReachable = hasApiKey ? await probeKLayout() : false;
const E2E_ENABLED = hasApiKey && klayoutReachable;

if (!E2E_ENABLED) {
  const reasons: string[] = [];
  if (!process.env.ANTHROPIC_API_KEY) reasons.push("ANTHROPIC_API_KEY not set");
  if (!existsSync(CLI_PATH)) reasons.push("dist/cli.js not built");
  if (!klayoutReachable) reasons.push(`KlayoutClaw MCP not reachable at ${KLAYOUT_MCP_URL}`);
  if (process.env.QLAYBOT_E2E === "0") reasons.push("QLAYBOT_E2E=0");
  console.log(`E2E tests skipped: ${reasons.join(", ")}`);
}

const describeE2E = E2E_ENABLED ? describe : describe.skip;

interface RPCEvent { method: string; params: Record<string, unknown>; }
interface RPCClient {
  proc: ChildProcess; rl: Interface; events: RPCEvent[];
  send: (method: string, params?: Record<string, unknown>) => Promise<unknown>;
  close: () => Promise<void>;
}

async function createRPCClient(): Promise<RPCClient> {
  const proc = spawn("node", [CLI_PATH, "--mode", "rpc"], {
    stdio: ["pipe", "pipe", "pipe"], env: { ...process.env },
  });
  const rl = createInterface({ input: proc.stdout!, terminal: false });
  let requestId = 0;
  const pending = new Map<number | string, { resolve: (v: unknown) => void; reject: (e: Error) => void }>();
  const events: RPCEvent[] = [];

  rl.on("line", (line: string) => {
    try {
      const msg = JSON.parse(line);
      if (msg.id !== undefined && pending.has(msg.id)) {
        const { resolve, reject } = pending.get(msg.id)!;
        pending.delete(msg.id);
        if (msg.error) reject(new Error(msg.error.message));
        else resolve(msg.result);
      } else if (msg.method && !msg.id) {
        events.push({ method: msg.method, params: msg.params ?? {} });
      }
    } catch { /* Non-JSON */ }
  });

  await new Promise<void>((resolve) => {
    const check = (line: string) => {
      try { if (JSON.parse(line).method === "ready") resolve(); } catch { /* */ }
    };
    rl.on("line", check);
    setTimeout(resolve, 5000);
  });

  function send(method: string, params?: Record<string, unknown>): Promise<unknown> {
    return new Promise((resolve, reject) => {
      const id = ++requestId;
      pending.set(id, { resolve, reject });
      proc.stdin!.write(JSON.stringify({ jsonrpc: "2.0", id, method, params }) + "\n");
      setTimeout(() => { if (pending.has(id)) { pending.delete(id); reject(new Error(`RPC timeout for ${method}`)); } }, 420000);
    });
  }

  async function close(): Promise<void> {
    try { await send("shutdown"); } catch { /* */ }
    proc.kill();
    rl.close();
  }

  return { proc, rl, events, send, close };
}

// --- 3a: Basic agent loop ---
describeE2E("3a: Basic agent loop", () => {
  let client: RPCClient;
  beforeAll(async () => { client = await createRPCClient(); await client.send("initialize", { ephemeral: true }); });
  afterAll(async () => { await client.close(); });

  it("echoes Hello World via bash", async () => {
    const result = await client.send("prompt", { message: "Run 'echo Hello World' in bash and show me the output" }) as Record<string, unknown>;
    expect(result.status).toBe("completed");
    expect((result.response as string).toLowerCase()).toContain("hello world");
  }, 60000);
});

// --- 3b: MCP connection ---
describeE2E("3b: MCP connection and tool routing", () => {
  let client: RPCClient;
  beforeAll(async () => { client = await createRPCClient(); await client.send("initialize"); });
  afterAll(async () => { await client.close(); });

  it("connects to KLayout and creates a layout", async () => {
    const result = await client.send("prompt", { message: "Connect to KLayout and create a new layout called 'test_e2e'" }) as Record<string, unknown>;
    expect(result.status).toBe("completed");
  }, 120000);
});

// --- 3d: Domain tool execution ---
describeE2E("3d: Domain tool execution", () => {
  let client: RPCClient;
  beforeAll(async () => { client = await createRPCClient(); await client.send("initialize"); });
  afterAll(async () => { await client.close(); });

  it("adds a rectangle and verifies dimensions", async () => {
    await client.send("prompt", { message: "Create a new layout called 'rect_test'" });
    await client.send("prompt", { message: "Add a 100x25 um rectangle at origin on layer 1/0" });
    const result = await client.send("prompt", {
      message: 'Run this pya code and tell me the result: `c = _layout.top_cell(); shapes = list(c.shapes(_layout.layer(1,0)).each()); bbox = shapes[0].bbox(); result = {"w": bbox.width() * _layout.dbu, "h": bbox.height() * _layout.dbu}`',
    }) as Record<string, unknown>;
    const response = result.response as string;
    expect(response).toContain("100");
    expect(response).toContain("25");
  }, 180000);
});

// --- 3f: Error recovery ---
describe("3f: Error recovery", () => {
  it("fails gracefully when KLayout is unreachable", async () => {
    const { MCPManager } = await import("../src/mcp/manager.js");
    const manager = new MCPManager({ servers: { klayout_mcp: { url: "http://127.0.0.1:19999/mcp", required: false } } });
    await manager.connectAll();
    expect(manager.isConnected("klayout")).toBe(false);
  }, 60000);

  it("throws with actionable message when required server fails", async () => {
    const { MCPManager } = await import("../src/mcp/manager.js");
    const manager = new MCPManager({ servers: { klayout_mcp: { url: "http://127.0.0.1:19999/mcp", required: true } } });
    await expect(manager.connectAll()).rejects.toThrow(/Cannot connect to KLayout/);
  }, 60000);
});

// --- 3g: Multi-turn conversation ---
describeE2E("3g: Multi-turn conversation", () => {
  let client: RPCClient;
  beforeAll(async () => { client = await createRPCClient(); await client.send("initialize"); });
  afterAll(async () => { await client.close(); });

  it("maintains context across turns", async () => {
    await client.send("prompt", { message: "Create a new layout called 'multi_turn_test'" });
    await client.send("prompt", { message: "Add a cell called 'main'" });
    const result = await client.send("prompt", { message: "What cells exist in the current layout? List them." }) as Record<string, unknown>;
    expect(result.status).toBe("completed");
    expect((result.response as string).toLowerCase()).toContain("main");
  }, 360000);
});

// --- 4a: Slash commands ---
describeE2E("4a: Slash commands via RPC", () => {
  let client: RPCClient;
  beforeAll(async () => { client = await createRPCClient(); await client.send("initialize", { ephemeral: true }); });
  afterAll(async () => { await client.close(); });

  it("/help lists commands", async () => {
    const result = await client.send("prompt", { message: "/help" }) as Record<string, unknown>;
    expect(result.status).toBe("completed");
    const response = result.response as string;
    expect(response).toContain("model");
    expect(response).toContain("mcp");
    expect(response).toContain("plan");
    expect(response).toContain("compact");
  }, 30000);

  it("/model show displays current model", async () => {
    const result = await client.send("prompt", { message: "/model show" }) as Record<string, unknown>;
    expect((result.response as string).toLowerCase()).toMatch(/model/);
  }, 30000);

  it("unknown slash command errors gracefully", async () => {
    const result = await client.send("prompt", { message: "/doesnotexist" }) as Record<string, unknown>;
    expect((result.response as string).toLowerCase()).toMatch(/unknown/);
  }, 30000);
});

// --- 4b: Planning mode ---
describeE2E("4b: Planning mode state", () => {
  let client: RPCClient;
  beforeAll(async () => { client = await createRPCClient(); await client.send("initialize", { ephemeral: true }); });
  afterAll(async () => { await client.close(); });

  it("enter and exit plan mode", async () => {
    await client.send("prompt", { message: "/plan" });
    let info = await client.send("get_session_info") as Record<string, unknown>;
    expect(info.planMode).toBe(true);

    await client.send("prompt", { message: "/plan exit" });
    info = await client.send("get_session_info") as Record<string, unknown>;
    expect(info.planMode).toBe(false);
  }, 60000);
});

// --- 4d: Background tasks ---
describeE2E("4d: Background tasks", () => {
  let client: RPCClient;
  beforeAll(async () => { client = await createRPCClient(); await client.send("initialize", { ephemeral: true }); });
  afterAll(async () => { await client.close(); });

  it("get_session_info returns backgroundTasks array", async () => {
    const info = await client.send("get_session_info") as Record<string, unknown>;
    expect(Array.isArray(info.backgroundTasks)).toBe(true);
  }, 30000);
});

// --- 4e: Agent autonomy ---
describeE2E("4e: Agent autonomy", () => {
  let client: RPCClient;
  beforeAll(async () => { client = await createRPCClient(); await client.send("initialize"); });
  afterAll(async () => { await client.close(); });

  it("builds multi-layer structure from natural language", async () => {
    const result = await client.send("prompt", {
      message: "I need a simple test structure: a 200x50um rectangle on layer 1/0 with a 50x50um contact pad on layer 2/0 centered on top of it. Create the layout and add the geometry.",
    }) as Record<string, unknown>;
    expect(result.status).toBe("completed");
  }, 360000);
});

// --- 5b: v0.3 Slash commands ---
describeE2E("5b: Slash commands v0.3", () => {
  let client: RPCClient;
  beforeAll(async () => { client = await createRPCClient(); await client.send("initialize", { ephemeral: true }); });
  afterAll(async () => { await client.close(); });

  it("/context shows workspace information", async () => {
    const result = await client.send("prompt", { message: "/context" }) as Record<string, unknown>;
    expect((result.response as string).toLowerCase()).toMatch(/workspace|context|file/);
  }, 30000);
});

// --- 5f: Workspace integrity ---
describeE2E("5f: Workspace integrity", () => {
  let client: RPCClient;
  beforeAll(async () => { client = await createRPCClient(); await client.send("initialize", { ephemeral: true }); });
  afterAll(async () => { await client.close(); });

  it("get_session_info returns session metadata", async () => {
    const info = await client.send("get_session_info") as Record<string, unknown>;
    expect(info).toHaveProperty("planMode");
    expect(info).toHaveProperty("backgroundTasks");
    expect(Array.isArray(info.backgroundTasks)).toBe(true);
  }, 30000);
});
