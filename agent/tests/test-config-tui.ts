/**
 * v0.4 Phase A+B: Config Primitives + TUI Foundation
 *
 * Test-Reinforced Development -- Overseer test suite.
 * All tests call real implementation functions that the Executor must create.
 * Every test should FAIL until the implementation is complete.
 */

import { describe, it, expect, beforeEach, afterEach } from "vitest";
import { existsSync, readFileSync, writeFileSync, mkdirSync, rmSync } from "fs";
import { join } from "path";

// --- Contract types (NEVER redefine -- import from contracts) ---
import type {
  KLayoutConfig,
  ToolAnnotation,
  FocusState,
  ConfigPanelTab,
  CommandResultStateChange,
} from "../src/types/v04-contracts.js";

// --- Test helpers ---
import {
  makeConfig,
  makeTmpDir,
  makeKLayoutConfig,
  writeConfigFiles,
} from "./helpers/config-builder.js";

// --- Implementation imports (files the Executor will create/modify) ---
import {
  loadConfig,
  saveKLayoutConfig,
  saveModelConfig,
  saveMCPConfig,
  saveSettingsConfig,
  resolveModel,
  applyModelHeaders,
  DEFAULT_USER_AGENT,
} from "../src/config.js";

import {
  getToolIcon,
  TOOL_ANNOTATIONS,
} from "../src/tools/annotations.js";

import { getGhostSuffix } from "../src/tui/ghost.js";

import {
  loadCommandRecency,
  saveCommandRecency,
} from "../src/tui/hooks/useCommandHistory.js";

import { tuiReducer, initialState } from "../src/tui/reducer.js";

import { SLASH_COMMANDS, matchCommands } from "../src/tui/commands.js";

import { filterDisabledTools } from "../src/tools/index.js";

import { isInputActive } from "../src/tui/focus.js";

// ==========================================================================
// Phase A: Config Primitives
// ==========================================================================

describe("SCC-A1: loadConfig reads klayout.json as 4th config file", () => {
  it("merges klayout.json into QlayBotConfig.klayout", () => {
    const tmpDir = makeTmpDir();
    const cfg = makeConfig({
      klayout: { autoLaunch: false, disabledTools: ["screenshot"] },
    });
    writeConfigFiles(tmpDir, cfg);

    const loaded = loadConfig(join(tmpDir, "config"));

    expect(loaded.klayout).toBeDefined();
    expect(loaded.klayout.url).toBe("http://127.0.0.1:8765/mcp");
    expect(loaded.klayout.autoLaunch).toBe(false);
    expect(loaded.klayout.disabledTools).toEqual(["screenshot"]);
    expect(loaded.klayout.required).toBe(true);
  });

  it("returns default klayout config when klayout.json is missing", () => {
    const tmpDir = makeTmpDir();
    const configDir = join(tmpDir, "config");
    mkdirSync(configDir, { recursive: true });
    // Write only model.json and mcp.json -- no klayout.json
    writeFileSync(join(configDir, "model.json"), JSON.stringify({
      defaultModel: "custom-anthropic/claude-sonnet-4-6",
      providers: {},
    }));
    writeFileSync(join(configDir, "mcp.json"), "{}");
    writeFileSync(join(configDir, "settings.json"), "{}");

    const loaded = loadConfig(configDir);

    expect(loaded.klayout).toBeDefined();
    expect(loaded.klayout.url).toBe("http://127.0.0.1:8765/mcp");
    expect(loaded.klayout.required).toBe(true);
    expect(loaded.klayout.autoLaunch).toBe(true);
    expect(loaded.klayout.disabledTools).toEqual([]);
  });
});

describe("SCC-A2: saveKLayoutConfig writes ONLY klayout fields", () => {
  it("writes klayout.json without other config sections", () => {
    const tmpDir = makeTmpDir();
    const configDir = join(tmpDir, "config");
    mkdirSync(configDir, { recursive: true });

    const cfg = makeConfig({
      klayout: { autoLaunch: false, disabledTools: ["auto_route"] },
    });

    saveKLayoutConfig(cfg, configDir);

    const raw = JSON.parse(readFileSync(join(configDir, "klayout.json"), "utf-8"));
    expect(raw.url).toBe("http://127.0.0.1:8765/mcp");
    expect(raw.autoLaunch).toBe(false);
    expect(raw.disabledTools).toEqual(["auto_route"]);
    // Must NOT contain other config sections
    expect(raw.agent).toBeUndefined();
    expect(raw.models).toBeUndefined();
    expect(raw.mcp).toBeUndefined();
    expect(raw.memory).toBeUndefined();
  });
});

describe("SCC-A2a: Config save array-replace behavior", () => {
  it("second save replaces arrays entirely, not merging", () => {
    const tmpDir = makeTmpDir();
    const configDir = join(tmpDir, "config");
    mkdirSync(configDir, { recursive: true });

    // First save with ["a", "b"]
    const cfg1 = makeConfig({ klayout: { disabledTools: ["a", "b"] } });
    saveKLayoutConfig(cfg1, configDir);

    const raw1 = JSON.parse(readFileSync(join(configDir, "klayout.json"), "utf-8"));
    expect(raw1.disabledTools).toEqual(["a", "b"]);

    // Second save with ["c"] -- should replace, not merge
    const cfg2 = makeConfig({ klayout: { disabledTools: ["c"] } });
    saveKLayoutConfig(cfg2, configDir);

    const raw2 = JSON.parse(readFileSync(join(configDir, "klayout.json"), "utf-8"));
    expect(raw2.disabledTools).toEqual(["c"]);
  });
});

describe("SCC-A3: Migration is idempotent", () => {
  it("loading config twice does not corrupt files", () => {
    const tmpDir = makeTmpDir();
    const cfg = makeConfig();
    writeConfigFiles(tmpDir, cfg);
    const configDir = join(tmpDir, "config");

    const loaded1 = loadConfig(configDir);
    const loaded2 = loadConfig(configDir);

    expect(loaded1.klayout).toEqual(loaded2.klayout);
    expect(loaded1.agent.defaultModel).toBe(loaded2.agent.defaultModel);
    expect(loaded1.mcp).toEqual(loaded2.mcp);

    // Verify config files on disk are valid JSON and not corrupted
    const modelOnDisk = JSON.parse(readFileSync(join(configDir, "model.json"), "utf-8"));
    expect(modelOnDisk.defaultModel).toBe("custom-anthropic/claude-sonnet-4-6");
    expect(modelOnDisk.providers).toBeDefined();

    const klayoutOnDisk = JSON.parse(readFileSync(join(configDir, "klayout.json"), "utf-8"));
    expect(klayoutOnDisk.url).toBe("http://127.0.0.1:8765/mcp");

    const mcpOnDisk = JSON.parse(readFileSync(join(configDir, "mcp.json"), "utf-8"));
    expect(mcpOnDisk).toBeDefined();
    expect(typeof mcpOnDisk).toBe("object");

    const settingsOnDisk = JSON.parse(readFileSync(join(configDir, "settings.json"), "utf-8"));
    expect(settingsOnDisk).toBeDefined();
    expect(typeof settingsOnDisk).toBe("object");
  });
});

describe("SCC-A4: Migration extracts klayout_mcp from mcp.json", () => {
  it("moves klayout_mcp entry to klayout.json and preserves other MCP entries", () => {
    const tmpDir = makeTmpDir();
    const configDir = join(tmpDir, "config");
    mkdirSync(configDir, { recursive: true });

    // Write old-style mcp.json with klayout_mcp inside it
    writeFileSync(join(configDir, "mcp.json"), JSON.stringify({
      klayout_mcp: { url: "http://127.0.0.1:8765/mcp", required: true },
      some_other_server: { url: "http://localhost:9999/mcp" },
    }));
    writeFileSync(join(configDir, "model.json"), JSON.stringify({
      defaultModel: "custom-anthropic/claude-sonnet-4-6",
      providers: {},
    }));
    writeFileSync(join(configDir, "settings.json"), "{}");
    // No klayout.json -- migration should create it

    const loaded = loadConfig(configDir);

    // klayout config should be extracted
    expect(loaded.klayout).toBeDefined();
    expect(loaded.klayout.url).toBe("http://127.0.0.1:8765/mcp");

    // klayout_mcp should be removed from mcp
    expect(loaded.mcp.klayout_mcp).toBeUndefined();

    // Other MCP entries preserved
    expect(loaded.mcp.some_other_server).toBeDefined();
    expect(loaded.mcp.some_other_server.url).toBe("http://localhost:9999/mcp");

    // Verify file persistence: mcp.json on disk should NOT contain klayout_mcp
    const mcpOnDisk = JSON.parse(readFileSync(join(configDir, "mcp.json"), "utf-8"));
    expect(mcpOnDisk.klayout_mcp).toBeUndefined();
    expect(mcpOnDisk.some_other_server).toBeDefined();

    // Verify file persistence: klayout.json was created on disk
    expect(existsSync(join(configDir, "klayout.json"))).toBe(true);
    const klayoutOnDisk = JSON.parse(readFileSync(join(configDir, "klayout.json"), "utf-8"));
    expect(klayoutOnDisk.url).toBe("http://127.0.0.1:8765/mcp");
  });
});

describe("SCC-A5: Bare model ID rewrite", () => {
  it("rewrites bare model ID to provider/model format", () => {
    const tmpDir = makeTmpDir();
    const configDir = join(tmpDir, "config");
    mkdirSync(configDir, { recursive: true });

    writeFileSync(join(configDir, "model.json"), JSON.stringify({
      defaultModel: "claude-sonnet-4-6",
      providers: {
        "custom-anthropic": {
          baseUrl: "https://bench.physcai.com/api",
          apiKey: "test-key",
          api: "anthropic-messages",
          models: [{ id: "claude-sonnet-4-6", name: "Claude Sonnet 4.6", reasoning: true, input: ["text", "image"], cost: { input: 3, output: 15, cacheRead: 0.3, cacheWrite: 3.75 }, contextWindow: 200000, maxTokens: 65536 }],
        },
      },
    }));
    writeFileSync(join(configDir, "mcp.json"), "{}");
    writeFileSync(join(configDir, "settings.json"), "{}");

    const loaded = loadConfig(configDir);
    expect(loaded.agent.defaultModel).toBe("custom-anthropic/claude-sonnet-4-6");
  });
});

