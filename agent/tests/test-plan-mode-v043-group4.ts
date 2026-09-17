/**
 * qlaybot v0.4.3 — Group 4 TRD tests (Test Overseer owned).
 *
 * Covers spec §2 + §9 steps 12-13:
 *   Step 12: MCP image-block passthrough.
 *     - `MCPContentItem` widened to a discriminated union: text variant
 *       `{type:"text", text:string}` | image variant
 *       `{type:"image", source:{type:"base64", media_type:string, data:string}}`.
 *     - Real flattening site: `src/tools/index.ts::createMCPTools` (lines
 *       117-132). Image blocks MUST survive the wrapper — today they are
 *       filtered out.
 *     - `KLayoutMCPClient.callTool` + `MCPManager.callTool` already pass the
 *       raw MCPToolResult through, but we assert the round-trip anyway so a
 *       future refactor that adds flattening there is caught.
 *   Step 13: New `src/tui/image-render.ts` module
 *     (`detectImageRenderMode`, `base64DecodedSize`, `renderInlineImage`,
 *     `renderFallback`) + ToolPanel expanded-view integration that routes
 *     image blocks to iterm2 OSC 1337 when `TERM_PROGRAM` indicates
 *     iTerm-compatible (iTerm.app / WezTerm / vscode) AND decoded size <=
 *     2 MB, otherwise to a fallback tmp-file path.
 *
 * ---------------------------------------------------------------------------
 * Expected state BEFORE the Executor runs steps 12/13:
 *   - Image-render module tests: FAIL — module does not exist
 *     (`Cannot find module "../src/tui/image-render.js"`).
 *   - MCPContentItem discriminated-union test: FAIL — current type has flat
 *     `data?`, `mimeType?` and no `source`.
 *   - `createMCPTools` wrapper tests: FAIL — current wrapper filters
 *     `c.type === "text"` and drops image blocks.
 *   - KLayoutMCPClient / MCPManager round-trip tests: PASS today (already
 *     pass-through); keep as regression guards.
 *   - ToolPanel E2E expanded-view image rendering: FAIL — no image-routing
 *     code path exists in ToolPanel yet.
 *
 * That is the intended TRD shape: most tests fail on missing-impl and turn
 * green as the Executor completes steps 12/13.
 */

import {
  describe,
  it,
  expect,
  afterEach,
  beforeEach,
  vi,
} from "vitest";
import { execFileSync } from "child_process";
import { existsSync, mkdtempSync, readFileSync, rmSync, unlinkSync, writeFileSync } from "fs";
import { tmpdir } from "os";
import { join } from "path";
import React from "react";
import { render, cleanup } from "ink-testing-library";

import type { MCPContentItem, MCPToolResult, NamespacedTool } from "../src/mcp/types.js";
import { parseTextResult } from "../src/mcp/transport.js";
import { KLayoutMCPClient } from "../src/mcp/klayout-client.js";
import { MCPManager } from "../src/mcp/manager.js";
import { createMCPTools } from "../src/tools/index.js";
import { ToolPanel } from "../src/tui/components/ToolPanel.js";
import type { ToolExecution } from "../src/tui/types.js";

// Type-only import from the not-yet-existing image-render module. A runtime
// `import` below will ALSO hit it so missing-impl tests truly fail at collect.
import type {
  ImageRenderMode,
} from "../src/tui/image-render.js";

// Runtime import — triggers "Cannot find module" before Executor lands impl.
// Using dynamic import here keeps the rest of the file parseable even if the
// module is missing at collect-time; individual tests will fail on the awaited
// import.
async function loadImageRender(): Promise<{
  detectImageRenderMode: () => ImageRenderMode;
  base64DecodedSize: (b64: string) => number;
  renderInlineImage: (b64: string, opts?: { width?: string; height?: string }) => string;
  renderFallback: (b64: string, outDir: string, mediaType?: string) => string;
}> {
  // eslint-disable-next-line @typescript-eslint/ban-ts-comment
  // @ts-ignore — module may not exist yet; tests should fail loudly if so.
  return await import("../src/tui/image-render.js");
}

// ─────────────────────────────────────────────────────────────────────────────
// Shared fixtures
// ─────────────────────────────────────────────────────────────────────────────

/** 4-byte PNG magic signature (0x89 "PNG") base64-encoded. */
const PNG_MAGIC_B64 = Buffer.from([0x89, 0x50, 0x4e, 0x47]).toString("base64");

/** Well-formed image block matching the new Anthropic-style nested schema. */
function makeImageBlock(
  data: string = PNG_MAGIC_B64,
  media_type: string = "image/png",
): MCPContentItem {
  return {
    type: "image",
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    source: { type: "base64", media_type, data } as any,
  } as MCPContentItem;
}

/** Well-formed text block. */
function makeTextBlock(text: string): MCPContentItem {
  return { type: "text", text };
}

/**
 * Install a mock fetch that responds to MCP JSON-RPC calls:
 *   - `initialize`  → returns `{protocolVersion, ...}` + Mcp-Session-Id header
 *   - `tools/list`  → returns {tools: []}
 *   - `tools/call`  → returns the supplied toolResult
 * This is HTTP-level mocking — NOT mocking the SUT (KLayoutMCPClient / MCPManager
 * / createMCPTools).
 */
function installMockFetch(toolResult: MCPToolResult) {
  const mock = vi.fn(async (_url: string | URL, init?: RequestInit) => {
    const body = JSON.parse(String(init?.body ?? "{}"));
    const method = body.method as string;
    let result: unknown;
    if (method === "initialize") {
      result = { protocolVersion: "2025-03-26", capabilities: {} };
    } else if (method === "tools/list") {
      result = { tools: [] };
    } else if (method === "tools/call") {
      result = toolResult;
    } else {
      result = {};
    }
    const headers = new Map<string, string>([["Mcp-Session-Id", "test-session-1"]]);
    return {
      ok: true,
      status: 200,
      statusText: "OK",
      headers: {
        get: (k: string) => headers.get(k) ?? null,
      },
      json: async () => ({ jsonrpc: "2.0", id: body.id, result }),
    } as unknown as Response;
  });
  vi.stubGlobal("fetch", mock);
  return mock;
}

