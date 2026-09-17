/**
 * Setup wizard for qlaybot — Phase D.
 *
 * Provides interactive and non-interactive setup, API key validation,
 * KLayout detection, and config backup.
 */

import { existsSync, mkdirSync, cpSync } from "fs";
import { join, dirname, basename } from "path";
import http from "http";
import type { QlayBotConfig } from "./config.js";
import {
  saveModelConfig,
  saveKLayoutConfig,
  saveMCPConfig,
  saveSettingsConfig,
} from "./config.js";
import { DEFAULT_COMPACTION_CONFIG } from "./compaction/index.js";

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

export type WizardStep =
  | "api-key"
  | "model"
  | "klayout"
  | "embedding"
  | "confirm";

export interface WizardOptions {
  configDir: string;
  templateDir: string;
  apiKey?: string;
  model?: string;
  mcpUrl?: string;
  embeddingUrl?: string;
  embeddingKey?: string;
  skipValidation?: boolean;
  env?: Record<string, string>;
}

// ---------------------------------------------------------------------------
// maskApiKey
// ---------------------------------------------------------------------------

/**
 * Mask an API key for safe display.
 * Shows a prefix + "..." + last 3 chars, or a placeholder for empty keys.
 */
export function maskApiKey(key: string): string {
  if (!key || key.length === 0) {
    return "(not set)";
  }
  if (key.length <= 6) {
    return key.slice(0, 2) + "...";
  }
  // Show first 6 chars + ... + last 3 chars
  return key.slice(0, 6) + "..." + key.slice(-3);
}

// ---------------------------------------------------------------------------
// validateApiKey
// ---------------------------------------------------------------------------

/**
 * Validate an API key — format check and optional network validation.
 */
export async function validateApiKey(
  key: string,
  opts?: { baseUrl?: string; skipValidation?: boolean },
): Promise<{ valid: boolean; error?: string }> {
  const trimmed = key.trim();

  if (!trimmed) {
    return { valid: false, error: "API key is empty" };
  }

  if (opts?.skipValidation) {
    return { valid: true };
  }

  // Network validation: POST to the messages endpoint
  const baseUrl = opts?.baseUrl ?? "https://api.anthropic.com";

  try {
    const url = new URL("/v1/messages", baseUrl);
    const postData = JSON.stringify({
      model: "claude-sonnet-4-6",
      max_tokens: 1,
      messages: [{ role: "user", content: "hi" }],
    });

    const result = await new Promise<{ statusCode: number }>((resolve, reject) => {
      const req = http.request(
        {
          hostname: url.hostname,
          port: url.port || (url.protocol === "https:" ? 443 : 80),
          path: url.pathname,
          method: "POST",
          headers: {
            "Content-Type": "application/json",
            "x-api-key": trimmed,
            "anthropic-version": "2023-06-01",
          },
          timeout: 5000,
        },
        (res) => {
          // Consume response body
          res.resume();
          resolve({ statusCode: res.statusCode ?? 0 });
        },
      );
      req.on("error", reject);
      req.on("timeout", () => {
        req.destroy();
        reject(new Error("Timeout"));
      });
      req.write(postData);
      req.end();
    });

    if (result.statusCode === 401) {
      return { valid: false, error: "Invalid API key (401 Unauthorized)" };
    }

    // Any non-401 response means the key was accepted (even 400, 429, etc.)
    return { valid: true };
  } catch {
    return { valid: false, error: "Could not reach API server" };
  }
}

// ---------------------------------------------------------------------------
// detectKLayout
// ---------------------------------------------------------------------------

/**
 * Check if KLayout MCP server is reachable at the given URL.
 */
export async function detectKLayout(url?: string): Promise<boolean> {
  const target = url ?? process.env.KLAYOUT_MCP_URL ?? "http://127.0.0.1:8765/mcp";

  try {
    const parsed = new URL(target);
    const result = await new Promise<boolean>((resolve) => {
      const req = http.get(
        {
          hostname: parsed.hostname,
          port: parsed.port || 80,
          path: parsed.pathname,
          timeout: 3000,
        },
        (res) => {
          if (res.statusCode !== 200) {
            res.resume();
            resolve(false);
            return;
          }
          res.setEncoding("utf8");
          let body = "";
          res.on("data", (chunk: string) => {
            body += chunk;
            if (body.length > 64 * 1024) {
              req.destroy();
              resolve(false);
            }
          });
          res.on("end", () => {
            try {
              const data = JSON.parse(body) as { status?: unknown; server?: unknown };
              resolve(data.status === "ok" && data.server === "KlayoutClaw");
            } catch {
              resolve(false);
            }
          });
        },
      );
      req.on("error", () => resolve(false));
      req.on("timeout", () => {
        req.destroy();
        resolve(false);
      });
    });
    return result;
  } catch {
    return false;
  }
}

// ---------------------------------------------------------------------------
// backupConfig
// ---------------------------------------------------------------------------

/**
 * Back up an existing config directory to a timestamped sibling.
 * Returns the backup directory path.
 */