describe("SCC-A6: resolveModel 5-step fallback", () => {
  const cfg = makeConfig({
    agent: { defaultModel: "custom-anthropic/claude-sonnet-4-6", thinkingLevel: "high" },
  });

  it("step 1: exact provider/modelId match", () => {
    const result = resolveModel("custom-anthropic/claude-sonnet-4-6", cfg);
    expect(result.provider).toBe("custom-anthropic");
    expect(result.model.id).toBe("claude-sonnet-4-6");
  });

  it("step 2: any-provider scan (just model ID, multiple providers)", () => {
    const multiCfg = makeConfig();
    // Add a second provider with a unique model
    (multiCfg as any).models.providers["openai-compat"] = {
      baseUrl: "https://api.openai.com/v1",
      apiKey: "sk-test",
      api: "openai-chat",
      models: [{ id: "gpt-5-mini", name: "GPT-5 Mini", reasoning: false, input: ["text"], cost: { input: 1, output: 2, cacheRead: 0, cacheWrite: 0 }, contextWindow: 128000, maxTokens: 16384 }],
    };

    const result = resolveModel("gpt-5-mini", multiCfg);
    expect(result.provider).toBe("openai-compat");
    expect(result.model.id).toBe("gpt-5-mini");
  });

  it("step 3: prefix match", () => {
    const result = resolveModel("claude-sonnet", cfg);
    expect(result.model.id).toContain("claude-sonnet");
  });

  it("step 4: first model fallback when nothing matches", () => {
    const result = resolveModel("nonexistent-model-xyz", cfg);
    // Should fall back to first model from the first provider
    expect(result.provider).toBe("custom-anthropic");
    expect(result.model).toBeDefined();
    expect(result.model.id).toBe("claude-sonnet-4-6");
  });

  it("step 5: error when no models exist at all", () => {
    const emptyCfg = makeConfig();
    emptyCfg.models.providers = {};

    expect(() => resolveModel("anything", emptyCfg)).toThrow();
  });
});

describe("SCC-A6b: applyModelHeaders default User-Agent + provider override", () => {
  const baseModel = {
    id: "claude-sonnet-4-6",
    name: "Claude Sonnet 4.6",
    api: "anthropic-messages",
    provider: "custom-anthropic",
    baseUrl: "https://bench.physcai.com/api",
    reasoning: true,
    input: ["text", "image"] as ("text" | "image")[],
    cost: { input: 3, output: 15, cacheRead: 0.3, cacheWrite: 3.75 },
    contextWindow: 200000,
    maxTokens: 65536,
  };

  it("injects the default neutral User-Agent when no provider headers are set", () => {
    const result = applyModelHeaders({ ...baseModel });
    expect(result.headers?.["User-Agent"]).toBe(DEFAULT_USER_AGENT);
  });

  it("lets a provider headers override win over the default User-Agent", () => {
    const result = applyModelHeaders({ ...baseModel }, { "User-Agent": "my-custom-agent/1.0", "X-Extra": "1" });
    expect(result.headers?.["User-Agent"]).toBe("my-custom-agent/1.0");
    expect(result.headers?.["X-Extra"]).toBe("1");
  });

  it("does not mutate the input model (non-mutating spread)", () => {
    const input = { ...baseModel };
    const result = applyModelHeaders(input);
    expect(input.headers).toBeUndefined();
    expect(result).not.toBe(input);
    expect(result.headers?.["User-Agent"]).toBe(DEFAULT_USER_AGENT);
  });
});

describe("SCC-A7: OpenAI provider with api:openai-chat", () => {
  it("resolves model from openai-chat provider correctly", () => {
    const cfg = makeConfig();
    (cfg as any).models.providers["openai-compat"] = {
      baseUrl: "https://api.openai.com/v1",
      apiKey: "sk-test",
      api: "openai-chat",
      models: [{ id: "gpt-5-mini", name: "GPT-5 Mini", reasoning: false, input: ["text"], cost: { input: 1, output: 2, cacheRead: 0, cacheWrite: 0 }, contextWindow: 128000, maxTokens: 16384 }],
    };

    const result = resolveModel("openai-compat/gpt-5-mini", cfg);
    expect(result.provider).toBe("openai-compat");
    expect(result.model.id).toBe("gpt-5-mini");
    expect(result.providerConfig.api).toBe("openai-chat");
  });
});

describe("SCC-A8: disabledTools filters KLayout tools but not base tools", () => {
  it("filters klayout_geometry_add_rect but NOT read", () => {
    const cfg = makeConfig({
      klayout: { disabledTools: ["klayout_geometry_add_rect"] },
    });

    // Build a mock tool list with both base and KLayout tools
    const allToolNames = [
      "read", "bash", "edit", "write",  // base tools
      "klayout_geometry_add_rect", "klayout_geometry_add_polygon",  // klayout tools
      "memory_save", "memory_search",  // custom tools
    ];

    const filtered = filterDisabledTools(allToolNames, cfg.klayout.disabledTools);

    // klayout tool should be removed
    expect(filtered).not.toContain("klayout_geometry_add_rect");
    // base tool must remain
    expect(filtered).toContain("read");
    // other klayout tool still present
    expect(filtered).toContain("klayout_geometry_add_polygon");
  });
});

describe("SCC-A9: disabledTools from klayout.json AND per-server mcp.json", () => {
  it("loads combined disabledTools from klayout.json and per-server mcp.json via loadConfig", () => {
    const tmpDir = makeTmpDir();
    const configDir = join(tmpDir, "config");
    mkdirSync(configDir, { recursive: true });

    // Write klayout.json with disabledTools
    writeFileSync(join(configDir, "klayout.json"), JSON.stringify({
      url: "http://127.0.0.1:8765/mcp",
      required: true,
      autoLaunch: true,
      disabledTools: ["klayout_geometry_add_rect"],
    }));
    // Write mcp.json with per-server disabledTools
    writeFileSync(join(configDir, "mcp.json"), JSON.stringify({
      some_server: {
        url: "http://localhost:9999/mcp",
        disabledTools: ["some_mcp_tool"],
      },
    }));
    writeFileSync(join(configDir, "model.json"), JSON.stringify({
      defaultModel: "custom-anthropic/claude-sonnet-4-6",
      providers: {},
    }));
    writeFileSync(join(configDir, "settings.json"), "{}");

    const loaded = loadConfig(configDir);

    // Combine disabled tools from both sources (klayout + per-server mcp)
    const mcpDisabledTools: string[] = [];
    for (const server of Object.values(loaded.mcp)) {
      if ((server as any).disabledTools) {
        mcpDisabledTools.push(...(server as any).disabledTools);
      }
    }
    const combined = [...loaded.klayout.disabledTools, ...mcpDisabledTools];

    const allToolNames = [
      "read", "bash",
      "klayout_geometry_add_rect", "klayout_geometry_add_polygon",
      "some_mcp_tool", "another_mcp_tool",
    ];

    const filtered = filterDisabledTools(allToolNames, combined);

    expect(filtered).not.toContain("klayout_geometry_add_rect");
    expect(filtered).not.toContain("some_mcp_tool");
    // Base tools always survive
    expect(filtered).toContain("read");
    expect(filtered).toContain("bash");
    // Non-disabled tools survive
    expect(filtered).toContain("klayout_geometry_add_polygon");
    expect(filtered).toContain("another_mcp_tool");
  });
});

describe("SCC-A10: getToolIcon returns correct icons", () => {
  it("returns eye for readonly", () => {
    const ann: ToolAnnotation = { name: "screenshot", readonly: true };
    expect(getToolIcon(ann)).toBe("\u{1F440}");
  });

  it("returns pen for readwrite", () => {
    const ann: ToolAnnotation = { name: "execute_script", readwrite: true };
    expect(getToolIcon(ann)).toBe("\u{1F58A}\uFE0F");
  });

  it("returns lightning for backgroundable", () => {
    const ann: ToolAnnotation = { name: "auto_route", backgroundable: true };
    expect(getToolIcon(ann)).toBe("\u26A1");
  });

  it("TOOL_ANNOTATIONS is a non-empty array of ToolAnnotation", () => {
    expect(Array.isArray(TOOL_ANNOTATIONS)).toBe(true);
    expect(TOOL_ANNOTATIONS.length).toBeGreaterThan(0);
    for (const ann of TOOL_ANNOTATIONS) {
      expect(ann.name).toBeTruthy();
    }
  });
});

describe("SCC-A11: API keys stored in model.json provider entries", () => {
  it("provider apiKey comes from model.json, not env vars by default", () => {
    const tmpDir = makeTmpDir();
    const configDir = join(tmpDir, "config");
    mkdirSync(configDir, { recursive: true });

    writeFileSync(join(configDir, "model.json"), JSON.stringify({
      defaultModel: "custom-anthropic/claude-sonnet-4-6",
      providers: {
        "custom-anthropic": {
          baseUrl: "https://bench.physcai.com/api",
          apiKey: "stored-key-in-file",
          api: "anthropic-messages",
          models: [{ id: "claude-sonnet-4-6", name: "Sonnet", reasoning: true, input: ["text"], cost: { input: 3, output: 15, cacheRead: 0.3, cacheWrite: 3.75 }, contextWindow: 200000, maxTokens: 65536 }],
        },
      },
    }));
    writeFileSync(join(configDir, "mcp.json"), "{}");
    writeFileSync(join(configDir, "settings.json"), "{}");

    // Temporarily unset env var to test file-based key
    const savedKey = process.env.ANTHROPIC_API_KEY;
    delete process.env.ANTHROPIC_API_KEY;

    try {
      const loaded = loadConfig(configDir);
      expect(loaded.models.providers["custom-anthropic"].apiKey).toBe("stored-key-in-file");
      expect(loaded.models.providers["custom-anthropic"].baseUrl).toBe("https://bench.physcai.com/api");
    } finally {
      if (savedKey !== undefined) process.env.ANTHROPIC_API_KEY = savedKey;
    }
  });
});

describe("SCC-A12: ANTHROPIC_API_KEY env var overrides provider apiKey", () => {
  it("env var overrides empty apiKey at load time", () => {
    const tmpDir = makeTmpDir();
    const configDir = join(tmpDir, "config");
    mkdirSync(configDir, { recursive: true });

    writeFileSync(join(configDir, "model.json"), JSON.stringify({
      defaultModel: "custom-anthropic/claude-sonnet-4-6",
      providers: {
        "custom-anthropic": {
          baseUrl: "https://bench.physcai.com/api",
          apiKey: "",
          api: "anthropic-messages",
          models: [{ id: "claude-sonnet-4-6", name: "Sonnet", reasoning: true, input: ["text"], cost: { input: 3, output: 15, cacheRead: 0.3, cacheWrite: 3.75 }, contextWindow: 200000, maxTokens: 65536 }],
        },
      },
    }));
    writeFileSync(join(configDir, "mcp.json"), "{}");
    writeFileSync(join(configDir, "settings.json"), "{}");

    const savedKey = process.env.ANTHROPIC_API_KEY;
    process.env.ANTHROPIC_API_KEY = "env-override-key";

    try {
      const loaded = loadConfig(configDir);
      expect(loaded.models.providers["custom-anthropic"].apiKey).toBe("env-override-key");
    } finally {
      if (savedKey !== undefined) {
        process.env.ANTHROPIC_API_KEY = savedKey;
      } else {
        delete process.env.ANTHROPIC_API_KEY;
      }
    }
  });
});

