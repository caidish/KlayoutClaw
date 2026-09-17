/**
 * Shared test instance factories for v0.4 integration/E2E tests.
 *
 * All tests use REAL APIs. Credentials loaded from ~/.keys/api_settings.md.
 * No mocks.
 */

import { readFileSync, existsSync } from "fs";
import { join } from "path";
import { homedir } from "os";
import type { QlayBotConfig } from "../../src/config.js";
import { makeConfig } from "./config-builder.js";

const API_SETTINGS_PATH = join(homedir(), ".keys", "api_settings.md");

interface ParsedCredentials {
  anthropicBaseUrl?: string;
  anthropicApiKey?: string;
  openaiBaseUrl?: string;
  openaiApiKey?: string;
  embeddingBaseUrl?: string;
  embeddingApiKey?: string;
}

function parseApiSettings(): ParsedCredentials {
  if (!existsSync(API_SETTINGS_PATH)) {
    return {};
  }
  const content = readFileSync(API_SETTINGS_PATH, "utf-8");
  const creds: ParsedCredentials = {};

  // Parse key-value pairs from markdown
  const lines = content.split("\n");
  let currentSection = "";

  for (const line of lines) {
    if (line.startsWith("##")) {
      currentSection = line.toLowerCase();
    }

    const kvMatch = line.match(
      /[-*]\s*(?:Base URL|API key|API Key|base_url|api_key):\s*`?([^`\s]+)`?/i,
    );
    if (kvMatch) {
      const value = kvMatch[1];

      if (currentSection.includes("anthropic")) {
        if (line.toLowerCase().includes("base url") || line.includes("base_url")) {
          creds.anthropicBaseUrl = value;
        } else if (line.toLowerCase().includes("api key") || line.includes("api_key")) {
          creds.anthropicApiKey = value;
        }
      } else if (currentSection.includes("openai") && currentSection.includes("embed")) {
        if (line.toLowerCase().includes("base url") || line.includes("base_url")) {
          creds.embeddingBaseUrl = value;
        } else if (line.toLowerCase().includes("api key") || line.includes("api_key")) {
          creds.embeddingApiKey = value;
        }
      } else if (currentSection.includes("openai")) {
        if (line.toLowerCase().includes("base url") || line.includes("base_url")) {
          creds.openaiBaseUrl = value;
        } else if (line.toLowerCase().includes("api key") || line.includes("api_key")) {
          creds.openaiApiKey = value;
        }
      }
    }
  }

  return creds;
}

let cachedCreds: ParsedCredentials | null = null;

function getCredentials(): ParsedCredentials {
  if (!cachedCreds) {
    cachedCreds = parseApiSettings();
  }
  return cachedCreds;
}

export function createTestConfig(
  overrides?: Parameters<typeof makeConfig>[0],
): ReturnType<typeof makeConfig> {
  const creds = getCredentials();

  return makeConfig({
    ...overrides,
    agent: {
      defaultModel: "custom-anthropic/claude-sonnet-4-6",
      thinkingLevel: "high",
      ...overrides?.agent,
    },
    models: {
      providers: {
        "custom-anthropic": {
          baseUrl: creds.anthropicBaseUrl ?? "https://bench.physcai.com/api",
          apiKey:
            creds.anthropicApiKey ?? process.env.ANTHROPIC_API_KEY ?? "",
          api: "anthropic-messages",
          models: [
            {
              id: "claude-sonnet-4-6",
              name: "Claude Sonnet 4.6",
              reasoning: true,
              input: ["text", "image"],
              cost: {
                input: 3,
                output: 15,
                cacheRead: 0.3,
                cacheWrite: 3.75,
              },
              contextWindow: 200000,
              maxTokens: 65536,
            },
          ],
        },
        ...overrides?.models?.providers,
      },
    },
    embedding: {
      api: "openai-embeddings",
      baseUrl: creds.embeddingBaseUrl ?? "",
      apiKey: creds.embeddingApiKey ?? "",
      model: "text-embedding-3-small",
      dimensions: 1536,
      similarityThreshold: 0.3,
      ...overrides?.embedding,
    },
  });
}

/**
 * Create a real MCPManager connected to KLAYOUT_MCP_URL (default :8765) if available.
 * Callers should handle connection failure gracefully.
 */
export async function createTestMcpManager(): Promise<unknown> {
  // Dynamic import to avoid circular dependencies during contract tests
  const { MCPManager } = await import("../../src/mcp/manager.js");
  const config = createTestConfig();
  return new MCPManager(config);
}

/**
 * Create a real MemoryManager with disk-based SQLite in the given tmpDir.
 */
export async function createTestMemoryManager(
  tmpDir: string,
): Promise<unknown> {
  const { MemoryManager } = await import("../../src/memory/index.js");
  const memDir = join(tmpDir, "memory");
  return new MemoryManager(memDir);
}

/**
 * Create a real SubagentRunner with EventEmitter.
 * Stub: returns a placeholder until SubagentRunner is implemented in Phase E.
 */
export function createTestSubagentRunner(
  _config: ReturnType<typeof makeConfig>,
): { run: (opts: unknown) => Promise<unknown>; on: (event: string, cb: (...args: unknown[]) => void) => void } {
  // Phase 0 stub — real implementation comes in Phase E
  return {
    run: async () => ({ status: "error", errorMessage: "SubagentRunner not yet implemented" }),
    on: () => {},
  };
}
