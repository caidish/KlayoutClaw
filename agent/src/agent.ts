/**
 * Pi-Agent SDK session wrapper for qlaybot.
 * Constructs Agent + AgentSession directly with custom tools,
 * system prompt, MCP manager, and event handling.
 */

import { Agent } from "@mariozechner/pi-agent-core";
import type { AgentMessage } from "@mariozechner/pi-agent-core";
import type { Api } from "@mariozechner/pi-ai";
import {
  AgentSession,
  SessionManager,
  SettingsManager,
  DefaultResourceLoader,
  AuthStorage,
  ModelRegistry,
  convertToLlm,
} from "@mariozechner/pi-coding-agent";

import {
  loadConfig,
  getQlayBotDir,
  getSessionsDir,
  getWorkspaceDir,
  getMemoryDir,
  resolveModel,
  applyModelHeaders,
  isInitialized,
  getAllMCPServers,
  type QlayBotConfig,
} from "./config.js";
import { buildSystemPrompt, PromptMode } from "./prompts/index.js";
import { assembleTools } from "./tools/index.js";
import { TOOL_ANNOTATIONS } from "./tools/annotations.js";
import { SubagentRunner } from "./subagent/runner.js";
import { createDelegateTool } from "./tools/delegate.js";
import { MCPManager } from "./mcp/manager.js";
import { MemoryManager } from "./memory/index.js";
import { createEmbedder } from "./memory/embedder.js";
import { createAutoRecallTransform, defaultAutoRecallConfig } from "./memory/auto-recall.js";
import { InteractionHistory } from "./history.js";
import { VerboseTranscriptWriter } from "./verbose-transcript.js";
import {
  TranscriptMarkerEmitter,
  setTranscriptMarkerEmitter,
} from "./events/marker-emitter.js";
import type { TranscriptMarker } from "./events/marker-types.js";
import { CommandRegistry, createCommandRegistry } from "./commands/index.js";
import { PlanManager } from "./planning/index.js";
import { PlanStateMachine } from "./planning/state-machine.js";
import { createToolResultPruner } from "./compaction/tool-result-pruner.js";
import { createStateLoaderTransform } from "./compaction/state-loader.js";
import {
  createPlanReinjector,
  prunePlanReinjections,
} from "./compaction/plan-reinjector.js";
import { resolveCompactionConfig, type CompactionConfig } from "./compaction/index.js";
import { extractStateFiles } from "./compaction/state-extractor.js";
import { buildCompactInstructions } from "./compaction/prompt-loader.js";
import { BackgroundTaskManager, BACKGROUNDABLE_TOOLS } from "./background/index.js";
import { createBackgroundStatusTool, createBackgroundResultTool } from "./tools/background.js";
import { resolve, dirname, join } from "path";
import { fileURLToPath } from "url";
import { tmpdir } from "os";

export interface CreateDesignSessionOptions {
  cwd?: string;
  sessionId?: string;
  model?: string;
  thinkingLevel?: "off" | "minimal" | "low" | "medium" | "high" | "xhigh";
  ephemeral?: boolean;
  promptMode?: PromptMode;
  headless?: boolean;
  /** §4.4 runtime-ephemeral verbose flag. CLI --verbose → createDesignSession. */
  verbose?: boolean;
}

export interface QlayBotSession {
  session: AgentSession;
  sessionManager: SessionManager;
  config: QlayBotConfig;
  mcpManager: MCPManager;
  memoryManager: MemoryManager;
  subagentRunner: SubagentRunner | null;
  history: InteractionHistory;
  commandRegistry: CommandRegistry | null;
  planManager: PlanManager | null;
  backgroundTaskManager: BackgroundTaskManager | null;
  compactionConfig: CompactionConfig;
  assembledSystemPrompt: string;
  compact: (userInstructions?: string) => Promise<void>;
  getContextUsage: () => { tokens: number; contextWindow: number; percent: number } | undefined;
  dispose: () => Promise<void>;
}

/**
 * Create a qlaybot agent session with direct AgentSession construction.
 */