// ==========================================================================
// Phase B: TUI Foundation -- Focus Exclusivity
// ==========================================================================

describe("SCC-B0a-e: Focus exclusivity -- InputBox isActive tied to focusState", () => {
  it("B0a: focusState config-panel makes InputBox inactive", () => {
    const state = { ...initialState, focusState: "config-panel" as FocusState };
    expect(isInputActive(state.focusState)).toBe(false);
  });

  it("B0b: subagent-summary and subagent-inspect make InputBox inert", () => {
    expect(isInputActive("subagent-summary")).toBe(false);
    expect(isInputActive("subagent-inspect")).toBe(false);
  });

  it("B0c: workspace-bar, background-bar, bar-select make InputBox inert", () => {
    expect(isInputActive("workspace-bar")).toBe(false);
    expect(isInputActive("background-bar")).toBe(false);
    expect(isInputActive("bar-select")).toBe(false);
  });

  it("B0d: input and completion allow InputBox keyboard events", () => {
    expect(isInputActive("input")).toBe(true);
    expect(isInputActive("completion")).toBe(true);
  });

  it("B0e: all 9 FocusState values are handled by isInputActive", () => {
    const allStates: FocusState[] = [
      "input", "completion", "bar-select", "config-panel",
      "workspace-bar", "background-bar",
      "subagent-summary", "subagent-inspect", "subagent-inject",
    ];
    for (const s of allStates) {
      const result = isInputActive(s);
      expect(typeof result).toBe("boolean");
    }
    // Only input and completion should be true
    const activeStates = allStates.filter((s) => isInputActive(s));
    expect(activeStates).toEqual(["input", "completion"]);
  });
});

// ==========================================================================
// Phase B: Focus & Ghost Text
// ==========================================================================

describe("SCC-B1: focusState starts as input", () => {
  it("initialState.focusState is 'input'", () => {
    expect(initialState.focusState).toBe("input");
  });
});

describe("SCC-B2: Up in input with completions open transitions to completion", () => {
  it("dispatching FOCUS_UP with completions transitions to completion", () => {
    const state = {
      ...initialState,
      focusState: "input" as FocusState,
    };
    const next = tuiReducer(state, {
      type: "FOCUS_UP",
      hasCompletions: true,
    } as any);
    expect(next.focusState).toBe("completion");
  });
});

describe("SCC-B3: Down from input with empty text transitions to bar-select", () => {
  it("dispatching FOCUS_DOWN with empty input moves to bar-select", () => {
    const state = {
      ...initialState,
      focusState: "input" as FocusState,
    };
    const next = tuiReducer(state, {
      type: "FOCUS_DOWN",
      inputText: "",
    } as any);
    expect(next.focusState).toBe("bar-select");
  });
});

describe("SCC-B4: Down from input with non-empty text stays in input", () => {
  it("does not transition when input has text", () => {
    const state = {
      ...initialState,
      focusState: "input" as FocusState,
    };
    const next = tuiReducer(state, {
      type: "FOCUS_DOWN",
      inputText: "/model",
    } as any);
    expect(next.focusState).toBe("input");
  });
});

describe("SCC-B5: Escape from config-panel returns to input", () => {
  it("escape transitions config-panel -> input", () => {
    const state = {
      ...initialState,
      focusState: "config-panel" as FocusState,
    };
    const next = tuiReducer(state, { type: "FOCUS_ESCAPE" } as any);
    expect(next.focusState).toBe("input");
  });
});

describe("SCC-B6: Escape from bar-select returns to input", () => {
  it("escape transitions bar-select -> input", () => {
    const state = {
      ...initialState,
      focusState: "bar-select" as FocusState,
    };
    const next = tuiReducer(state, { type: "FOCUS_ESCAPE" } as any);
    expect(next.focusState).toBe("input");
  });
});

describe("SCC-B7: Ghost text for /mo shows del suffix", () => {
  it("getGhostSuffix returns 'del' for /mo input", () => {
    const commands = SLASH_COMMANDS.map((c) => `/${c.name}`);
    const suffix = getGhostSuffix("/mo", commands, {});
    expect(suffix).toBe("del");
  });
});

describe("SCC-B8: Ghost text respects recency", () => {
  it("shows 'mpact' with up-indicator for /co when compact is more recent", () => {
    const commands = SLASH_COMMANDS.map((c) => `/${c.name}`);
    const recency: Record<string, number> = {
      "/config": 100,
      "/compact": 200,
    };
    const suffix = getGhostSuffix("/co", commands, recency);
    // /compact is more recent, so ghost should complete to "mpact"
    // with an up-arrow indicator because there are multiple matches
    expect(suffix).toContain("mpact");
    // The up-indicator signals more completions available
    expect(suffix).toContain("\u2191");
  });
});

describe("SCC-B9: Tab accepts ghost hint", () => {
  it("getGhostSuffix returns a non-empty suffix that completes the command", () => {
    const input = "/mo";
    const commands = SLASH_COMMANDS.map((c) => `/${c.name}`);
    const suffix = getGhostSuffix(input, commands, {});

    // The suffix itself must be non-empty -- proves the function did work
    expect(suffix).toBeTruthy();
    expect(suffix!.length).toBeGreaterThan(0);

    // The suffix when applied must yield a valid slash command
    expect(suffix).toBe("del");
    expect(commands).toContain(input + suffix);
  });
});

describe("SCC-B10: Up/Down in CompletionList cycles through matches", () => {
  it("cycling through completions with reducer actions", () => {
    // Start in completion mode with some matches
    const state = {
      ...initialState,
      focusState: "completion" as FocusState,
      completionIndex: 0,
      completionMatches: ["/model", "/mcp", "/memory"],
    };

    // Down should increment
    const next1 = tuiReducer(state, { type: "COMPLETION_DOWN" } as any);
    expect(next1.completionIndex).toBe(1);

    // Down again
    const next2 = tuiReducer(next1, { type: "COMPLETION_DOWN" } as any);
    expect(next2.completionIndex).toBe(2);

    // Down wraps to 0
    const next3 = tuiReducer(next2, { type: "COMPLETION_DOWN" } as any);
    expect(next3.completionIndex).toBe(0);

    // Up from 0 wraps to end
    const next4 = tuiReducer(state, { type: "COMPLETION_UP" } as any);
    expect(next4.completionIndex).toBe(2);
  });
});

describe("SCC-B11: Command recency persists to history.json", () => {
  it("saves recency data on command execution", () => {
    const tmpDir = makeTmpDir();
    const historyPath = join(tmpDir, "history.json");

    const recency: Record<string, number> = { "/model": Date.now() };
    saveCommandRecency(recency, historyPath);

    const raw = JSON.parse(readFileSync(historyPath, "utf-8"));
    expect(raw.recency).toBeDefined();
    expect(raw.recency["/model"]).toBeDefined();
    expect(typeof raw.recency["/model"]).toBe("number");
  });
});

describe("SCC-B12: Command recency loads from history.json on startup", () => {
  it("loadCommandRecency reads saved recency data", () => {
    const tmpDir = makeTmpDir();
    const historyPath = join(tmpDir, "history.json");

    const now = Date.now();
    writeFileSync(historyPath, JSON.stringify({
      entries: ["/model show", "/config"],
      recency: { "/model": now - 1000, "/config": now },
    }));

    const recency = loadCommandRecency(historyPath);
    expect(recency["/config"]).toBe(now);
    expect(recency["/model"]).toBe(now - 1000);
  });
});

describe("SCC-B13: Subcommand ghost hint", () => {
  it("/model with trailing space shows subcommand choices", () => {
    const commands = SLASH_COMMANDS.map((c) => `/${c.name}`);
    const suffix = getGhostSuffix("/model ", commands, {});
    // Should show subcommand hints like "[show|set|list]"
    expect(suffix).toContain("show");
    expect(suffix).toContain("set");
    expect(suffix).toContain("list");
  });
});

describe("SCC-B14: SLASH_COMMANDS entries have subcommands field", () => {
  it("model command has subcommands", () => {
    const modelCmd = SLASH_COMMANDS.find((c) => c.name === "model");
    expect(modelCmd).toBeDefined();
    expect(modelCmd!.subcommands).toBeDefined();
    expect(modelCmd!.subcommands).toContain("show");
    expect(modelCmd!.subcommands).toContain("set");
    expect(modelCmd!.subcommands).toContain("list");
  });

  it("mcp command has subcommands", () => {
    const mcpCmd = SLASH_COMMANDS.find((c) => c.name === "mcp");
    expect(mcpCmd).toBeDefined();
    expect(mcpCmd!.subcommands).toBeDefined();
    expect(mcpCmd!.subcommands).toContain("status");
    expect(mcpCmd!.subcommands).toContain("tools");
    expect(mcpCmd!.subcommands).toContain("reconnect");
  });

  it("config command has subcommands", () => {
    const configCmd = SLASH_COMMANDS.find((c) => c.name === "config");
    expect(configCmd).toBeDefined();
    expect(configCmd!.subcommands).toBeDefined();
    expect(configCmd!.subcommands).toContain("show");
    expect(configCmd!.subcommands).toContain("set");
    expect(configCmd!.subcommands).toContain("reset");
  });

  it("exit command has no subcommands (or empty)", () => {
    const exitCmd = SLASH_COMMANDS.find((c) => c.name === "exit");
    expect(exitCmd).toBeDefined();
    // exit either has no subcommands field or an empty array
    if (exitCmd!.subcommands) {
      expect(exitCmd!.subcommands).toHaveLength(0);
    }
  });
});

describe("SCC-B15: Bar-select mode highlights focused bar", () => {
  const BAR_NAMES = ["status", "workspace", "background"] as const;

  it("Down from status cycles to workspace", () => {
    const state = {
      ...initialState,
      focusState: "bar-select" as FocusState,
      selectedBar: "status" as string,
    };

    const next = tuiReducer(state, { type: "BAR_SELECT_DOWN" } as any);
    expect(next.selectedBar).toBe("workspace");
  });

  it("Down from background wraps to status", () => {
    const state = {
      ...initialState,
      focusState: "bar-select" as FocusState,
      selectedBar: "background" as string,
    };

    const next = tuiReducer(state, { type: "BAR_SELECT_DOWN" } as any);
    expect(next.selectedBar).toBe("status");
  });

  it("Up from status wraps to background", () => {
    const state = {
      ...initialState,
      focusState: "bar-select" as FocusState,
      selectedBar: "status" as string,
    };

    const next = tuiReducer(state, { type: "BAR_SELECT_UP" } as any);
    expect(next.selectedBar).toBe("background");
  });

  it("Up from workspace cycles to status", () => {
    const state = {
      ...initialState,
      focusState: "bar-select" as FocusState,
      selectedBar: "workspace" as string,
    };

    const next = tuiReducer(state, { type: "BAR_SELECT_UP" } as any);
    expect(next.selectedBar).toBe("status");
  });

  it("all bar names are valid", () => {
    for (const bar of BAR_NAMES) {
      const state = {
        ...initialState,
        focusState: "bar-select" as FocusState,
        selectedBar: bar as string,
      };
      const next = tuiReducer(state, { type: "BAR_SELECT_DOWN" } as any);
      expect(BAR_NAMES).toContain(next.selectedBar);
    }
  });
});