/** Create a ToolExecution with an arbitrary MCP tool result payload. */
function makeTool(result: unknown, overrides: Partial<ToolExecution> = {}): ToolExecution {
  return {
    id: "tool-img-1",
    toolName: "klayout_native_screenshot",
    args: { filepath: "/tmp/x.png" },
    status: "completed",
    result,
    startTime: 1000,
    endTime: 1500,
    ...overrides,
  };
}

// ─────────────────────────────────────────────────────────────────────────────
// Env + render cleanup
// ─────────────────────────────────────────────────────────────────────────────

const _savedTermProgram = process.env.TERM_PROGRAM;

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  if (_savedTermProgram === undefined) delete process.env.TERM_PROGRAM;
  else process.env.TERM_PROGRAM = _savedTermProgram;
});

// ─────────────────────────────────────────────────────────────────────────────
// Step 12 — MCP passthrough tests
// ─────────────────────────────────────────────────────────────────────────────

describe("Group 4 · Step 12 · MCPContentItem discriminated union (source-level)", () => {
  // These tests compile-gate the Executor via `tsc --noEmit` on a scratch
  // file that imports `MCPContentItem` from src/mcp/types.ts and runs
  // `satisfies` assertions. The scratch file uses `// @ts-expect-error`
  // comments — if the expected-to-fail forms stop failing (i.e. the type
  // loosens), tsc flags the unused directive and the build fails.
  //
  // This is a REAL structural gate — not a regex loophole. An Executor that
  // leaves a legacy flat `{data?, mimeType?}` anywhere at the top level of
  // the union will cause the tsc spawn to exit non-zero because either:
  //   (a) the positive-case `satisfies` blocks fail (nested source missing), or
  //   (b) the `@ts-expect-error` directives go unused (flat fields still valid).
  const TYPES_PATH = new URL("../src/mcp/types.ts", import.meta.url).pathname;
  const TSC_PATH = new URL("../node_modules/.bin/tsc", import.meta.url).pathname;
  const AGENT_ROOT = new URL("..", import.meta.url).pathname;

  /** Run `tsc --noEmit` on a scratch file; return { ok, output }. */
  function runTscOnScratch(scratchContents: string): { ok: boolean; output: string } {
    const scratchDir = mkdtempSync(join(tmpdir(), "qlaybot-tsc-"));
    const scratchPath = join(scratchDir, "scratch.ts");
    try {
      writeFileSync(scratchPath, scratchContents, "utf8");
      // Mark the scratch dir as ESM so Node16 module resolution treats the
      // `.ts` file as an ES module — matches the agent project's setup and
      // avoids spurious TS1541 errors when importing from src/mcp/types.ts.
      writeFileSync(
        join(scratchDir, "package.json"),
        JSON.stringify({ type: "module" }),
        "utf8",
      );
      // Minimal tsconfig matching the project's strict Node16 settings so we
      // exercise the same type-checking the real build uses.
      const tsconfigPath = join(scratchDir, "tsconfig.json");
      writeFileSync(
        tsconfigPath,
        JSON.stringify({
          compilerOptions: {
            target: "ES2022",
            module: "Node16",
            moduleResolution: "Node16",
            strict: true,
            esModuleInterop: true,
            skipLibCheck: true,
            noEmit: true,
            resolveJsonModule: true,
          },
          include: [scratchPath],
        }),
        "utf8",
      );
      try {
        const stdout = execFileSync(
          TSC_PATH,
          ["--noEmit", "--project", tsconfigPath],
          { cwd: AGENT_ROOT, encoding: "utf8", stdio: ["ignore", "pipe", "pipe"] },
        );
        return { ok: true, output: stdout };
      } catch (err: unknown) {
        const e = err as { stdout?: Buffer | string; stderr?: Buffer | string };
        const out =
          (typeof e.stdout === "string"
            ? e.stdout
            : e.stdout?.toString?.() ?? "") +
          (typeof e.stderr === "string"
            ? e.stderr
            : e.stderr?.toString?.() ?? "");
        return { ok: false, output: out };
      }
    } finally {
      try {
        rmSync(scratchDir, { recursive: true, force: true });
      } catch {
        /* best-effort */
      }
    }
  }

  it("types.ts: tsc satisfies both variants (text + nested-image) AND rejects legacy flat shape", () => {
    // Sanity: source file exists (fast-fail if workspace is broken).
    expect(existsSync(TYPES_PATH)).toBe(true);

    // Scratch uses a relative import path that points back to src/mcp/types.ts
    // from the temp scratch dir — since TSC resolves relative paths against
    // the scratch file itself, we use the absolute file URL converted to a
    // project-relative import via AGENT_ROOT + "src/mcp/types.js".
    // Node16 moduleResolution requires .js extension on relative imports.
    const typesImportPath = join(AGENT_ROOT, "src", "mcp", "types.js");
    // Build scratch contents as a joined string (not a template literal)
    // so inline backticks in @ts-expect-error comments don't terminate
    // the outer template. esbuild parses the file at test-collection time
    // and is strict about backticks inside template literals.
    const scratchLines = [
      "import type { MCPContentItem } from " + JSON.stringify(typesImportPath) + ";",
      "",
      "// Positive case 1: text variant compiles.",
      'const _text: MCPContentItem = { type: "text", text: "hello" };',
      "",
      "// Positive case 2: nested-image variant compiles (Anthropic shape).",
      "const _image: MCPContentItem = {",
      '  type: "image",',
      '  source: { type: "base64", media_type: "image/png", data: "abc" },',
      "};",
      "",
      "// Negative case: legacy flat shape must NOT compile. If tsc accepts it",
      "// (i.e. the Executor kept flat fields on the union), @ts-expect-error is",
      "// unused and tsc emits TS2578 — turning this scratch compile into an error.",
      "// @ts-expect-error legacy flat shape must not satisfy MCPContentItem",
      'const _legacy: MCPContentItem = { type: "image", data: "abc", mimeType: "image/png" };',
      "",
      "// Negative case: missing nested source on image variant must NOT compile.",
      "// @ts-expect-error image variant without source field must be rejected",
      'const _noSource: MCPContentItem = { type: "image" };',
      "",
      '// Silence "unused" lint from the scratch compile.',
      "void _text; void _image; void _legacy; void _noSource;",
      "",
    ];
    const scratch = scratchLines.join("\n");
    const { ok, output } = runTscOnScratch(scratch);
    expect(
      ok,
      `tsc --noEmit failed on MCPContentItem scratch. Output:\n${output}`,
    ).toBe(true);
  }, 30_000);

  it("types.ts: MCPContentItem declaration has NO top-level flat `data?` / `mimeType?` (brace-balanced parse)", () => {
    const src = readFileSync(TYPES_PATH, "utf8");

    // Find the start of the MCPContentItem declaration.
    const startMatch = src.match(/export\s+(?:interface|type)\s+MCPContentItem\b/);
    expect(startMatch, "MCPContentItem declaration not found in types.ts").not.toBeNull();
    const startIdx = startMatch!.index!;

    // Capture the ENTIRE declaration up to the first statement terminator at
    // brace depth 0. This handles all three shapes:
    //   (a) `export interface MCPContentItem { ... }`       — no terminating `;`
    //   (b) `export type MCPContentItem = { ... };`          — single variant
    //   (c) `export type MCPContentItem = {A} | {B} | {C};`  — discriminated union
    //
    // The round-2 parser stopped at the first balanced `}` which, for case
    // (c), missed every variant after the first — an Executor could leave
    // `data?`/`mimeType?` on later variants and this check would not notice.
    // Scanning to the first top-level `;` (or end-of-file for case (a))
    // guarantees we see every variant of the union.
    let depth = 0;
    let endIdx = -1;
    let sawBrace = false;
    for (let i = startIdx; i < src.length; i++) {
      const ch = src[i];
      if (ch === "{") {
        depth++;
        sawBrace = true;
      } else if (ch === "}") {
        depth--;
        // For an `interface` declaration there is no terminating `;`. The end
        // of the block is the matching `}` at depth 0.
        if (depth === 0 && sawBrace) {
          // Peek ahead: if the next non-whitespace char is `;`, keep going
          // (this could be a type-alias closing brace). Otherwise, if the
          // next non-whitespace is a union pipe `|`, we have more variants
          // to scan. Otherwise (end of interface), stop here.
          let j = i + 1;
          while (j < src.length && /\s/.test(src[j]!)) j++;
          const next = src[j];
          if (next !== ";" && next !== "|") {
            endIdx = i + 1;
            break;
          }
          // else: fall through — the loop keeps scanning for the top-level `;`.
        }
      } else if (ch === ";" && depth === 0 && sawBrace) {
        endIdx = i + 1;
        break;
      }
    }
    expect(
      endIdx,
      "could not determine end of MCPContentItem declaration",
    ).toBeGreaterThan(-1);
    const block = src.slice(startIdx, endIdx);

    // Remove all nested `source: { ... }` blocks — legitimate place for `data: string`.
    // Brace-balanced strip to guarantee we don't just skip the first line.
    function stripSourceBlocks(s: string): string {
      let out = "";
      let i = 0;
      while (i < s.length) {
        const m = s.slice(i).match(/source\s*:\s*\{/);
        if (!m) {
          out += s.slice(i);
          break;
        }
        const absStart = i + m.index!;
        out += s.slice(i, absStart);
        // Walk brace balance from the `{` at absStart + m[0].length - 1.
        const braceIdx = absStart + m[0].length - 1;
        let d = 0;
        let end = -1;
        for (let j = braceIdx; j < s.length; j++) {
          if (s[j] === "{") d++;
          else if (s[j] === "}") {
            d--;
            if (d === 0) {
              end = j + 1;
              break;
            }
          }
        }
        if (end < 0) {
          // Unbalanced — treat rest as stripped to be conservative.
          break;
        }
        i = end;
      }
      return out;
    }

    const withoutSource = stripSourceBlocks(block);

    // After stripping nested `source: {...}` blocks, NEITHER `data?:` nor
    // `mimeType?:` nor the non-optional legacy forms may appear anywhere in
    // the MCPContentItem declaration.
    expect(
      withoutSource,
      "MCPContentItem still exposes a top-level `data` field outside of `source: {...}`",
    ).not.toMatch(/\bdata\s*\??\s*:\s*string/);
    expect(
      withoutSource,
      "MCPContentItem still exposes a top-level `mimeType` field",
    ).not.toMatch(/\bmimeType\s*\??\s*:\s*string/);
  });
});

describe("Group 4 · Step 12 · KLayoutMCPClient.callTool round-trip", () => {
  it("T43-a: image-only result — content[0].type === 'image' with nested source", async () => {
    const image = makeImageBlock(PNG_MAGIC_B64, "image/png");
    installMockFetch({ content: [image], isError: false });

    const client = new KLayoutMCPClient({ url: "http://127.0.0.1:8765/mcp" });
    await client.connect();

    const result = await client.callTool("klayout_native_screenshot", {});
    expect(result.content).toHaveLength(1);
    expect(result.content[0].type).toBe("image");
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    const src = (result.content[0] as any).source;
    expect(src).toBeDefined();
    expect(src.type).toBe("base64");
    expect(src.media_type).toBe("image/png");
    expect(src.data).toBe(PNG_MAGIC_B64);
  });

  it("mixed text+image+text: order preserved, image block intact at index 1", async () => {
    const image = makeImageBlock(PNG_MAGIC_B64, "image/png");
    installMockFetch({
      content: [makeTextBlock("before"), image, makeTextBlock("after")],
      isError: false,
    });

    const client = new KLayoutMCPClient({ url: "http://127.0.0.1:8765/mcp" });
    await client.connect();
    const result = await client.callTool("klayout_native_screenshot", {});

    expect(result.content).toHaveLength(3);
    expect(result.content[0].type).toBe("text");
    expect(result.content[0].text).toBe("before");
    expect(result.content[1].type).toBe("image");
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    expect((result.content[1] as any).source.data).toBe(PNG_MAGIC_B64);
    expect(result.content[2].type).toBe("text");
    expect(result.content[2].text).toBe("after");
  });
});

describe("KLayoutMCPClient.healthCheck identity guards", () => {
  it("accepts a valid get_layout_info tool result", async () => {
    installMockFetch({
      content: [{ type: "text", text: JSON.stringify({ status: "ok" }) }],
      isError: false,
    });
    const client = new KLayoutMCPClient({ url: "http://127.0.0.1:8765/mcp" });
    expect(await client.healthCheck()).toBe(true);
  });

  it("rejects an AnkiConnect-shaped result:null HTTP 200", async () => {
    installMockFetch(null as unknown as MCPToolResult);
    const client = new KLayoutMCPClient({ url: "http://127.0.0.1:8765/mcp" });
    expect(await client.healthCheck()).toBe(false);
  });

  it("rejects a malformed tool result", async () => {
    installMockFetch({ content: [], isError: false });
    const client = new KLayoutMCPClient({ url: "http://127.0.0.1:8765/mcp" });
    expect(await client.healthCheck()).toBe(false);
  });
});

describe("Group 4 · Step 12 · MCPManager.callTool round-trip", () => {
  it("T43-b: image-only result survives through MCPManager for klayout namespace", async () => {
    const image = makeImageBlock(PNG_MAGIC_B64, "image/png");
    installMockFetch({ content: [image], isError: false });

    const manager = new MCPManager({
      servers: {
        klayout: { url: "http://127.0.0.1:8765/mcp", required: true },
      },
    });
    await manager.connectAll();

    const result = await manager.callTool("klayout_native_screenshot", {});
    expect(result.content).toHaveLength(1);
    expect(result.content[0].type).toBe("image");
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    expect((result.content[0] as any).source.data).toBe(PNG_MAGIC_B64);
  });
});

describe("Group 4 · Step 12 · createMCPTools wrapper (the REAL flattening site)", () => {
  /** Minimal MCPManager double: only exposes `callTool`. NOT mocking createMCPTools itself. */
  function fakeManager(result: MCPToolResult): MCPManager {
    return {
      callTool: vi.fn().mockResolvedValue(result),
    } as unknown as MCPManager;
  }

  const nt: NamespacedTool = {
    name: "klayout_native_screenshot",
    originalName: "screenshot",
    serverKey: "klayout",
    group: "native",
    description: "Capture viewport",
    inputSchema: { type: "object", properties: {} },
  };

  it("image-only content: wrapper returns EXACTLY [imageBlock] — no synthesized text, no extra blocks", async () => {
    const image = makeImageBlock(PNG_MAGIC_B64, "image/png");
    const mgr = fakeManager({ content: [image], isError: false });

    const [tool] = createMCPTools(mgr, [nt]);
    const out = await tool.execute("call-1", {});

    // Exact-shape assertion: a wrapper that prepends a synthesized text
    // fallback, reorders blocks, or duplicates the image block all fail
    // this toEqual. Today's code filters image blocks out and synthesises
    // `[{type:"text", text: JSON.stringify(result)}]` — that behaviour is
    // exactly what this test guards against.
    expect(out.content).toEqual([
      {
        type: "image",
        source: {
          type: "base64",
          media_type: "image/png",
          data: PNG_MAGIC_B64,
        },
      },
    ]);
    // Belt + suspenders: length exactly 1.
    expect(out.content.length).toBe(1);
    // No synthesized JSON fallback text should ever appear when `content`
    // is non-empty. An Executor that appends a JSON.stringify(result)
    // fallback "just in case" fails here.
    const asJson = JSON.stringify(out.content);
    expect(asJson).not.toContain("isError");
    expect(asJson).not.toContain("JSON.stringify");
  });

  it("mixed text+image content: wrapper returns EXACTLY [text, image] — order preserved, no extras", async () => {
    const image = makeImageBlock(PNG_MAGIC_B64, "image/png");
    const mgr = fakeManager({
      content: [makeTextBlock("caption"), image],
      isError: false,
    });

    const [tool] = createMCPTools(mgr, [nt]);
    const out = await tool.execute("call-1", {});

    // Exact shape + order. A wrapper that reorders, duplicates, or
    // prepends/appends a synthesized block fails this assertion.
    expect(out.content).toEqual([
      { type: "text", text: "caption" },
      {
        type: "image",
        source: {
          type: "base64",
          media_type: "image/png",
          data: PNG_MAGIC_B64,
        },
      },
    ]);
    expect(out.content.length).toBe(2);
    // No synthesized JSON fallback text when `content` is non-empty.
    const asJson = JSON.stringify(out.content);
    expect(asJson).not.toContain("isError");
  });

  it("mixed text+image+text content: wrapper returns EXACTLY [text, image, text] — order preserved, trailing block kept", async () => {
    // Round-2 review gap: the two-block mixed test above would pass even if
    // the wrapper dropped every block AFTER the first image (i.e. a buggy
    // impl that stops iterating on first image). A three-block fixture
    // (text, image, text) catches the "dropped trailing blocks" bug while
    // also re-asserting order preservation.
    const image = makeImageBlock(PNG_MAGIC_B64, "image/png");
    const mgr = fakeManager({
      content: [
        makeTextBlock("first"),
        image,
        makeTextBlock("last"),
      ],
      isError: false,
    });

    const [tool] = createMCPTools(mgr, [nt]);
    const out = await tool.execute("call-1", {});

    // Exact-shape, exact-order assertion. Any wrapper that drops the
    // trailing text, reorders blocks, or inserts extras fails this.
    expect(out.content).toEqual([
      { type: "text", text: "first" },
      {
        type: "image",
        source: {
          type: "base64",
          media_type: "image/png",
          data: PNG_MAGIC_B64,
        },
      },
      { type: "text", text: "last" },
    ]);
    expect(out.content.length).toBe(3);

    // Belt + suspenders: the final block must be the post-image text, not
    // a synthesized JSON fallback or a duplicate of the pre-image text.
    const tail = out.content[2] as { type: string; text?: string };
    expect(tail.type).toBe("text");
    expect(tail.text).toBe("last");
  });

  it("empty content: wrapper still synthesises a text fallback (backward compat)", async () => {
    const mgr = fakeManager({ content: [], isError: false });
    const [tool] = createMCPTools(mgr, [nt]);
    const out = await tool.execute("call-1", {});

    // With no text and no image, the existing behaviour returns a single
    // text block whose body is JSON.stringify(result). That fallback must
    // stay.
    expect(Array.isArray(out.content)).toBe(true);
    expect(out.content.length).toBe(1);
    const only = out.content[0] as { type: string; text?: string };
    expect(only.type).toBe("text");
    expect(typeof only.text).toBe("string");
    expect(only.text!.length).toBeGreaterThan(0);
  });

  it("details still carries the raw MCPToolResult for backward compat", async () => {
    const image = makeImageBlock(PNG_MAGIC_B64, "image/png");
    const raw: MCPToolResult = {
      content: [makeTextBlock("hi"), image],
      isError: false,
    };
    const mgr = fakeManager(raw);
    const [tool] = createMCPTools(mgr, [nt]);
    const out = await tool.execute("call-1", {});

    expect(out.details).toBeDefined();
    expect(out.details).toEqual(raw);
  });
});

describe("Group 4 · Step 12 · parseTextResult ignores image blocks", () => {
  it("returns only concatenated text, no crash on image block", () => {
    const image = makeImageBlock(PNG_MAGIC_B64, "image/png");
    const result: MCPToolResult = {
      content: [makeTextBlock("line1"), image, makeTextBlock("line2")],
      isError: false,
    };
    const text = parseTextResult(result);
    expect(text).toBe("line1\nline2");
  });
});

// ─────────────────────────────────────────────────────────────────────────────
// Step 13 — image-render module tests
// ─────────────────────────────────────────────────────────────────────────────

describe("Group 4 · Step 13 · detectImageRenderMode", () => {
  it("returns 'iterm2' for TERM_PROGRAM = iTerm.app", async () => {
    const m = await loadImageRender();
    process.env.TERM_PROGRAM = "iTerm.app";
    expect(m.detectImageRenderMode()).toBe("iterm2");
  });

  it("returns 'iterm2' for TERM_PROGRAM = WezTerm", async () => {
    const m = await loadImageRender();
    process.env.TERM_PROGRAM = "WezTerm";
    expect(m.detectImageRenderMode()).toBe("iterm2");
  });

  it("returns 'iterm2' for TERM_PROGRAM = vscode", async () => {
    const m = await loadImageRender();
    process.env.TERM_PROGRAM = "vscode";
    expect(m.detectImageRenderMode()).toBe("iterm2");
  });

  it("returns 'fallback' for TERM_PROGRAM = xterm-256color", async () => {
    const m = await loadImageRender();
    process.env.TERM_PROGRAM = "xterm-256color";
    expect(m.detectImageRenderMode()).toBe("fallback");
  });

  it("returns 'fallback' when TERM_PROGRAM is unset/missing", async () => {
    const m = await loadImageRender();
    delete process.env.TERM_PROGRAM;
    expect(m.detectImageRenderMode()).toBe("fallback");
  });
});

describe("Group 4 · Step 13 · base64DecodedSize", () => {
  it("empty string → 0", async () => {
    const m = await loadImageRender();
    expect(m.base64DecodedSize("")).toBe(0);
  });

  it("no padding (length 8) → 6 bytes", async () => {
    const m = await loadImageRender();
    // "SGVsbG8h" = "Hello!" (6 bytes) has no padding.
    expect(m.base64DecodedSize("SGVsbG8h")).toBe(6);
  });

  it("one '=' padding (length 8) → 5 bytes", async () => {
    const m = await loadImageRender();
    // "SGVsbG8=" = "Hello" (5 bytes) has 1 padding char.
    expect(m.base64DecodedSize("SGVsbG8=")).toBe(5);
  });

  it("two '==' padding (length 8) → 4 bytes", async () => {
    const m = await loadImageRender();
    // "SGVsbA==" = "Hell" (4 bytes) has 2 padding chars.
    expect(m.base64DecodedSize("SGVsbA==")).toBe(4);
  });
});

describe("Group 4 · Step 13 · renderInlineImage", () => {
  it("produces iTerm2 OSC 1337 escape with defaults width=auto height=auto", async () => {
    const m = await loadImageRender();
    const out = m.renderInlineImage(PNG_MAGIC_B64);

    expect(out.startsWith("\x1b]1337;File=inline=1;")).toBe(true);
    expect(out.endsWith("\x07")).toBe(true);
    expect(out).toContain("width=auto");
    expect(out).toContain("height=auto");
    expect(out).toContain("preserveAspectRatio=1");
    expect(out).toContain(PNG_MAGIC_B64);
  });
});

describe("Group 4 · Step 13 · renderFallback", () => {
  const written: string[] = [];

  afterEach(() => {
    while (written.length > 0) {
      const p = written.pop()!;
      try {
        if (existsSync(p)) unlinkSync(p);
      } catch {
        /* best-effort cleanup */
      }
    }
  });

  it("writes PNG file with valid magic bytes, returns '[image saved: <abs>]'", async () => {
    const m = await loadImageRender();
    const msg = m.renderFallback(PNG_MAGIC_B64, tmpdir(), "image/png");

    // Format: "[image saved: /abs/path/qlaybot-img-*.png]"
    const match = msg.match(/^\[image saved: (.+\.png)\]$/);
    expect(match, `expected "[image saved: ...path.png]" got: ${msg}`).not.toBeNull();
    const path = match![1];
    expect(path.startsWith("/")).toBe(true);
    expect(path).toContain("qlaybot-img-");
    written.push(path);

    expect(existsSync(path)).toBe(true);
    const raw = readFileSync(path);
    expect(raw.length).toBe(4);
    expect(raw[0]).toBe(0x89);
    expect(raw[1]).toBe(0x50); // P
    expect(raw[2]).toBe(0x4e); // N
    expect(raw[3]).toBe(0x47); // G
  });

  it("image/jpeg → filename ends in .jpg", async () => {
    const m = await loadImageRender();
    const msg = m.renderFallback(PNG_MAGIC_B64, tmpdir(), "image/jpeg");
    const match = msg.match(/^\[image saved: (.+)\]$/);
    expect(match).not.toBeNull();
    const path = match![1];
    written.push(path);
    expect(path.endsWith(".jpg")).toBe(true);
  });

  it("unknown media type → filename ends in .bin", async () => {
    const m = await loadImageRender();
    const msg = m.renderFallback(PNG_MAGIC_B64, tmpdir(), "application/x-unknown");
    const match = msg.match(/^\[image saved: (.+)\]$/);
    expect(match).not.toBeNull();
    const path = match![1];
    written.push(path);
    expect(path.endsWith(".bin")).toBe(true);
  });
});

// ─────────────────────────────────────────────────────────────────────────────
// Step 13 — ToolPanel E2E integration (T28 / T29 / T30)
// ─────────────────────────────────────────────────────────────────────────────

// Image rendering in ToolPanel was removed to stop long-session OOM —
// base64 screenshots stayed pinned in reducer state forever and drove the
// heap to 4GB. Images are now replaced with a `[image omitted: …]` text
// placeholder by truncateToolResult in the reducer, and ToolPanel renders
// text only. The image-render module itself is still tested above.
describe.skip("Group 4 · Step 13 · ToolPanel expanded view · image rendering", () => {
  /** Collect tmp files written by fallback renders so we can clean up. */
  const savedFallbackPaths: string[] = [];
  afterEach(() => {
    while (savedFallbackPaths.length > 0) {
      const p = savedFallbackPaths.pop()!;
      try {
        if (existsSync(p)) unlinkSync(p);
      } catch {
        /* best-effort cleanup */
      }
    }
  });

  it("T28: iTerm2 → frame contains OSC 1337 inline-image escape", () => {
    process.env.TERM_PROGRAM = "iTerm.app";
    const image = makeImageBlock(PNG_MAGIC_B64, "image/png");
    const tool = makeTool({ content: [image], isError: false });

    const instance = render(
      React.createElement(ToolPanel, { tool, expanded: true }),
    );
    const frame = instance.lastFrame() ?? "";
    instance.unmount();

    expect(frame).toContain("\x1b]1337;File=inline=1;");
    expect(frame).toContain(PNG_MAGIC_B64);
  });

  it("T29: fallback → frame contains '[image saved: ...]' with an existing .png file", () => {
    process.env.TERM_PROGRAM = "xterm-256color";
    const image = makeImageBlock(PNG_MAGIC_B64, "image/png");
    const tool = makeTool({ content: [image], isError: false });

    const instance = render(
      React.createElement(ToolPanel, { tool, expanded: true }),
    );
    const frame = instance.lastFrame() ?? "";
    instance.unmount();

    expect(frame).toContain("[image saved: ");
    const m = frame.match(/\[image saved: (\S+?\.png)\]/);
    expect(m, `no fallback path in frame: ${frame}`).not.toBeNull();
    const path = m![1];
    savedFallbackPaths.push(path);
    expect(existsSync(path)).toBe(true);
    // MUST NOT contain the iterm2 escape when in fallback mode.
    expect(frame).not.toContain("\x1b]1337;File=inline=1;");
  });

  it("T30: iTerm2 + decoded size > 2 MB → falls back (no OSC 1337)", () => {
    process.env.TERM_PROGRAM = "iTerm.app";
    // 2_700_000 base64 chars → decoded ≈ 2_025_000 bytes > 2 MB boundary.
    const big = "A".repeat(2_700_000);
    const image = makeImageBlock(big, "image/png");
    const tool = makeTool({ content: [image], isError: false });

    const instance = render(
      React.createElement(ToolPanel, { tool, expanded: true }),
    );
    const frame = instance.lastFrame() ?? "";
    instance.unmount();

    expect(frame).not.toContain("\x1b]1337;File=inline=1;");
    expect(frame).toContain("[image saved: ");
    const m = frame.match(/\[image saved: (\S+?\.png)\]/);
    if (m) savedFallbackPaths.push(m[1]);
  }, 30_000);

  it("T30-b (boundary): decoded size <= 2_000_000 renders iterm2; > 2_000_000 falls back — BOTH rendered", async () => {
    // Boundary is expressed in the impl as a numeric cap of 2_000_000 bytes
    // (spec §2.3). We test BOTH sides of the threshold by rendering through
    // ToolPanel, not just measuring. This catches an off-by-one cap at
    // 2_000_001 (the round-1 version of this test did not render `overStr`,
    // so such a bug passed undetected).
    const m = await loadImageRender();

    // Build base64 strings whose decoded sizes straddle the 2 MB boundary.
    // decoded = floor(L*3/4) - pad_count.
    //   L = 2_666_668, pad="==" → 3L/4 = 2_000_001 → decoded = 1_999_999 (under)
    //   L = 2_666_672, pad="==" → 3L/4 = 2_000_004 → decoded = 2_000_002 (over)
    const underStr = "A".repeat(2_666_666) + "==";
    const overStr = "A".repeat(2_666_670) + "==";
    const underSize = m.base64DecodedSize(underStr);
    const overSize = m.base64DecodedSize(overStr);
    expect(underSize).toBeLessThanOrEqual(2_000_000);
    expect(overSize).toBeGreaterThan(2_000_000);
    // Spacing around the boundary is tight but adequate: both within ~5
    // bytes of 2_000_000, so an off-by-N cap with |N| <= underSize's gap
    // will still be caught by at least one of the two renders below.

    // --- Under branch: must use iterm2 OSC 1337. ---
    process.env.TERM_PROGRAM = "iTerm.app";
    const imageUnder = makeImageBlock(underStr, "image/png");
    const toolUnder = makeTool({ content: [imageUnder], isError: false });
    const underInstance = render(
      React.createElement(ToolPanel, { tool: toolUnder, expanded: true }),
    );
    const frameUnder = underInstance.lastFrame() ?? "";
    underInstance.unmount();
    expect(
      frameUnder,
      `under-threshold (${underSize} bytes) should render iterm2 OSC 1337`,
    ).toContain("\x1b]1337;File=inline=1;");
    expect(
      frameUnder,
      `under-threshold (${underSize} bytes) must NOT fall back`,
    ).not.toContain("[image saved: ");

    // --- Over branch: must fall back (no OSC 1337). This catches an
    //     off-by-one cap at 2_000_001 — the key weakness the round-1
    //     version of this test missed. ---
    process.env.TERM_PROGRAM = "iTerm.app";
    const imageOver = makeImageBlock(overStr, "image/png");
    const toolOver = makeTool({ content: [imageOver], isError: false });
    const overInstance = render(
      React.createElement(ToolPanel, { tool: toolOver, expanded: true }),
    );
    const frameOver = overInstance.lastFrame() ?? "";
    overInstance.unmount();
    expect(
      frameOver,
      `over-threshold (${overSize} bytes) must fall back (no OSC 1337)`,
    ).not.toContain("\x1b]1337;File=inline=1;");
    expect(
      frameOver,
      `over-threshold (${overSize} bytes) must render fallback marker`,
    ).toContain("[image saved: ");
    const fbMatch = frameOver.match(/\[image saved: (\S+?\.png)\]/);
    if (fbMatch) savedFallbackPaths.push(fbMatch[1]);
  }, 60_000);

  it("mixed text+image+text expanded view: frame contains each text AND inline escape in ORIGINAL order (iTerm2)", () => {
    process.env.TERM_PROGRAM = "iTerm.app";
    const image = makeImageBlock(PNG_MAGIC_B64, "image/png");
    const tool = makeTool({
      content: [
        makeTextBlock("before-marker-42"),
        image,
        makeTextBlock("after-marker-99"),
      ],
      isError: false,
    });

    const instance = render(
      React.createElement(ToolPanel, { tool, expanded: true }),
    );
    const frame = instance.lastFrame() ?? "";
    instance.unmount();

    expect(frame).toContain("before-marker-42");
    expect(frame).toContain("after-marker-99");
    expect(frame).toContain("\x1b]1337;File=inline=1;");

    // Spec §2.4 mandates iteration order (text, image, text). Assert the
    // three markers appear in the original order in the rendered frame.
    const idxBefore = frame.indexOf("before-marker-42");
    const idxImage = frame.indexOf("\x1b]1337;File=inline=1;");
    const idxAfter = frame.indexOf("after-marker-99");
    expect(idxBefore).toBeGreaterThanOrEqual(0);
    expect(idxImage).toBeGreaterThanOrEqual(0);
    expect(idxAfter).toBeGreaterThanOrEqual(0);
    expect(
      idxBefore,
      `expected "before-marker-42" (idx=${idxBefore}) before image OSC 1337 (idx=${idxImage})`,
    ).toBeLessThan(idxImage);
    expect(
      idxImage,
      `expected image OSC 1337 (idx=${idxImage}) before "after-marker-99" (idx=${idxAfter})`,
    ).toBeLessThan(idxAfter);
  });

  it("compact mode (expanded=false) IMAGE-ONLY: NO image escape, NO fallback marker, NO base64 leak (spec §2.5)", () => {
    process.env.TERM_PROGRAM = "iTerm.app";
    // 20-char segment guard catches medium-to-large raw-base64 leaks.
    // JSON-structural negatives below close the short-prefix-dump gap
    // (e.g. a JSON.stringify(result) truncated to ≤60 chars would fit
    // under the 20-char segment tripwire but still leak structural JSON).
    const PAYLOAD =
      "B".repeat(50) + "C".repeat(50) + "D".repeat(50) + "E".repeat(650); // 800 chars total
    const image = makeImageBlock(PAYLOAD, "image/png");
    const tool = makeTool({ content: [image], isError: false });

    const instance = render(
      React.createElement(ToolPanel, { tool, expanded: false }),
    );
    const frame = instance.lastFrame() ?? "";
    instance.unmount();

    // Hard negative 1: compact view must NEVER emit the iterm2 escape OR the
    // fallback saved-file marker. That is the load-bearing invariant.
    expect(frame).not.toContain("\x1b]1337;File=inline=1;");
    expect(frame).not.toContain("[image saved: ");

    // Hard negative 2: NO raw base64 leak at 20-char granularity — catches
    // both full and partial (truncated) leaks of any segment. A bare
    // JSON.stringify(result) OR a leak of the first N chars followed by "..."
    // both trip this.
    expect(
      frame,
      "compact frame leaked PAYLOAD segment B (20+ consecutive 'B')",
    ).not.toContain("B".repeat(20));
    expect(
      frame,
      "compact frame leaked PAYLOAD segment C (20+ consecutive 'C')",
    ).not.toContain("C".repeat(20));
    expect(
      frame,
      "compact frame leaked PAYLOAD segment D (20+ consecutive 'D')",
    ).not.toContain("D".repeat(20));
    expect(
      frame,
      "compact frame leaked PAYLOAD segment E (20+ consecutive 'E')",
    ).not.toContain("E".repeat(20));

    // Hard negative 3: reject JSON-dump compact summaries. Per spec §2.5 the
    // compact path must emit either a semantic "[image...]" marker or no
    // summary at all — but NEVER raw MCP result JSON. A buggy impl that
    // JSON.stringify()s the result and truncates to ≤60 chars would slip
    // past the 20-char base64 segment guards above (the data prefix never
    // reaches 20 consecutive payload chars) but still leak structural JSON.
    // Whitespace-tolerant regexes catch both the minified form
    // (`{"content":[`) AND the pretty-printed form produced by
    // `JSON.stringify(obj, null, 2)` (`{\n  "content": [\n    {`).
    expect(frame).not.toMatch(/"content"\s*:\s*\[/);
    expect(frame).not.toMatch(/"type"\s*:\s*"image"/);
    expect(frame).not.toMatch(/"source"\s*:\s*\{/);
    expect(frame).not.toMatch(/"type"\s*:\s*"base64"/);
    expect(frame).not.toMatch(/"data"\s*:\s*"/);

    // Image-only content in compact mode: per parent brief, the current impl
    // shows nothing via resultAsString (which ignores image blocks), and that
    // is legitimate. We do NOT require a positive summary here. The assertion
    // is purely that none of the forbidden things happened (no inline render,
    // no fallback marker, no base64 leak). An empty/short frame is fine.
  });

  it("compact mode (expanded=false) MIXED text+image: shows text payload, NO image escape, NO fallback marker, NO base64 leak", () => {
    process.env.TERM_PROGRAM = "iTerm.app";
    // 20-char segment guard catches medium-to-large raw-base64 leaks.
    // JSON-structural negatives below close the short-prefix-dump gap —
    // a JSON.stringify(result) prefix ≤60 chars would contain the text
    // marker (satisfying the positive assertion) while still leaking
    // structural JSON that violates spec §2.5's "single-line summary" intent.
    const PAYLOAD =
      "B".repeat(50) + "C".repeat(50) + "D".repeat(50) + "E".repeat(650); // 800 chars total
    const TEXT_MARKER = "compact-text-marker-77";
    const image = makeImageBlock(PAYLOAD, "image/png");
    const tool = makeTool({
      content: [makeTextBlock(TEXT_MARKER), image],
      isError: false,
    });

    const instance = render(
      React.createElement(ToolPanel, { tool, expanded: false }),
    );
    const frame = instance.lastFrame() ?? "";
    instance.unmount();

    // Hard negative 1: compact view must NEVER emit the iterm2 escape OR the
    // fallback saved-file marker.
    expect(frame).not.toContain("\x1b]1337;File=inline=1;");
    expect(frame).not.toContain("[image saved: ");

    // Hard negative 2: NO raw base64 leak at 20-char granularity — catches
    // both full and partial (truncated) leaks of any segment.
    expect(
      frame,
      "compact frame leaked PAYLOAD segment B (20+ consecutive 'B')",
    ).not.toContain("B".repeat(20));
    expect(
      frame,
      "compact frame leaked PAYLOAD segment C (20+ consecutive 'C')",
    ).not.toContain("C".repeat(20));
    expect(
      frame,
      "compact frame leaked PAYLOAD segment D (20+ consecutive 'D')",
    ).not.toContain("D".repeat(20));
    expect(
      frame,
      "compact frame leaked PAYLOAD segment E (20+ consecutive 'E')",
    ).not.toContain("E".repeat(20));

    // Hard negative 3: reject JSON-dump compact summaries. Even if the
    // TEXT_MARKER is visible (satisfying the positive assertion below),
    // a buggy impl that JSON.stringify()s the full result and truncates
    // to ≤60 chars would leak structural JSON like
    // `{"content":[{"type":"text","text":"compact-text-marker-77`. Spec
    // §2.5 requires a semantic summary, not a raw JSON prefix.
    // Whitespace-tolerant regexes catch both the minified form
    // (`{"content":[`) AND the pretty-printed form produced by
    // `JSON.stringify(obj, null, 2)` (`{\n  "content": [\n    {`).
    expect(frame).not.toMatch(/"content"\s*:\s*\[/);
    expect(frame).not.toMatch(/"type"\s*:\s*"image"/);
    expect(frame).not.toMatch(/"source"\s*:\s*\{/);
    expect(frame).not.toMatch(/"type"\s*:\s*"base64"/);
    expect(frame).not.toMatch(/"data"\s*:\s*"/);

    // Positive: the text block content MUST be visible. For mixed content,
    // compact mode's summary IS the text block content (via resultAsString,
    // which concatenates text and ignores image blocks). Silence on mixed
    // content would be a regression.
    expect(
      frame,
      `compact mixed frame must show the text payload. Got frame:\n${frame}`,
    ).toContain(TEXT_MARKER);
  });
});