export async function createDesignSession(
  opts: CreateDesignSessionOptions = {},
): Promise<QlayBotSession> {
  const cwd = opts.cwd ?? process.cwd();
  const agentDir = getQlayBotDir();
  const config = loadConfig();
  // Propagate CLI --verbose override into runtime config. CLI wins. The
  // field is never persisted to settings.json (§4.4 runtime-ephemeral).
  if (opts.verbose === true) {
    config.verbose = true;
  }
  const verbose = config.verbose === true;
  const compactionConfig = resolveCompactionConfig(config.compaction);
  const mode = opts.promptMode ?? PromptMode.Full;
  if (config.autoApprovePlans === false) {
    console.warn(
      "autoApprovePlans=false is v0.4.5 (OQ-7); treating as true",
    );
    config.autoApprovePlans = true;
  }

  // --- Session & Settings Managers ---
  let sessionManager: SessionManager;
  if (opts.ephemeral) {
    sessionManager = SessionManager.inMemory(cwd);
  } else {
    if (!isInitialized()) {
      // Auto-initialize on first run
      const templateDir = getDefaultWorkspaceTemplate();
      const { initializeUserDir } = await import("./config.js");
      initializeUserDir(templateDir);
    }
    sessionManager = SessionManager.create(cwd, getSessionsDir());
  }

  const settingsManager = opts.ephemeral
    ? SettingsManager.inMemory({
        defaultThinkingLevel: opts.thinkingLevel ?? config.agent.thinkingLevel,
      })
    : SettingsManager.create(cwd, agentDir);

  // --- Auth & Model Registry ---
  // v0.53+: AuthStorage constructor is private; use static factories.
  // v0.64+: ModelRegistry constructor is private; use static factories.
  const authStorage = opts.ephemeral
    ? AuthStorage.inMemory()
    : AuthStorage.create(`${agentDir}/auth.json`);
  const modelRegistry = opts.ephemeral
    ? ModelRegistry.inMemory(authStorage)
    : ModelRegistry.create(authStorage, `${agentDir}/models.json`);

  // Register providers from config
  for (const [providerName, providerConfig] of Object.entries(config.models.providers)) {
    modelRegistry.registerProvider(providerName, {
      baseUrl: providerConfig.baseUrl,
      apiKey: providerConfig.apiKey,
      api: providerConfig.api as Api,
      models: providerConfig.models as Array<{ id: string; name: string; reasoning: boolean; input: ("text" | "image")[]; cost: { input: number; output: number; cacheRead: number; cacheWrite: number }; contextWindow: number; maxTokens: number }>,
    });
    if (providerConfig.apiKey) {
      authStorage.setRuntimeApiKey(providerName, providerConfig.apiKey);
    }
  }

  // Resolve model via resolveModel (handles provider/modelId, bare ID, prefix, fallback)
  const modelRef = opts.model ?? config.agent.defaultModel;
  const resolved = resolveModel(modelRef, config);
  let model = modelRegistry.find(resolved.provider, resolved.model.id);

  // Inject a neutral default User-Agent (overridable per provider) onto the
  // resolved Model. pi-ai's providers read Model.headers as defaultHeaders, so
  // this overrides the SDK UA that Cloudflare bot rules 403 on (see
  // applyModelHeaders / DEFAULT_USER_AGENT).
  if (model) {
    model = applyModelHeaders(model, resolved.providerConfig.headers);
  }

  // Ensure auth for the model's provider
  if (model && !authStorage.hasAuth(model.provider)) {
    const firstKey = Object.values(config.models.providers).find(
      (p) => p.apiKey,
    )?.apiKey;
    if (firstKey) {
      authStorage.setRuntimeApiKey(model.provider, firstKey);
    }
  }

  // --- MCP Manager ---
  const mcpManager = new MCPManager({ servers: getAllMCPServers(config), timeouts: config.mcpTimeouts });
  try {
    await mcpManager.connectAll();
  } catch (err: unknown) {
    const msg = err instanceof Error ? err.message : String(err);
    console.error(`MCP: ${msg}`);
    // Don't crash — degrade gracefully
  }

  // --- Memory ---
  const workspaceDir = opts.ephemeral
    ? getDefaultWorkspaceTemplate()
    : getWorkspaceDir();
  const memoryDir = opts.ephemeral
    ? join(tmpdir(), `qlaybot_memory_${Date.now()}`)
    : getMemoryDir();
  const embedder = createEmbedder(config);
  const memoryManager = new MemoryManager(memoryDir, config.memory.budget, embedder, config);

  // --- Interaction History (with config snapshot) ---
  // When verbose=true, disable truncation (spec §4 — verbose mode preserves
  // full-fidelity results in the transcript).
  const historyTruncationOpts = verbose
    ? { threshold: Infinity, headChars: 0, tailChars: 0 }
    : undefined;
  const history = new InteractionHistory(
    opts.sessionId,
    {
      model: modelRef,
      thinkingLevel: opts.thinkingLevel ?? config.agent.thinkingLevel,
      mcpServers: Object.keys(config.mcp),
      klayoutConnected: mcpManager.isConnected("klayout"),
    },
    historyTruncationOpts,
  );

  // --- API key resolver ---
  const configApiKeyResolver = async (
    _prov: string,
  ): Promise<string | undefined> => {
    const exact = config.models.providers[_prov];
    if (exact?.apiKey) return exact.apiKey;
    for (const pc of Object.values(config.models.providers)) {
      if (pc.apiKey) return pc.apiKey;
    }
    return undefined;
  };

  // --- Plan Manager + Background Task Manager (created early for tool wrapping) ---
  const planManager = new PlanManager(workspaceDir);
  planManager.setApprovalMode(opts.headless ? "headless" : "interactive");
  const backgroundTaskManager = new BackgroundTaskManager();

  // --- TranscriptMarkerEmitter (v0.4.4 §4.7) ---
  // Constructed up-front so it can be threaded into assembleTools opts
  // (consumed by the thinking tool factory — G2 Phase 1) AND registered
  // on the session via setTranscriptMarkerEmitter AFTER session
  // construction. Chicken-and-egg: the WeakMap registry can't be keyed
  // until the session object exists, but tools need the emitter at
  // assembly time. The single instance threaded through both paths
  // resolves the ordering without duplication.
  const transcriptMarkerEmitter = new TranscriptMarkerEmitter();
  const planStateMachine = new PlanStateMachine(transcriptMarkerEmitter);
  planManager.attachStateMachine(planStateMachine);

  // --- Assemble tools ---
  // Collect disabled tools from ALL servers
  const allServers = getAllMCPServers(config);
  const allDisabledTools = Object.values(allServers).flatMap(s => s.disabledTools ?? []);

  // Use extended assembleTools signature when subagent is enabled. After the
  // issue-#23 redesign the built-in general-purpose role is a reachable
  // target even with roles:{}, so we no longer gate on role count here (R3
  // finding #1) — the returned runner is needed for delegate registration.
  const hasSubagent = config.subagent?.enabled === true;
  let rawBaseTools: Record<string, any>;
  let rawCustomTools: any[];
  let subagentRunner: SubagentRunner | null = null;

  if (hasSubagent) {
    const { toolMap, runner } = assembleTools({
      config,
      cwd,
      mcpManager,
      memoryManager,
      workspaceDir,
      annotations: TOOL_ANNOTATIONS,
      disabledTools: allDisabledTools,
      getApiKey: configApiKeyResolver,
      defaultModel: config.agent.defaultModel,
      defaultThinkingLevel: config.agent.thinkingLevel,
      modelRegistry,
      planManager,
      isSubagent: false,
      transcriptMarkerEmitter,
    });
    subagentRunner = runner;
    // Extended signature returns tool map + runner — split into base + custom
    rawBaseTools = {};
    rawCustomTools = [];
    for (const [name, tool] of Object.entries(toolMap)) {
      if (['read', 'bash', 'edit', 'write'].includes(name)) {
        rawBaseTools[name] = tool;
      } else {
        rawCustomTools.push(tool);
      }
    }
  } else {
    // Migrate legacy call site to extended signature so planManager +
    // isSubagent are threaded through consistently (spec §9 step 5.3).
    const { toolMap } = assembleTools({
      config,
      cwd,
      mcpManager,
      memoryManager,
      workspaceDir,
      annotations: TOOL_ANNOTATIONS,
      disabledTools: allDisabledTools,
      getApiKey: configApiKeyResolver,
      defaultModel: config.agent.defaultModel,
      defaultThinkingLevel: config.agent.thinkingLevel,
      modelRegistry,
      planManager,
      isSubagent: false,
      transcriptMarkerEmitter,
    });
    rawBaseTools = {};
    rawCustomTools = [];
    for (const [name, tool] of Object.entries(toolMap)) {
      if (['read', 'bash', 'edit', 'write'].includes(name)) {
        rawBaseTools[name] = tool;
      } else {
        rawCustomTools.push(tool);
      }
    }
  }

  // Plan-mode wrapping is already applied inside assembleTools (per-tool via
  // wrapWriteForPlanMode/wrapEditForPlanMode/wrapBashForPlanMode/wrapMCPToolForPlanMode).
  // Do NOT re-wrap with the legacy allowlist sandbox — its ALLOWED_TOOLS set would
  // block exit_plan_mode, enter_plan_mode, write, edit, and most readonly MCP tools.
  const baseToolsOverride = rawBaseTools;
  const customTools = [
    ...rawCustomTools,
    createBackgroundStatusTool(backgroundTaskManager),
    createBackgroundResultTool(backgroundTaskManager),
  ];
  const activeToolNames = Object.keys(baseToolsOverride);
  const customToolNames = rawCustomTools.map((t: any) => t.name);

  // --- Build system prompt ---
  const connectedServers: string[] = [];
  if (mcpManager.isConnected("klayout")) {
    connectedServers.push(`klayout (KLayout MCP ${config.klayout.url})`);
  }

  // Compute skillsDirs — search paths for SKILL.md files
  const __dirname_agent = dirname(fileURLToPath(import.meta.url));
  const projectSkills = resolve(__dirname_agent, "..", "..", "skills"); // ../../skills from agent/src
  const skillsDirs = [projectSkills, join(workspaceDir, "skills"), join(cwd, "skills")];

  const systemPrompt = buildSystemPrompt({
    mode,
    workspaceDir,
    toolNames: [
      ...activeToolNames,
      ...mcpManager.allToolNames(),
      ...customToolNames,
    ],
    connectedServers,
    subagentConfig: config.subagent,
    skillsDirs,
  });

  // --- Resource Loader ---
  const resourceLoader = new DefaultResourceLoader({
    cwd,
    agentDir,
    settingsManager,
    systemPrompt,
    noExtensions: opts.ephemeral,
    noSkills: true,
    noPromptTemplates: opts.ephemeral,
    noThemes: opts.ephemeral,
  });
  await resourceLoader.reload();

  // --- Transform context: auto-recall ---
  const autoRecallConfig = {
    ...defaultAutoRecallConfig,
    ...config.memory.autoRecall,
  };
  const autoRecall = createAutoRecallTransform(memoryManager, autoRecallConfig);

  // Phase: Tool result pruning
  const pruner = createToolResultPruner(compactionConfig.toolResultPruning);

  // Phase: State loader (inject compaction state into context)
  const stateLoader = createStateLoaderTransform(workspaceDir);

  // Phase: Plan re-injection (issue #24). Periodically re-inject the plan
  // file contents after plan mode exit to keep the agent honest about
  // multi-turn plan execution.
  const planReinjector = createPlanReinjector(planManager, {
    interval: config.plan.reinjectionInterval,
  });

  const transformContext = async (
    messages: AgentMessage[],
    signal?: AbortSignal,
  ): Promise<AgentMessage[]> => {
    // Phase: drop stale <plan-reinjection> blocks first so the context
    // never carries more than one at a time.
    let transformed = await prunePlanReinjections(messages as any);
    // Phase: prune old tool results.
    transformed = pruner(transformed);
    // Phase: auto-recall memory (bug #8: must precede stateLoader so the
    // recall query anchors on the real user message, not injected state).
    transformed = await autoRecall(transformed, signal);
    // Phase: inject compaction state.
    transformed = stateLoader(transformed);
    // Phase: append the plan re-injection reminder if cadence matches.
    transformed = await planReinjector(transformed as any);
    return transformed;
  };

  // --- Core Agent ---
  const thinkingLevel = opts.thinkingLevel ?? config.agent.thinkingLevel;

  const agent = new Agent({
    initialState: {
      model: model ?? undefined,
      thinkingLevel,
    },
    convertToLlm,
    transformContext,
    getApiKey: configApiKeyResolver,
  });

  // --- AgentSession ---
  const session = new AgentSession({
    agent,
    baseToolsOverride,
    customTools,
    resourceLoader,
    sessionManager,
    settingsManager,
    modelRegistry,
    initialActiveToolNames: activeToolNames,
    cwd,
  });

  session.setAutoCompactionEnabled(false);

  // --- Register the TranscriptMarkerEmitter on the session (§4.7) ---
  // Post-session-construction: keyed by the session object reference so
  // consumers (RPC, history sink, verbose writer, TUI callback) can
  // reach the SAME emitter instance via getTranscriptMarkerEmitter.
  setTranscriptMarkerEmitter(session, transcriptMarkerEmitter);
  planManager.bindSession(session);

  // --- Subscribe InteractionHistory to the emitter (§4.7 Persistence 1) ---
  // Direct subscription — the history sink wraps each marker in the
  // canonical {timestamp, type, data} envelope. `timestamp` copies
  // marker.ts verbatim (canonical-ts rule). This subscription is
  // INDEPENDENT of the verbose writer's subscription (Task 0.5); neither
  // mirrors the other.
  const historyMarkerUnsub = ((): (() => void) => {
    const listener = (marker: TranscriptMarker): void => {
      history.appendTranscript({
        timestamp: marker.ts,
        type: "transcript_marker",
        data: marker,
      });
    };
    transcriptMarkerEmitter.on("marker", listener);
    return () => transcriptMarkerEmitter.off("marker", listener);
  })();

  // --- Verbose transcript writer (spec §4.3, runtime-ephemeral) ---
  // When verbose=true we mirror every NON-MARKER history entry
  // (user_prompt, agent_response, tool_call, …) into a JSONL file next
  // to where the user launched qlaybot (process.cwd()), not the
  // internal qlaybot workspace (~/.qlaybot/workspace).
  //
  // v0.4.4 §4.7 changes: transcript_marker entries do NOT flow through
  // the mirror path. Instead, the VerboseTranscriptWriter subscribes
  // DIRECTLY to the TranscriptMarkerEmitter — parallel to (and
  // independent of) the InteractionHistory subscription above. This
  // guarantees both sinks produce byte-equal envelope lines (T43(b)
  // precursor) and that clearing the mirror via history.setMirror(null)
  // does not stop markers from reaching the verbose JSONL.
  const verboseTranscriptDir = process.cwd();
  const verboseWriter: VerboseTranscriptWriter | null =
    verbose
      ? new VerboseTranscriptWriter(
          verboseTranscriptDir,
          history.getSessionId(),
        )
      : null;
  let verboseMarkerUnsub: (() => void) | null = null;
  if (verboseWriter) {
    // Legacy non-marker mirror: every OTHER history entry is mirrored
    // to the verbose JSONL. transcript_marker entries are routed via
    // the direct emitter subscription below to preserve v0.4.4's
    // "independent subscribers" contract.
    history.setMirror((entry) => {
      if (entry.type === "transcript_marker") return;
      verboseWriter.write(entry);
    });
    // Direct subscription — the verbose writer wraps each marker in
    // the same {timestamp, type, data} envelope that the history sink
    // uses. Same envelope across both sinks → T43(b) byte-equality.
    const verboseListener = (marker: TranscriptMarker): void => {
      verboseWriter.write({
        timestamp: marker.ts,
        type: "transcript_marker",
        data: marker,
      });
    };
    transcriptMarkerEmitter.on("marker", verboseListener);
    verboseMarkerUnsub = () =>
      transcriptMarkerEmitter.off("marker", verboseListener);
  }

  // --- Auto-save: subscribe to session events for interaction history ---
  const toolStartTimes = new Map<string, number>();
  const toolStartArgs = new Map<string, unknown>();
  let pendingTextChunks: string[] = [];
  let pendingThinkingChunks: string[] = [];
  const historyUnsub = session.subscribe((event) => {
    switch (event.type) {
      case "message_update": {
        const ame = event.assistantMessageEvent;
        if (ame.type === "text_delta") {
          pendingTextChunks.push(ame.delta);
        } else if (ame.type === "thinking_delta") {
          pendingThinkingChunks.push(ame.delta);
        }
        break;
      }
      case "tool_execution_start":
        // Flush thinking/text accumulated before this tool call
        // (the LLM produces thinking + text, then emits tool_use)
        {
          const thinkingText = pendingThinkingChunks.join("");
          if (thinkingText) history.recordThinking(thinkingText);
          pendingThinkingChunks = [];

          const responseText = pendingTextChunks.join("");
          if (responseText) history.recordResponse(responseText);
          pendingTextChunks = [];
        }
        toolStartTimes.set(event.toolCallId, Date.now());
        toolStartArgs.set(event.toolCallId, event.args);
        break;
      case "tool_execution_end": {
        const startTime = toolStartTimes.get(event.toolCallId) ?? Date.now();
        const durationMs = Date.now() - startTime;
        const args = toolStartArgs.get(event.toolCallId);
        history.recordToolCall(event.toolName, args, event.result, durationMs);
        toolStartTimes.delete(event.toolCallId);
        toolStartArgs.delete(event.toolCallId);
        break;
      }
      case "agent_end": {
        // Flush any remaining thinking/text from the final turn
        const thinkingText = pendingThinkingChunks.join("");
        if (thinkingText) history.recordThinking(thinkingText);
        pendingThinkingChunks = [];

        const responseText = pendingTextChunks.join("");
        if (responseText) history.recordResponse(responseText);
        pendingTextChunks = [];
        break;
      }
    }
  });

  // --- Context usage helper ---
  function getContextUsage(): { tokens: number; contextWindow: number; percent: number } | undefined {
    const usage = session.getContextUsage();
    if (!usage) return undefined;
    return {
      tokens: usage.tokens ?? 0,
      contextWindow: usage.contextWindow,
      percent: usage.percent ?? 0,
    };
  }

  // --- Dispose ---
  async function dispose(): Promise<void> {
    historyUnsub();
    historyMarkerUnsub();
    verboseMarkerUnsub?.();
    if (verboseWriter) {
      try {
        await verboseWriter.close();
      } catch {
        /* best-effort */
      }
    }
    session.dispose();
    memoryManager.close();
    await mcpManager.disconnectAll();
  }

  // --- Compaction convenience method ---
  async function compact(userInstructions?: string): Promise<void> {
    const instructions = buildCompactInstructions(workspaceDir, compactionConfig, userInstructions);
    const result = await session.compact(instructions);
    if (result?.summary) {
      extractStateFiles(result.summary, workspaceDir);
    }
  }

  // --- prompt_too_long error recovery ---
  // Auto-compaction is disabled (custom compaction strategy), so we need
  // explicit recovery: catch prompt_too_long, compact, and retry once.
  const rawPrompt = session.prompt.bind(session);
  (session as any).prompt = async function promptWithRecovery(
    text: string,
    options?: any,
  ): Promise<void> {
    try {
      return await rawPrompt(text, options);
    } catch (err: any) {
      const msg = String(err?.message ?? err);
      const isPromptTooLong =
        err?.code === "prompt_too_long" ||
        err?.type === "prompt_too_long" ||
        msg.includes("prompt_too_long");
      if (!isPromptTooLong) throw err;

      // Auto-compact and retry once
      await compact();
      return rawPrompt(text, options);
    }
  };

  // --- Command Registry ---
  const commandRegistry = createCommandRegistry();

  const botSession: QlayBotSession = {
    session,
    sessionManager,
    config,
    mcpManager,
    memoryManager,
    subagentRunner,
    history,
    commandRegistry,
    planManager,
    backgroundTaskManager,
    compactionConfig,
    assembledSystemPrompt: systemPrompt,
    compact,
    getContextUsage,
    dispose,
  };

  return botSession;
}

function getDefaultWorkspaceTemplate(): string {
  const __dirname_resolved = dirname(fileURLToPath(import.meta.url));
  return resolve(__dirname_resolved, "..", "workspace");
}

export { AgentSession, SessionManager };