describe("SCC-B16: History migration: old format auto-migrates", () => {
  it("old plain-array history.json migrates to {entries, recency} format", () => {
    const tmpDir = makeTmpDir();
    const historyPath = join(tmpDir, "history.json");

    // Old format: plain array
    writeFileSync(historyPath, JSON.stringify([
      "/model show",
      "/config",
      "/compact",
    ]));

    const recency = loadCommandRecency(historyPath);

    // Old format has no recency data, so recency should be empty
    expect(recency).toEqual({});

    // Write back new recency to verify the NEW format was persisted
    saveCommandRecency({ "/model": 1234 }, historyPath);

    // Re-read the file from disk to verify new format was written
    const raw = JSON.parse(readFileSync(historyPath, "utf-8"));
    expect(raw.recency).toBeDefined();
    expect(raw.recency["/model"]).toBe(1234);
    // File should NOT be a plain array anymore
    expect(Array.isArray(raw)).toBe(false);

    // Re-load to verify round-trip
    const reloaded = loadCommandRecency(historyPath);
    expect(reloaded["/model"]).toBe(1234);
  });
});

describe("SCC-B17: Multi-session recency", () => {
  it("recency accumulates across sessions", () => {
    const tmpDir = makeTmpDir();
    const historyPath = join(tmpDir, "history.json");

    // Session A: run /compact
    const tA = 1000;
    saveCommandRecency({ "/compact": tA }, historyPath);

    // Session B: run /config (reads existing, merges, writes)
    const existing = loadCommandRecency(historyPath);
    const tB = 2000;
    saveCommandRecency({ ...existing, "/config": tB }, historyPath);

    // Session C: read back
    const final = loadCommandRecency(historyPath);
    expect(final["/compact"]).toBe(tA);
    expect(final["/config"]).toBe(tB);

    // Ghost text for "/co" should pick /config (more recent)
    const commands = SLASH_COMMANDS.map((c) => `/${c.name}`);
    const suffix = getGhostSuffix("/co", commands, final);
    expect(suffix).toContain("nfig");
  });
});

describe("SCC-B18: Ghost text -> CompletionList -> selection full flow", () => {
  it("typing /m shows ghost, Up enters completion, Down cycles, Tab accepts", () => {
    const commands = SLASH_COMMANDS.map((c) => `/${c.name}`);

    // Step 1: Ghost text for /m
    const ghost = getGhostSuffix("/m", commands, {});
    expect(ghost).toBeTruthy(); // Should show something (first match suffix)

    // Step 2: Matches for /m
    const matches = matchCommands("/m");
    expect(matches.length).toBeGreaterThanOrEqual(3); // /model, /mcp, /memory

    // Step 3: Up enters completion state
    const state = {
      ...initialState,
      focusState: "input" as FocusState,
    };
    const afterUp = tuiReducer(state, {
      type: "FOCUS_UP",
      hasCompletions: true,
    } as any);
    expect(afterUp.focusState).toBe("completion");

    // Step 4: Down cycles in completion
    const withCompletion = {
      ...afterUp,
      completionIndex: 0,
      completionMatches: matches,
    };
    const afterDown = tuiReducer(withCompletion, { type: "COMPLETION_DOWN" } as any);
    expect(afterDown.completionIndex).toBe(1);

    // Step 5: Selecting a completion should be the match at the current index
    const selectedCommand = matches[afterDown.completionIndex];
    expect(selectedCommand).toBeDefined();
    expect(selectedCommand.startsWith("/m")).toBe(true);

    // Step 6: Tab-selecting a candidate sets input to "/config " (with trailing space)
    // and returns focus to "input"
    const afterSelect = tuiReducer(afterDown, {
      type: "COMPLETION_SELECT",
      command: selectedCommand,
    } as any);
    expect(afterSelect.focusState).toBe("input");
    // The selected command should have a trailing space for subcommand entry
    expect(afterSelect.inputText).toBe(selectedCommand + " ");

    // Step 7: Ghost text shows subcommand hint after selection
    const ghostAfterSelect = getGhostSuffix(
      afterSelect.inputText,
      commands,
      {},
    );
    // If the selected command has subcommands, ghost should show them
    const selectedCmd = SLASH_COMMANDS.find(
      (c) => `/${c.name}` === selectedCommand,
    );
    if (selectedCmd?.subcommands && selectedCmd.subcommands.length > 0) {
      expect(ghostAfterSelect).toBeTruthy();
      // Should contain at least one subcommand name
      expect(ghostAfterSelect).toContain(selectedCmd.subcommands[0]);
    }
  });
});

// ==========================================================================
// Phase A additional: Config save functions
// ==========================================================================

describe("saveModelConfig writes model fields correctly", () => {
  it("writes defaultModel, thinkingLevel, and providers", () => {
    const tmpDir = makeTmpDir();
    const configDir = join(tmpDir, "config");
    mkdirSync(configDir, { recursive: true });

    const cfg = makeConfig();
    saveModelConfig(cfg, configDir);

    const raw = JSON.parse(readFileSync(join(configDir, "model.json"), "utf-8"));
    expect(raw.defaultModel).toBe("custom-anthropic/claude-sonnet-4-6");
    expect(raw.thinkingLevel).toBe("high");
    expect(raw.providers).toBeDefined();
    expect(raw.providers["custom-anthropic"]).toBeDefined();
    // Must NOT contain non-model fields
    expect(raw.mcp).toBeUndefined();
    expect(raw.klayout).toBeUndefined();
  });
});

describe("saveMCPConfig writes only MCP entries", () => {
  it("writes mcp server entries", () => {
    const tmpDir = makeTmpDir();
    const configDir = join(tmpDir, "config");
    mkdirSync(configDir, { recursive: true });

    const cfg = makeConfig();
    (cfg as any).mcp = { my_server: { url: "http://localhost:9999" } };
    saveMCPConfig(cfg, configDir);

    const raw = JSON.parse(readFileSync(join(configDir, "mcp.json"), "utf-8"));
    expect(raw.my_server).toBeDefined();
    expect(raw.my_server.url).toBe("http://localhost:9999");
    // Must NOT contain non-MCP fields
    expect(raw.agent).toBeUndefined();
    expect(raw.klayout).toBeUndefined();
  });
});

describe("saveSettingsConfig writes settings fields", () => {
  it("writes memory, mcpTimeouts, tui, compaction", () => {
    const tmpDir = makeTmpDir();
    const configDir = join(tmpDir, "config");
    mkdirSync(configDir, { recursive: true });

    const cfg = makeConfig();
    saveSettingsConfig(cfg, configDir);

    const raw = JSON.parse(readFileSync(join(configDir, "settings.json"), "utf-8"));
    expect(raw.memory).toBeDefined();
    expect(raw.mcpTimeouts).toBeDefined();
    expect(raw.tui).toBeDefined();
    expect(raw.compaction).toBeDefined();
    // Must NOT contain non-settings fields
    expect(raw.agent).toBeUndefined();
    expect(raw.models).toBeUndefined();
    expect(raw.klayout).toBeUndefined();
  });
});

// ==========================================================================
// Phase B additional: COMMAND_RESULT configPanel stateChange
// ==========================================================================

describe("SCC-A9 extension: COMMAND_RESULT with configPanel stateChange", () => {
  it("reducer handles configPanel open/tab in COMMAND_RESULT", () => {
    const state = { ...initialState, focusState: "input" as FocusState };
    const next = tuiReducer(state, {
      type: "COMMAND_RESULT",
      output: "Config panel opened",
      stateChange: {
        configPanel: { open: true, tab: "models" },
      },
    } as any);

    // When configPanel opens, focusState should transition to config-panel
    expect(next.focusState).toBe("config-panel");
  });
});

// ==========================================================================
// Integration: Runtime wiring tests
// These tests verify that pure functions are actually wired into the runtime.
// They should FAIL until the Executor implements the wiring.
// ==========================================================================

import { parseModelRef } from "../src/config.js";
import { assembleTools } from "../src/tools/index.js";

describe("WIRING-1: assembleTools applies disabledTools filtering", () => {
  it("assembleTools signature accepts disabledTools option", () => {
    // The updated assembleTools must accept a disabledTools parameter.
    // If the signature hasn't been extended, this test documents the requirement.
    // We verify by calling assembleTools with a mock MCPManager that returns
    // known tools, and checking that disabledTools are filtered out.

    const mockMcpTools = [
      {
        name: "klayout_geometry_add_rect",
        group: "klayout",
        originalName: "geometry_add_rect",
        description: "Add a rectangle",
        inputSchema: { type: "object" as const, properties: {}, required: [] },
      },
      {
        name: "klayout_visual_capture",
        group: "klayout",
        originalName: "visual_capture",
        description: "Capture viewport",
        inputSchema: { type: "object" as const, properties: {}, required: [] },
      },
    ];

    const mockMcpManager = {
      allTools: () => mockMcpTools,
      callTool: async () => ({ content: [{ type: "text" as const, text: "ok" }] }),
    } as any;

    const mockMemoryManager = {
      save: async () => {},
      search: async () => [],
    } as any;

    // The WIRING requirement: assembleTools must accept disabledTools and filter them.
    // Current signature: assembleTools({ cwd, mcpManager, memoryManager })
    // Required signature: assembleTools({ cwd, mcpManager, memoryManager, disabledTools? })
    const result = assembleTools({
      cwd: "/tmp",
      mcpManager: mockMcpManager,
      memoryManager: mockMemoryManager,
      disabledTools: ["klayout_geometry_add_rect"],
    } as any);

    const toolNames = result.customTools.map((t: any) => t.name);
    expect(toolNames).not.toContain("klayout_geometry_add_rect");
    expect(toolNames).toContain("klayout_visual_capture");
  });

  it("assembleTools without disabledTools returns all MCP tools", () => {
    const mockMcpTools = [
      {
        name: "klayout_geometry_add_rect",
        group: "klayout",
        originalName: "geometry_add_rect",
        description: "Add a rectangle",
        inputSchema: { type: "object" as const, properties: {}, required: [] },
      },
    ];

    const mockMcpManager = {
      allTools: () => mockMcpTools,
      callTool: async () => ({ content: [{ type: "text" as const, text: "ok" }] }),
    } as any;

    const mockMemoryManager = {
      save: async () => {},
      search: async () => [],
    } as any;

    const result = assembleTools({
      cwd: "/tmp",
      mcpManager: mockMcpManager,
      memoryManager: mockMemoryManager,
    });

    const toolNames = result.customTools.map((t: any) => t.name);
    expect(toolNames).toContain("klayout_geometry_add_rect");
  });
});

