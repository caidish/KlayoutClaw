/**
 * qlaybot v0.4.4 Phase 5 Task 5.10 — Agent-side VC tool discovery.
 *
 * Two suites:
 *   1) Pure-function prefix test (always runs): feeds a fake nativeTools
 *      list containing the 9 vc_* bare names through KLayoutMCPClient's
 *      allTools() prefix wiring and asserts the correct
 *      `klayout_native_vc_*` names appear.
 *   2) Integration test (skip-if-unreachable): calls tools/list over HTTP
 *      JSON-RPC against the live plugin and asserts all 9 bare names are
 *      present.  Skipped when the server isn't reachable, matching the
 *      probeKLayout() pattern in test-e2e.ts.
 */

import { describe, it, expect } from "vitest";
import { KLayoutMCPClient } from "../src/mcp/klayout-client.js";
import type { MCPToolInfo } from "../src/mcp/types.js";

const EXPECTED_BARE_VC_TOOL_NAMES = [
  "vc_init",
  "vc_checkpoint",
  "vc_history",
  "vc_checkout",
  "vc_diff",
  "vc_branch",
  "vc_tag",
  "vc_export",
  "vc_status",
] as const;

const EXPECTED_NAMESPACED = EXPECTED_BARE_VC_TOOL_NAMES.map(
  (n) => `klayout_native_${n}`,
);

// ---------------------------------------------------------------------------
// Suite 1: Pure-function prefix wiring test (always runs)
// ---------------------------------------------------------------------------

describe("Phase 5 VC tools — prefix wiring (unit)", () => {
  /**
   * KLayoutMCPClient exposes `allTools()` which reads `this.nativeTools`
   * (populated by `discoverNativeTools()` during `connect()`).  For the
   * unit test we construct the client WITHOUT calling connect() and
   * inject a mock nativeTools list via a cast — no network, no server,
   * no fragile HTTP mocks.  This exercises the real allTools() body.
   */
  function makeClientWithMockNatives(natives: MCPToolInfo[]): KLayoutMCPClient {
    const client = new KLayoutMCPClient({ url: "http://127.0.0.1:0/mcp" });
    // Private field injection: we're asserting behaviour of the real
    // allTools() method, so we bypass connect() and inject the native tools
    // list directly.  The cast is the standard vitest unit-test pattern.
    (client as unknown as { nativeTools: MCPToolInfo[] }).nativeTools = natives;
    (client as unknown as { domainTools: unknown[] }).domainTools = [];
    return client;
  }

  it("prefixes all 9 vc_* bare names with klayout_native_", () => {
    const mockNatives: MCPToolInfo[] = EXPECTED_BARE_VC_TOOL_NAMES.map((name) => ({
      name,
      description: `mock ${name}`,
      inputSchema: { type: "object", properties: {} },
    }));

    const client = makeClientWithMockNatives(mockNatives);
    const tools = client.allTools();
    const names = tools.map((t) => t.name);

    for (const expected of EXPECTED_NAMESPACED) {
      expect(names).toContain(expected);
    }
  });

  it("allTools() preserves originalName for each vc_* tool", () => {
    const mockNatives: MCPToolInfo[] = EXPECTED_BARE_VC_TOOL_NAMES.map((name) => ({
      name,
      description: "",
      inputSchema: { type: "object", properties: {} },
    }));
    const client = makeClientWithMockNatives(mockNatives);
    const tools = client.allTools();

    for (const bare of EXPECTED_BARE_VC_TOOL_NAMES) {
      const namespaced = `klayout_native_${bare}`;
      const t = tools.find((x) => x.name === namespaced);
      expect(t, `tool ${namespaced} must exist after prefixing`).toBeDefined();
      expect(t!.originalName).toBe(bare);
      expect(t!.group).toBe("native");
      expect(t!.serverKey).toBe("klayout");
    }
  });

  it("allToolNames() returns all 9 prefixed names when vc_* natives are present", () => {
    const mockNatives: MCPToolInfo[] = EXPECTED_BARE_VC_TOOL_NAMES.map((name) => ({
      name,
      description: "",
      inputSchema: { type: "object", properties: {} },
    }));
    const client = makeClientWithMockNatives(mockNatives);
    const names = client.allToolNames();
    for (const expected of EXPECTED_NAMESPACED) {
      expect(names).toContain(expected);
    }
  });
});

// ---------------------------------------------------------------------------
// Suite 2: Live-server integration test (skip-if-unreachable)
// ---------------------------------------------------------------------------

const MCP_URL = process.env.KLAYOUT_MCP_URL ?? "http://127.0.0.1:8765/mcp";

async function probeKLayout(): Promise<boolean> {
  try {
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), 3000);
    const resp = await fetch(MCP_URL, { signal: controller.signal });
    clearTimeout(timer);
    if (!resp.ok) return false;
    const data = await resp.json() as { status?: unknown; server?: unknown };
    return data.status === "ok" && data.server === "KlayoutClaw";
  } catch {
    return false;
  }
}

const klayoutReachable = await probeKLayout();

if (!klayoutReachable) {
  console.log(
    `Phase 5 VC live integration test skipped: KlayoutClaw MCP not reachable at ${MCP_URL}`,
  );
}

const describeIntegration = klayoutReachable ? describe : describe.skip;

describeIntegration("Phase 5 VC tools — live tools/list (integration)", () => {
  it("every vc_* bare name appears in the plugin's tools/list", async () => {
    // Initialize session first (matches the test-phase4-mcp.py flow).
    const initResp = await fetch(MCP_URL, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        jsonrpc: "2.0",
        id: 1,
        method: "initialize",
        params: {
          protocolVersion: "2025-03-26",
          capabilities: {},
          clientInfo: { name: "phase5-vc-integration", version: "0.1" },
        },
      }),
    });
    expect(initResp.ok).toBe(true);
    const sessionId = initResp.headers.get("Mcp-Session-Id") ?? undefined;

    // Fetch tools/list.
    const headers: Record<string, string> = { "Content-Type": "application/json" };
    if (sessionId) headers["Mcp-Session-Id"] = sessionId;

    const listResp = await fetch(MCP_URL, {
      method: "POST",
      headers,
      body: JSON.stringify({ jsonrpc: "2.0", id: 2, method: "tools/list" }),
    });
    expect(listResp.ok).toBe(true);
    const listBody = (await listResp.json()) as {
      result: { tools: Array<{ name: string }> };
      error?: unknown;
    };
    expect(listBody.error).toBeUndefined();
    const names = listBody.result.tools.map((t) => t.name);

    for (const bare of EXPECTED_BARE_VC_TOOL_NAMES) {
      expect(
        names,
        `vc_* tool ${bare} must appear in live plugin tools/list. Got: ${JSON.stringify(names)}`,
      ).toContain(bare);
    }
  });
});