export function backupConfig(configDir: string): string {
  const timestamp = new Date().toISOString().replace(/[:.]/g, "-");
  const parent = dirname(configDir);
  const base = basename(configDir);
  const backupDir = join(parent, `${base}-backup-${timestamp}`);

  mkdirSync(backupDir, { recursive: true });

  if (existsSync(configDir)) {
    cpSync(configDir, backupDir, { recursive: true, force: true });
  }

  return backupDir;
}

// ---------------------------------------------------------------------------
// runSetupWizard
// ---------------------------------------------------------------------------

/**
 * Run the setup wizard (non-interactive programmatic path).
 *
 * Resolves the API key, model, and embedding config, then writes
 * all 4 config files and returns the full QlayBotConfig.
 */
export async function runSetupWizard(options: WizardOptions): Promise<QlayBotConfig> {
  const {
    configDir,
    templateDir,
    apiKey: providedKey,
    model,
    mcpUrl,
    embeddingUrl,
    embeddingKey,
    skipValidation,
    env,
  } = options;

  // --- Resolve API key ---
  const envVars = env ?? process.env;
  let apiKey = providedKey;

  if (apiKey === undefined || apiKey === null) {
    // Try env var
    const envKey = envVars.ANTHROPIC_API_KEY;
    if (envKey) {
      apiKey = envKey;
    }
  }

  if (!apiKey) {
    throw new Error("No API key provided. Set ANTHROPIC_API_KEY or pass apiKey option.");
  }

  // --- Resolve model ---
  const defaultModel = model ?? "custom-anthropic/claude-sonnet-4-6";

  // --- Build full config ---
  const config: QlayBotConfig = {
    agent: {
      defaultModel,
      thinkingLevel: "high",
    },
    models: {
      providers: {
        "custom-anthropic": {
          baseUrl: "https://bench.physcai.com/api",
          apiKey,
          api: "anthropic-messages",
          models: [
            {
              id: "claude-sonnet-4-6",
              name: "Claude Sonnet 4.6",
              reasoning: true,
              input: ["text", "image"],
              cost: { input: 3, output: 15, cacheRead: 0.3, cacheWrite: 3.75 },
              contextWindow: 200000,
              maxTokens: 65536,
            },
            {
              id: "claude-opus-4-6",
              name: "Claude Opus 4.6",
              reasoning: true,
              input: ["text", "image"],
              cost: { input: 15, output: 75, cacheRead: 1.5, cacheWrite: 18.75 },
              contextWindow: 1000000,
              maxTokens: 65536,
            },
            {
              id: "claude-sonnet-4-5-20250929",
              name: "Claude Sonnet 4.5",
              reasoning: true,
              input: ["text", "image"],
              cost: { input: 3, output: 15, cacheRead: 0.3, cacheWrite: 3.75 },
              contextWindow: 200000,
              maxTokens: 65536,
            },
            {
              id: "claude-haiku-4-5-20251001",
              name: "Claude Haiku 4.5",
              reasoning: false,
              input: ["text", "image"],
              cost: { input: 0.8, output: 4, cacheRead: 0.08, cacheWrite: 1 },
              contextWindow: 200000,
              maxTokens: 65536,
            },
          ],
        },
      },
    },
    autoApprovePlans: true,
    mcp: {},
    mcpTimeouts: {
      requestMs: 5000,
      toolExecutionMs: 300000,
      healthCheckMs: 5000,
      autoLaunchDelaysMs: [1000, 2000, 4000, 8000],
    },
    tui: {
      contextPollMs: 5000,
    },
    memory: {
      autoRecall: { enabled: true, maxResults: 3, minReindexMs: 5000 },
      budget: { maxEntriesPerCategory: 500, maxFileSizeBytes: 512 * 1024 },
    },
    compaction: { ...DEFAULT_COMPACTION_CONFIG },
    klayout: {
      url: mcpUrl ?? envVars.KLAYOUT_MCP_URL ?? "http://127.0.0.1:8765/mcp",
      required: true,
      autoLaunch: true,
      disabledTools: [],
    },
    subagent: {
      enabled: true,
      logDir: join(configDir, "..", "subagent-logs"),
      maxLogFiles: 100,
      roles: {},
    },
    search: {
      mode: embeddingUrl && embeddingKey ? "fts5+vector+rerank" : "fts5",
      minRerank: 4,
      rerankMinScore: 0.3,
      rerankMaxTokens: 256,
    },
    embedding: {
      api: "openai-embeddings",
      baseUrl: embeddingUrl ?? "",
      apiKey: embeddingKey ?? "",
      model: "text-embedding-3-small",
      dimensions: 1536,
      similarityThreshold: 0.3,
    },
    plan: {
      reinjectionInterval: 3,
    },
    verbose: false,
  };

  // --- Write all 4 config files ---
  mkdirSync(configDir, { recursive: true });
  saveModelConfig(config, configDir);
  saveKLayoutConfig(config, configDir);
  saveMCPConfig(config, configDir);
  saveSettingsConfig(config, configDir);

  return config;
}