describe("WIRING-2: InputBox renders ghost text suffix", () => {
  // We cannot easily render InputBox without ink-testing-library,
  // but we CAN verify the integration contract: InputBox must call
  // getGhostSuffix and render the result. We test the contract here
  // by verifying that getGhostSuffix output is non-empty for a valid
  // prefix, and that the TUI types support ghost text rendering.

  it("getGhostSuffix returns renderable suffix for /mo that InputBox must display", () => {
    // This is the data contract: InputBox receives this suffix and renders it
    const commands = SLASH_COMMANDS.map((c) => `/${c.name}`);
    const suffix = getGhostSuffix("/mo", commands, {});
    expect(suffix).toBe("del");

    // The WIRING requirement: InputBox.tsx must:
    // 1. Call getGhostSuffix(inputValue, commands, recency)
    // 2. Render the suffix after the cursor in dim/gray text
    // We verify this by checking the InputBox component accepts a ghostSuffix prop.
    // Since we can't easily import React components in a pure test,
    // we verify the ghost module exports the right function signature.
    expect(typeof getGhostSuffix).toBe("function");
    expect(getGhostSuffix.length).toBeGreaterThanOrEqual(3); // 3 params
  });

  it("ghost suffix is empty for non-slash input (InputBox should render nothing)", () => {
    const commands = SLASH_COMMANDS.map((c) => `/${c.name}`);
    const suffix = getGhostSuffix("hello", commands, {});
    expect(suffix).toBe("");
  });

  it("ghost suffix includes up-indicator for ambiguous prefix (InputBox must show)", () => {
    const commands = SLASH_COMMANDS.map((c) => `/${c.name}`);
    // "/m" matches /model, /mcp, /memory -- should show ghost with up-indicator
    const suffix = getGhostSuffix("/m", commands, {});
    expect(suffix).toBeTruthy();
    // Multiple matches: suffix should contain up-arrow to indicate more options
    expect(suffix).toContain("\u2191");
  });
});

describe("WIRING-3: App.tsx uses focusState instead of useState booleans", () => {
  // The WIRING requirement: App.tsx must replace:
  //   const [workspaceMode, setWorkspaceMode] = useState(false)
  //   const [backgroundMode, setBackgroundMode] = useState(false)
  // with dispatch actions that set focusState via the reducer.
  //
  // Currently, the reducer has NO TOGGLE_WORKSPACE or TOGGLE_BACKGROUND actions.
  // The Executor must add these action types to TUIAction and handle them in tuiReducer.

  it("reducer handles TOGGLE_WORKSPACE action to set focusState", () => {
    // Ctrl+W must dispatch TOGGLE_WORKSPACE which sets focusState to "workspace-bar"
    const state = { ...initialState, focusState: "input" as FocusState };
    const next = tuiReducer(state, { type: "TOGGLE_WORKSPACE" } as any);
    expect(next.focusState).toBe("workspace-bar");
  });

  it("reducer handles TOGGLE_WORKSPACE as toggle (workspace-bar -> input)", () => {
    // Pressing Ctrl+W again when already in workspace-bar should return to input
    const state = { ...initialState, focusState: "workspace-bar" as FocusState };
    const next = tuiReducer(state, { type: "TOGGLE_WORKSPACE" } as any);
    expect(next.focusState).toBe("input");
  });

  it("reducer handles TOGGLE_BACKGROUND action to set focusState", () => {
    // Ctrl+G must dispatch TOGGLE_BACKGROUND which sets focusState to "background-bar"
    const state = { ...initialState, focusState: "input" as FocusState };
    const next = tuiReducer(state, { type: "TOGGLE_BACKGROUND" } as any);
    expect(next.focusState).toBe("background-bar");
  });

  it("reducer handles TOGGLE_BACKGROUND as toggle (background-bar -> input)", () => {
    const state = { ...initialState, focusState: "background-bar" as FocusState };
    const next = tuiReducer(state, { type: "TOGGLE_BACKGROUND" } as any);
    expect(next.focusState).toBe("input");
  });

  it("Escape from workspace-bar returns focusState to input", () => {
    const state = { ...initialState, focusState: "workspace-bar" as FocusState };
    const next = tuiReducer(state, { type: "FOCUS_ESCAPE" } as any);
    expect(next.focusState).toBe("input");
  });

  it("Escape from background-bar returns focusState to input", () => {
    const state = { ...initialState, focusState: "background-bar" as FocusState };
    const next = tuiReducer(state, { type: "FOCUS_ESCAPE" } as any);
    expect(next.focusState).toBe("input");
  });
});

describe("WIRING-4: resolveModel replaces parseModelRef in agent.ts", () => {
  it("resolveModel handles bare model ID that parseModelRef would misroute", () => {
    // parseModelRef("gpt-5-mini") returns { provider: "custom-anthropic", modelId: "gpt-5-mini" }
    // which would FAIL because "custom-anthropic" doesn't have "gpt-5-mini".
    // resolveModel correctly scans all providers (step 2) and finds it in openai-compat.
    const cfg = makeConfig();
    (cfg as any).models.providers["openai-compat"] = {
      baseUrl: "https://api.openai.com/v1",
      apiKey: "sk-test",
      api: "openai-chat",
      models: [{
        id: "gpt-5-mini",
        name: "GPT-5 Mini",
        reasoning: false,
        input: ["text"],
        cost: { input: 1, output: 2, cacheRead: 0, cacheWrite: 0 },
        contextWindow: 128000,
        maxTokens: 16384,
      }],
    };

    // parseModelRef incorrectly defaults to custom-anthropic for bare IDs
    const parsed = parseModelRef("gpt-5-mini");
    expect(parsed.provider).toBe("custom-anthropic"); // WRONG provider

    // resolveModel correctly finds it via any-provider scan (step 2)
    const resolved = resolveModel("gpt-5-mini", cfg);
    expect(resolved.provider).toBe("openai-compat"); // CORRECT provider
    expect(resolved.model.id).toBe("gpt-5-mini");
  });

  it("resolveModel handles prefix match that parseModelRef cannot do", () => {
    // parseModelRef("claude-sonnet") returns { provider: "custom-anthropic", modelId: "claude-sonnet" }
    // but there's no model with EXACT id "claude-sonnet" -- resolveModel finds "claude-sonnet-4-6" via prefix (step 3)
    const cfg = makeConfig();

    const parsed = parseModelRef("claude-sonnet");
    expect(parsed.modelId).toBe("claude-sonnet"); // Exact string -- no prefix logic

    const resolved = resolveModel("claude-sonnet", cfg);
    expect(resolved.model.id).toBe("claude-sonnet-4-6"); // Prefix match found the right model
    expect(resolved.provider).toBe("custom-anthropic");
  });

  it("resolveModel provides graceful fallback that parseModelRef lacks", () => {
    // parseModelRef("nonexistent") returns { provider: "custom-anthropic", modelId: "nonexistent" }
    // and the caller would get an undefined model lookup.
    // resolveModel falls back to first available model (step 4).
    const cfg = makeConfig();

    const parsed = parseModelRef("nonexistent");
    expect(parsed.modelId).toBe("nonexistent"); // No fallback -- caller gets raw string

    const resolved = resolveModel("nonexistent", cfg);
    expect(resolved.model).toBeDefined(); // Falls back to first model
    expect(resolved.model.id).toBe("claude-sonnet-4-6");
    expect(resolved.provider).toBe("custom-anthropic");
  });
});

// ==========================================================================
// GAP tests: Cross-reviewer identified wiring gaps (iteration 3)
// ==========================================================================

describe("GAP-1: KLayout accessible after migration via getAllMCPServers", () => {
  it("loadConfig returns klayout config that MCPManager can consume", async () => {
    const tmpDir = makeTmpDir();
    const configDir = join(tmpDir, "config");
    mkdirSync(configDir, { recursive: true });
    // Old format: klayout_mcp in mcp.json
    writeFileSync(
      join(configDir, "mcp.json"),
      JSON.stringify({
        klayout_mcp: { url: "http://127.0.0.1:8765/mcp", required: true },
      }),
    );
    writeFileSync(
      join(configDir, "model.json"),
      JSON.stringify({
        defaultModel: "custom-anthropic/claude-sonnet-4-6",
        providers: {},
      }),
    );
    writeFileSync(join(configDir, "settings.json"), "{}");

    const loaded = loadConfig(configDir);

    // After migration, klayout_mcp must be removed from mcp
    expect(loaded.mcp.klayout_mcp).toBeUndefined();

    // klayout config must be available as loaded.klayout
    expect(loaded.klayout).toBeDefined();
    expect(loaded.klayout.url).toBe("http://127.0.0.1:8765/mcp");

    // The KEY assertion: getAllMCPServers merges config.mcp + config.klayout
    // so MCPManager gets KLayout even though it was removed from config.mcp.
    const { getAllMCPServers } = await import("../src/config.js");
    const allServers = getAllMCPServers(loaded);
    expect(allServers.klayout).toBeDefined();
    expect(allServers.klayout.url).toBe("http://127.0.0.1:8765/mcp");
    // Generic MCP servers should also be present if any exist
    expect(Object.keys(allServers).length).toBeGreaterThanOrEqual(1);
  });

  it("getAllMCPServers includes both generic MCP servers and KLayout", async () => {
    const cfg = makeConfig();
    // Add a generic MCP server alongside klayout
    (cfg as any).mcp = { my_server: { url: "http://localhost:9999" } };

    const { getAllMCPServers } = await import("../src/config.js");
    const allServers = getAllMCPServers(cfg);

    // Both KLayout and generic servers must be present
    expect(allServers.klayout).toBeDefined();
    expect(allServers.klayout.url).toBe("http://127.0.0.1:8765/mcp");
    expect(allServers.my_server).toBeDefined();
    expect(allServers.my_server.url).toBe("http://localhost:9999");
  });
});

describe("GAP-2: Config provides disabledTools for assembleTools", () => {
  it("getAllMCPServers includes disabledTools from klayout config", async () => {
    const cfg = makeConfig({
      klayout: { disabledTools: ["auto_route"] },
    });

    const { getAllMCPServers } = await import("../src/config.js");
    const allServers = getAllMCPServers(cfg);

    // The KLayout entry in allServers must carry disabledTools
    // so assembleTools can consume them at the call site
    expect(allServers.klayout.disabledTools).toEqual(["auto_route"]);
  });

  it("config.klayout.disabledTools is available for assembleTools", () => {
    const cfg = makeConfig({
      klayout: { disabledTools: ["tool_a", "tool_b"] },
    });
    expect(cfg.klayout.disabledTools).toEqual(["tool_a", "tool_b"]);
  });
});

describe("GAP-3: InputBox respects focusState via isInputActive", () => {
  it("isInputActive returns false for all non-input states", () => {
    // Extends SCC-B0a-e: explicit coverage of every non-input FocusState.
    // The wiring contract: InputBox must pass isInputActive(focusState)
    // to useInput({ isActive }), so input is suppressed in all these states.
    const nonInputStates: FocusState[] = [
      "config-panel",
      "workspace-bar",
      "background-bar",
      "bar-select",
      "subagent-summary",
      "subagent-inspect",
      "subagent-inject",
    ];
    for (const state of nonInputStates) {
      expect(isInputActive(state)).toBe(false);
    }
  });

  it("isInputActive returns true only for input and completion states", () => {
    // These are the ONLY states where InputBox should accept keystrokes
    expect(isInputActive("input")).toBe(true);
    expect(isInputActive("completion")).toBe(true);
  });
});

describe("GAP-4: useCommandHistory provides recency data for ghost text", () => {
  it("loadCommandRecency returns saved recency data for ghost text consumption", () => {
    const tmpDir = makeTmpDir();
    const historyPath = join(tmpDir, "history.json");

    // Save some recency data (simulating what useCommandHistory would persist)
    saveCommandRecency({ "/model": 100, "/config": 200 }, historyPath);

    // Load it back (simulating what useCommandHistory would provide to InputBox)
    const recency = loadCommandRecency(historyPath);

    // Ghost text with this recency should pick /config (more recent) over /compact
    const commands = SLASH_COMMANDS.map((c) => `/${c.name}`);
    const suffix = getGhostSuffix("/co", commands, recency);
    // /config (recency=200) should beat /compact (no recency) and /context (no recency)
    expect(suffix).toContain("nfig");
  });

  it("ghost text without recency data uses alphabetical order", () => {
    // This documents what happens when InputBox passes {} for recency (the current bug).
    // Without recency, /co matches are ordered alphabetically: /compact, /config, /context
    // The first match (/compact) wins, NOT the most recently used command.
    const commands = SLASH_COMMANDS.map((c) => `/${c.name}`);
    const suffixNoRecency = getGhostSuffix("/co", commands, {});
    // Without recency, the first alphabetical match wins -- verify it is deterministic
    expect(suffixNoRecency).toBeTruthy();
    // The suffix should be either "mpact" (/compact) or "nfig" (/config) or "ntext" (/context)
    // depending on sort order. The point is: it does NOT respect user intent.
    expect(
      suffixNoRecency.includes("mpact") ||
      suffixNoRecency.includes("nfig") ||
      suffixNoRecency.includes("ntext"),
    ).toBe(true);
  });
});

// ==========================================================================
// Phase D: SETUP Wizard
// ==========================================================================

// Dynamic imports -- setup.ts does not exist yet (TRD red phase).
// Using dynamic import() so Phase A-C tests are not broken by the missing module.
async function importSetup() {
  return import("../src/setup.js") as Promise<{
    runSetupWizard: typeof import("../src/setup.js")["runSetupWizard"];
    detectKLayout: typeof import("../src/setup.js")["detectKLayout"];
    backupConfig: typeof import("../src/setup.js")["backupConfig"];
  }>;
}

async function importConfig() {
  return import("../src/config.js");
}

// --- D1: runSetupWizard writes all 4 config files (SCC-D8) ---

describe("SCC-D8: Wizard writes all 4 config files", () => {
  it("runSetupWizard creates model.json, klayout.json, mcp.json, settings.json", async () => {
    const { runSetupWizard } = await importSetup();
    const tmpDir = makeTmpDir();
    const configDir = join(tmpDir, "config");
    const templateDir = join(tmpDir, "templates");
    mkdirSync(templateDir, { recursive: true });

    const result = await runSetupWizard({
      configDir,
      templateDir,
      apiKey: "sk-ant-test-key-1234",
      model: "custom-anthropic/claude-sonnet-4-6",
      mcpUrl: "http://127.0.0.1:8765/mcp",
      skipValidation: true,
    });

    // All 4 config files must exist on disk
    expect(existsSync(join(configDir, "model.json"))).toBe(true);
    expect(existsSync(join(configDir, "klayout.json"))).toBe(true);
    expect(existsSync(join(configDir, "mcp.json"))).toBe(true);
    expect(existsSync(join(configDir, "settings.json"))).toBe(true);

    // The returned config should reflect the chosen model
    expect(result.agent.defaultModel).toBe("custom-anthropic/claude-sonnet-4-6");
  });
});

// --- D2: Wizard returns config with chosen API key (SCC-D3) ---

describe("SCC-D3: Wizard requires non-empty API key", () => {
  it("runSetupWizard uses provided apiKey in the returned config", async () => {
    const { runSetupWizard } = await importSetup();
    const tmpDir = makeTmpDir();
    const configDir = join(tmpDir, "config");
    const templateDir = join(tmpDir, "templates");
    mkdirSync(templateDir, { recursive: true });

    const result = await runSetupWizard({
      configDir,
      templateDir,
      apiKey: "sk-ant-my-secret-key",
      skipValidation: true,
    });

    // The API key must be stored in the provider config
    const providers = Object.values(result.models.providers);
    expect(providers.length).toBeGreaterThan(0);
    const firstProvider = providers[0];
    expect(firstProvider.apiKey).toBe("sk-ant-my-secret-key");

    // Verify it persisted to model.json on disk
    const modelJson = JSON.parse(readFileSync(join(configDir, "model.json"), "utf-8"));
    const diskProviders = Object.values(modelJson.providers) as any[];
    expect(diskProviders[0].apiKey).toBe("sk-ant-my-secret-key");
  });
});

// --- D3: Wizard with env var API key (SCC-D18 / UC-7) ---

describe("SCC-D18: Wizard accepts API key from env var", () => {
  it("runSetupWizard uses env ANTHROPIC_API_KEY when no apiKey option given", async () => {
    const { runSetupWizard } = await importSetup();
    const tmpDir = makeTmpDir();
    const configDir = join(tmpDir, "config");
    const templateDir = join(tmpDir, "templates");
    mkdirSync(templateDir, { recursive: true });

    const result = await runSetupWizard({
      configDir,
      templateDir,
      env: { ANTHROPIC_API_KEY: "sk-ant-from-env-12345" },
      skipValidation: true,
    });

    // The env-provided key should be used
    const providers = Object.values(result.models.providers);
    expect(providers.length).toBeGreaterThan(0);
    expect(providers[0].apiKey).toBe("sk-ant-from-env-12345");
  });

  it("runSetupWizard uses KLAYOUT_MCP_URL when no mcpUrl option is given", async () => {
    const { runSetupWizard } = await importSetup();
    const tmpDir = makeTmpDir();
    const configDir = join(tmpDir, "config");
    const templateDir = join(tmpDir, "templates");
    mkdirSync(templateDir, { recursive: true });

    const result = await runSetupWizard({
      configDir,
      templateDir,
      apiKey: "sk-ant-test",
      env: { KLAYOUT_MCP_URL: "http://127.0.0.1:8766/mcp" },
      skipValidation: true,
    });

    expect(result.klayout.url).toBe("http://127.0.0.1:8766/mcp");
  });
});

// --- D4: Model selection (SCC-D4) ---

describe("SCC-D4: Wizard model selection from registry", () => {
  it("runSetupWizard with explicit model sets defaultModel in returned config", async () => {
    const { runSetupWizard } = await importSetup();
    const tmpDir = makeTmpDir();
    const configDir = join(tmpDir, "config");
    const templateDir = join(tmpDir, "templates");
    mkdirSync(templateDir, { recursive: true });

    const result = await runSetupWizard({
      configDir,
      templateDir,
      apiKey: "sk-ant-test",
      model: "custom-anthropic/claude-opus-4-6",
      skipValidation: true,
    });

    expect(result.agent.defaultModel).toBe("custom-anthropic/claude-opus-4-6");

    // Verify model.json on disk
    const modelJson = JSON.parse(readFileSync(join(configDir, "model.json"), "utf-8"));
    expect(modelJson.defaultModel).toBe("custom-anthropic/claude-opus-4-6");
  });

  it("runSetupWizard without model option defaults to Sonnet 4.6", async () => {
    const { runSetupWizard } = await importSetup();
    const tmpDir = makeTmpDir();
    const configDir = join(tmpDir, "config");
    const templateDir = join(tmpDir, "templates");
    mkdirSync(templateDir, { recursive: true });

    const result = await runSetupWizard({
      configDir,
      templateDir,
      apiKey: "sk-ant-test",
      skipValidation: true,
    });

    // Default model should be Sonnet 4.6
    expect(result.agent.defaultModel).toContain("claude-sonnet-4-6");
  });
});

// --- D5: Embedding config sets search mode (SCC-D14) ---

describe("SCC-D14: Wizard with embedding creds auto-sets search mode", () => {
  it("runSetupWizard with embeddingUrl+embeddingKey sets fts5+vector+rerank", async () => {
    const { runSetupWizard } = await importSetup();
    const tmpDir = makeTmpDir();
    const configDir = join(tmpDir, "config");
    const templateDir = join(tmpDir, "templates");
    mkdirSync(templateDir, { recursive: true });

    const result = await runSetupWizard({
      configDir,
      templateDir,
      apiKey: "sk-ant-test",
      embeddingUrl: "https://api.openai.com/v1/embeddings",
      embeddingKey: "sk-embed-key-123",
      skipValidation: true,
    });

    // Search mode must be upgraded when embedding creds provided
    expect(result.search.mode).toBe("fts5+vector+rerank");
    expect(result.embedding.baseUrl).toBe("https://api.openai.com/v1/embeddings");
    expect(result.embedding.apiKey).toBe("sk-embed-key-123");

    // Verify settings.json on disk
    const settingsJson = JSON.parse(readFileSync(join(configDir, "settings.json"), "utf-8"));
    expect(settingsJson.search.mode).toBe("fts5+vector+rerank");
    expect(settingsJson.embedding.baseUrl).toBe("https://api.openai.com/v1/embeddings");
  });

  it("runSetupWizard without embedding creds keeps search mode as fts5", async () => {
    const { runSetupWizard } = await importSetup();
    const tmpDir = makeTmpDir();
    const configDir = join(tmpDir, "config");
    const templateDir = join(tmpDir, "templates");
    mkdirSync(templateDir, { recursive: true });

    const result = await runSetupWizard({
      configDir,
      templateDir,
      apiKey: "sk-ant-test",
      skipValidation: true,
      // No embeddingUrl or embeddingKey
    });

    expect(result.search.mode).toBe("fts5");
  });
});

// --- D6: detectKLayout HTTP health check (SCC-D5) ---

describe("SCC-D5: detectKLayout auto-detects KLayout via HTTP", () => {
  it("detectKLayout uses KLAYOUT_MCP_URL when no URL is passed", async () => {
    const { detectKLayout } = await importSetup();
    const http = await import("http");
    const server = http.createServer((_req, res) => {
      res.writeHead(200, { "Content-Type": "application/json" });
      res.end(JSON.stringify({ status: "ok", server: "KlayoutClaw", version: "0.6" }));
    });

    await new Promise<void>((resolve) => {
      server.listen(0, "127.0.0.1", () => resolve());
    });
    const addr = server.address() as { port: number };
    const previous = process.env.KLAYOUT_MCP_URL;
    process.env.KLAYOUT_MCP_URL = `http://127.0.0.1:${addr.port}/mcp`;

    try {
      expect(await detectKLayout()).toBe(true);
    } finally {
      if (previous === undefined) delete process.env.KLAYOUT_MCP_URL;
      else process.env.KLAYOUT_MCP_URL = previous;
      server.close();
    }
  });

  it("detectKLayout returns true for the KlayoutClaw identity response", async () => {
    const { detectKLayout } = await importSetup();
    // Start a minimal HTTP server to simulate KLayout
    const http = await import("http");
    const server = http.createServer((_req, res) => {
      res.writeHead(200, { "Content-Type": "application/json" });
      res.end(JSON.stringify({ status: "ok", server: "KlayoutClaw", version: "0.6" }));
    });

    await new Promise<void>((resolve) => {
      server.listen(0, "127.0.0.1", () => resolve());
    });
    const addr = server.address() as { port: number };

    try {
      const detected = await detectKLayout(`http://127.0.0.1:${addr.port}`);
      expect(detected).toBe(true);
    } finally {
      server.close();
    }
  });

  it.each([
    ["HTML response", "text/html", "<html>not KLayout</html>"],
    ["empty JSON", "application/json", "{}"],
    ["Anki-like JSON-RPC response", "application/json", JSON.stringify({ jsonrpc: "2.0", result: null })],
    ["wrong server", "application/json", JSON.stringify({ status: "ok", server: "AnkiConnect" })],
  ])("detectKLayout rejects an unrelated 200: %s", async (_name, contentType, body) => {
    const { detectKLayout } = await importSetup();
    const http = await import("http");
    const server = http.createServer((_req, res) => {
      res.writeHead(200, { "Content-Type": contentType });
      res.end(body);
    });

    await new Promise<void>((resolve) => {
      server.listen(0, "127.0.0.1", () => resolve());
    });
    const addr = server.address() as { port: number };

    try {
      expect(await detectKLayout(`http://127.0.0.1:${addr.port}/mcp`)).toBe(false);
    } finally {
      server.close();
    }
  });

  it("detectKLayout returns false when no server is running", async () => {
    const { detectKLayout } = await importSetup();
    // Use a port that is almost certainly not listening
    const detected = await detectKLayout("http://127.0.0.1:19999");
    expect(detected).toBe(false);
  });
});

// --- D7: backupConfig creates backup of existing config (SCC-D9, SCC-D19) ---

describe("SCC-D9: backupConfig backs up existing config before re-run", () => {
  it("backupConfig creates timestamped backup directory and returns path", async () => {
    const { backupConfig } = await importSetup();
    const tmpDir = makeTmpDir();
    const configDir = join(tmpDir, "config");
    mkdirSync(configDir, { recursive: true });

    // Write some existing config
    writeFileSync(join(configDir, "model.json"), JSON.stringify({ defaultModel: "old-model" }));
    writeFileSync(join(configDir, "settings.json"), JSON.stringify({ old: true }));

    const backupPath = backupConfig(configDir);

    // Backup path must be returned and exist on disk
    expect(backupPath).toBeTruthy();
    expect(existsSync(backupPath)).toBe(true);

    // The backup must contain the original files
    expect(existsSync(join(backupPath, "model.json"))).toBe(true);
    const backedUp = JSON.parse(readFileSync(join(backupPath, "model.json"), "utf-8"));
    expect(backedUp.defaultModel).toBe("old-model");
  });

  it("backupConfig + wizard removes stale klayout_mcp from mcp.json (SCC-D19)", async () => {
    const { runSetupWizard, backupConfig } = await importSetup();
    const tmpDir = makeTmpDir();
    const configDir = join(tmpDir, "config");
    const templateDir = join(tmpDir, "templates");
    mkdirSync(configDir, { recursive: true });
    mkdirSync(templateDir, { recursive: true });

    // Old format: klayout_mcp in mcp.json (legacy)
    writeFileSync(
      join(configDir, "mcp.json"),
      JSON.stringify({ klayout_mcp: { url: "http://127.0.0.1:8765/mcp", required: true } }),
    );
    writeFileSync(join(configDir, "model.json"), JSON.stringify({ defaultModel: "old" }));
    writeFileSync(join(configDir, "settings.json"), JSON.stringify({}));

    // Step 1: backup
    const backupPath = backupConfig(configDir);
    expect(existsSync(backupPath)).toBe(true);

    // Step 2: re-run wizard
    const result = await runSetupWizard({
      configDir,
      templateDir,
      apiKey: "sk-ant-test",
      skipValidation: true,
    });

    // The new mcp.json must NOT contain klayout_mcp (stale key removed)
    const mcpJson = JSON.parse(readFileSync(join(configDir, "mcp.json"), "utf-8"));
    expect(mcpJson.klayout_mcp).toBeUndefined();

    // KLayout config should be in klayout.json instead
    expect(existsSync(join(configDir, "klayout.json"))).toBe(true);
    expect(result.klayout.url).toBe("http://127.0.0.1:8765/mcp");
  });
});

// --- D8: initializeUserDir writes subagent templates (SCC-D10, SCC-D11) ---

describe("SCC-D10/D11: initializeUserDir writes subagent templates and defaults", () => {
  it("initializeUserDir creates workspace/subagent/*.md templates", async () => {
    const { initializeUserDir } = await importConfig();
    const tmpDir = makeTmpDir();
    const templateDir = join(tmpDir, "templates");
    mkdirSync(templateDir, { recursive: true });

    // Create subagent templates in the template source dir
    const subagentTemplateDir = join(templateDir, "subagent");
    mkdirSync(subagentTemplateDir, { recursive: true });
    writeFileSync(join(subagentTemplateDir, "scout.md"), "# Scout\nRecon agent");
    writeFileSync(join(subagentTemplateDir, "designer.md"), "# Designer\nDesign agent");
    writeFileSync(join(subagentTemplateDir, "analyst.md"), "# Analyst\nAnalysis agent");
    writeFileSync(join(subagentTemplateDir, "planner.md"), "# Planner\nPlanning agent");

    // initializeUserDir should copy them
    // Note: initializeUserDir writes to ~/.qlaybot/ which we can't control for unit tests,
    // so we test the updated version that accepts a custom base dir
    initializeUserDir(templateDir, tmpDir);

    // Verify subagent templates were copied to workspace/subagent/
    const workspaceDir = join(tmpDir, "workspace", "subagent");
    expect(existsSync(join(workspaceDir, "scout.md"))).toBe(true);
    expect(existsSync(join(workspaceDir, "designer.md"))).toBe(true);
    expect(existsSync(join(workspaceDir, "analyst.md"))).toBe(true);
    expect(existsSync(join(workspaceDir, "planner.md"))).toBe(true);

    // Verify content was preserved
    const scoutContent = readFileSync(join(workspaceDir, "scout.md"), "utf-8");
    expect(scoutContent).toContain("Scout");
  });

  it("initializeUserDir writes subagent defaults to settings.json (SCC-D11)", async () => {
    const { initializeUserDir } = await importConfig();
    const tmpDir = makeTmpDir();
    const templateDir = join(tmpDir, "templates");
    mkdirSync(templateDir, { recursive: true });

    initializeUserDir(templateDir, tmpDir);

    const settingsPath = join(tmpDir, "config", "settings.json");
    expect(existsSync(settingsPath)).toBe(true);

    const settings = JSON.parse(readFileSync(settingsPath, "utf-8"));

    // Must include subagent defaults
    expect(settings.subagent).toBeDefined();
    expect(settings.subagent.enabled).toBe(true);

    // Must include search defaults
    expect(settings.search).toBeDefined();
    expect(settings.search.mode).toBe("fts5");

    // Must include embedding defaults
    expect(settings.embedding).toBeDefined();
    expect(settings.embedding.api).toBe("openai-embeddings");
  });
});

// --- D9: Corrupted config recovery (SCC-D21 / UC-E3) ---

describe("SCC-D21: /config setup recovery from corrupted config", () => {
  it("backupConfig + wizard recovers from corrupted settings.json", async () => {
    const { runSetupWizard, backupConfig } = await importSetup();
    const tmpDir = makeTmpDir();
    const configDir = join(tmpDir, "config");
    const templateDir = join(tmpDir, "templates");
    mkdirSync(configDir, { recursive: true });
    mkdirSync(templateDir, { recursive: true });

    // Write corrupted settings.json (invalid JSON)
    writeFileSync(join(configDir, "settings.json"), "{corrupted: not valid json!!!");
    writeFileSync(join(configDir, "model.json"), JSON.stringify({ defaultModel: "old" }));
    writeFileSync(join(configDir, "mcp.json"), "{}");

    // Step 1: backup should succeed even with corrupted files (it copies bytes, doesn't parse)
    const backupPath = backupConfig(configDir);
    expect(existsSync(backupPath)).toBe(true);
    expect(existsSync(join(backupPath, "settings.json"))).toBe(true);

    // Step 2: wizard should write fresh, valid config
    const result = await runSetupWizard({
      configDir,
      templateDir,
      apiKey: "sk-ant-recovery-test",
      skipValidation: true,
    });

    // The new settings.json must be valid JSON
    const settingsRaw = readFileSync(join(configDir, "settings.json"), "utf-8");
    expect(() => JSON.parse(settingsRaw)).not.toThrow();

    // The returned config must be usable
    expect(result.agent).toBeDefined();
    expect(result.memory).toBeDefined();
    expect(result.compaction).toBeDefined();
  });
});

// ==========================================================================
// Phase D (continued): Cross-review gap tests — SCC items D3, D12, D1, D15/D16, D17, D20
// ==========================================================================

// Extended dynamic import for new setup.ts exports
async function importSetupExtended() {
  return import("../src/setup.js") as Promise<{
    runSetupWizard: typeof import("../src/setup.js")["runSetupWizard"];
    detectKLayout: typeof import("../src/setup.js")["detectKLayout"];
    backupConfig: typeof import("../src/setup.js")["backupConfig"];
    maskApiKey: typeof import("../src/setup.js")["maskApiKey"];
    validateApiKey: typeof import("../src/setup.js")["validateApiKey"];
  }>;
}

// --- D10: SCC-D3 rejection path — wizard MUST reject missing API key ---

describe("SCC-D3 rejection: Wizard rejects when no API key provided", () => {
  it("runSetupWizard with no apiKey and no env var throws/rejects", async () => {
    const { runSetupWizard } = await importSetupExtended();
    const tmpDir = makeTmpDir();
    const configDir = join(tmpDir, "config");
    const templateDir = join(tmpDir, "templates");
    mkdirSync(templateDir, { recursive: true });

    // No apiKey option, no env var — wizard must reject
    await expect(
      runSetupWizard({
        configDir,
        templateDir,
        skipValidation: true,
        env: {}, // explicitly empty, no ANTHROPIC_API_KEY
      }),
    ).rejects.toThrow();
  });

  it("runSetupWizard with empty string apiKey throws/rejects", async () => {
    const { runSetupWizard } = await importSetupExtended();
    const tmpDir = makeTmpDir();
    const configDir = join(tmpDir, "config");
    const templateDir = join(tmpDir, "templates");
    mkdirSync(templateDir, { recursive: true });

    await expect(
      runSetupWizard({
        configDir,
        templateDir,
        apiKey: "",
        skipValidation: true,
      }),
    ).rejects.toThrow();
  });
});

// --- D11: SCC-D12 — Non-interactive mode (JSON/RPC path) ---

describe("SCC-D12: Non-interactive initializeUserDir produces loadable config", () => {
  it("initializeUserDir creates config files that loadConfig can parse", async () => {
    const { initializeUserDir, loadConfig: loadCfg } = await importConfig();
    const tmpDir = makeTmpDir();
    const templateDir = join(tmpDir, "templates");
    mkdirSync(templateDir, { recursive: true });

    // Simulate the non-interactive path: initializeUserDir without wizard
    initializeUserDir(templateDir, tmpDir);

    const configDir = join(tmpDir, "config");

    // All config files must exist
    expect(existsSync(join(configDir, "model.json"))).toBe(true);
    expect(existsSync(join(configDir, "settings.json"))).toBe(true);

    // loadConfig must be able to parse them without throwing
    const config = loadCfg(configDir);
    expect(config).toBeDefined();
    expect(config.agent).toBeDefined();
    expect(config.agent.defaultModel).toBeTruthy();
    expect(config.memory).toBeDefined();
  });
});

// --- D12: SCC-D1 — Key masking utility ---

describe("SCC-D1: maskApiKey hides the full key", () => {
  it("masks a standard Anthropic key so full key is not visible", async () => {
    const { maskApiKey } = await importSetupExtended();
    const key = "sk-ant-api03-abc123xyz789def456";
    const masked = maskApiKey(key);

    // The full key must NOT appear in the masked output
    expect(masked).not.toBe(key);
    expect(masked).not.toContain("abc123xyz789def456");

    // The masked output must be non-empty
    expect(masked.length).toBeGreaterThan(0);

    // Should retain some prefix or suffix hint (at least 3 chars from original)
    const hasPartialHint =
      masked.includes("sk-") || masked.includes(key.slice(-3));
    expect(hasPartialHint).toBe(true);
  });

  it("masks a short key without crashing", async () => {
    const { maskApiKey } = await importSetupExtended();
    const key = "sk-short";
    const masked = maskApiKey(key);

    expect(masked).not.toBe(key);
    expect(masked.length).toBeGreaterThan(0);
  });

  it("returns a placeholder for empty string", async () => {
    const { maskApiKey } = await importSetupExtended();
    const masked = maskApiKey("");

    // Should not throw, should return something non-empty
    expect(masked).toBeTruthy();
  });
});

// --- D13: SCC-D15/D16 — CLI parseArgs routes "setup" and "onboard" ---

describe("SCC-D15/D16: CLI routes setup and onboard to wizard", () => {
  // parseArgs is not exported from cli.ts, so we test the routing at integration level.
  // The Executor must either export parseArgs or add a "setup" alias in the switch.
  // We dynamically import cli.ts internals via a test-only export.

  it("parseArgs maps 'setup' to the onboard/setup command", async () => {
    // Import parseArgs — Executor must export it from cli.ts
    const { parseArgs } = await import("../src/cli.js") as {
      parseArgs: (argv: string[]) => { command: string };
    };

    const result = parseArgs(["node", "qlaybot", "setup"]);
    // "setup" must route to the setup/onboard command (either "setup" or "onboard")
    expect(["setup", "onboard"]).toContain(result.command);
  });

  it("parseArgs maps 'onboard' to the setup command", async () => {
    const { parseArgs } = await import("../src/cli.js") as {
      parseArgs: (argv: string[]) => { command: string };
    };

    const result = parseArgs(["node", "qlaybot", "onboard"]);
    expect(["setup", "onboard"]).toContain(result.command);
  });

  it("both 'setup' and 'onboard' route to the same command value", async () => {
    const { parseArgs } = await import("../src/cli.js") as {
      parseArgs: (argv: string[]) => { command: string };
    };

    const setupResult = parseArgs(["node", "qlaybot", "setup"]);
    const onboardResult = parseArgs(["node", "qlaybot", "onboard"]);
    expect(setupResult.command).toBe(onboardResult.command);
  });
});

// --- D14: SCC-D17 — /config setup triggers backup + wizard ---

describe("SCC-D17: /config setup triggers backup then wizard", () => {
  it("/config setup on existing config creates backup and writes new config", async () => {
    const { runSetupWizard, backupConfig } = await importSetupExtended();

    // We need the config command handler to support "setup" subcommand.
    // Import it and invoke execute(["setup"], context).
    const { configCommand } = await import("../src/commands/config.js");

    const tmpDir = makeTmpDir();
    const configDir = join(tmpDir, "config");
    const templateDir = join(tmpDir, "templates");
    mkdirSync(configDir, { recursive: true });
    mkdirSync(templateDir, { recursive: true });

    // Write existing config that should be backed up
    writeFileSync(
      join(configDir, "model.json"),
      JSON.stringify({ defaultModel: "old-model-before-setup" }),
    );
    writeFileSync(
      join(configDir, "settings.json"),
      JSON.stringify({ memory: { autoRecall: { enabled: true } } }),
    );

    // Build a minimal command context with configDir and templateDir
    // The Executor must make /config setup accept these via context or options
    const context = {
      session: {
        config: {
          agent: { defaultModel: "old-model-before-setup", thinkingLevel: "high" as const },
          models: { providers: {} },
          mcp: {},
          mcpTimeouts: { requestMs: 5000, toolExecutionMs: 300000, healthCheckMs: 5000, autoLaunchDelaysMs: [1000] },
          tui: { contextPollMs: 5000 },
          memory: { autoRecall: { enabled: true, maxResults: 3, minReindexMs: 5000 }, budget: { maxEntriesPerCategory: 500, maxFileSizeBytes: 524288 } },
          compaction: { enabled: true, autoThreshold: 90, warningThreshold: 70, toolResultPruning: { keepRecentResults: 3, minResultSizeBytes: 500, neverPruneTools: [] } },
        },
        configDir,
        templateDir,
      },
      mode: "shell" as const,
      apiKey: "sk-ant-test-setup-cmd",
    };

    const result = await configCommand.execute(["setup"], context as any);

    // The command must succeed (exitCode 0 or no exitCode)
    expect(result.exitCode === undefined || result.exitCode === 0).toBe(true);

    // A backup directory must have been created somewhere under the tmpDir
    const { readdirSync } = await import("fs");
    const entries = readdirSync(tmpDir);
    const backupDirs = entries.filter(
      (e) => e.startsWith("config-backup") || e.startsWith("backup"),
    );
    // There should be at least one backup dir, OR the backup lives inside configDir's parent
    const parentEntries = readdirSync(join(configDir, ".."));
    const anyBackup = parentEntries.some(
      (e) => e.includes("backup") || e.includes("bak"),
    );
    expect(anyBackup || backupDirs.length > 0).toBe(true);

    // New config must be written (model.json should exist with new content)
    expect(existsSync(join(configDir, "model.json"))).toBe(true);
    const newModel = JSON.parse(readFileSync(join(configDir, "model.json"), "utf-8"));
    // The wizard should have written fresh config (not the old "old-model-before-setup")
    expect(newModel).toBeDefined();
  });
});

// --- D15: SCC-D20 — Invalid API key validation ---

describe("SCC-D20: validateApiKey rejects invalid keys", () => {
  it("validates empty string as invalid", async () => {
    const { validateApiKey } = await importSetupExtended();

    const result = await validateApiKey("");
    expect(result.valid).toBe(false);
    expect(result.error).toBeTruthy();
  });

  it("validates whitespace-only string as invalid", async () => {
    const { validateApiKey } = await importSetupExtended();

    const result = await validateApiKey("   ");
    expect(result.valid).toBe(false);
    expect(result.error).toBeTruthy();
  });

  it("rejects key when server returns 401 (skipValidation=false)", async () => {
    const { validateApiKey } = await importSetupExtended();

    // Start a mock server that returns 401 for any request
    const http = await import("http");
    const server = http.createServer((_req, res) => {
      res.writeHead(401, { "Content-Type": "application/json" });
      res.end(JSON.stringify({ error: { type: "authentication_error", message: "Invalid API key" } }));
    });

    await new Promise<void>((resolve) => {
      server.listen(0, "127.0.0.1", () => resolve());
    });
    const addr = server.address() as { port: number };

    try {
      const result = await validateApiKey("sk-ant-bad-key-12345", {
        baseUrl: `http://127.0.0.1:${addr.port}`,
      });
      expect(result.valid).toBe(false);
      expect(result.error).toBeTruthy();
    } finally {
      server.close();
    }
  });

  it("accepts a plausible key format when skipValidation is true", async () => {
    const { validateApiKey } = await importSetupExtended();

    const result = await validateApiKey("sk-ant-api03-valid-looking-key", {
      skipValidation: true,
    });
    expect(result.valid).toBe(true);
    expect(result.error).toBeUndefined();
  });
});

// Issue #17: the client tool-execution timeout must not abort a long route/eval
// the server is still legitimately running (which surfaces as a spurious
// "fetch failed"). The client must wait at least as long as the server's
// LARGEST subprocess clamp (evaluate_design caps at 900s).
describe("SCC-A17: client tool timeout >= server subprocess cap", () => {
  const SERVER_MAX_SUBPROCESS_MS = 900_000; // evaluate_design clamp in the .lym

  it("DEFAULT_TOOL_TIMEOUT_MS is at least the server's max subprocess timeout", async () => {
    const { DEFAULT_TOOL_TIMEOUT_MS } = await import("../src/mcp/klayout-client.js");
    expect(DEFAULT_TOOL_TIMEOUT_MS).toBeGreaterThanOrEqual(SERVER_MAX_SUBPROCESS_MS);
  });

  it("default config mcpTimeouts.toolExecutionMs is at least the server cap", () => {
    // fresh empty config dir -> production defaults from defaultConfig()
    const cfg = loadConfig(makeTmpDir());
    expect(cfg.mcpTimeouts.toolExecutionMs).toBeGreaterThanOrEqual(SERVER_MAX_SUBPROCESS_MS);
  });
});
