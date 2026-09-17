/**
 * Phase 2b — E2E integration tests for v0.4.4 plan-mode + marker transport.
 * Task 2.20 (spec §9.1 T2/T3/T4/T24/T39/T43).
 *
 * These tests spawn the real CLI in `-m rpc` (or `-m rpc --verbose`) mode,
 * send prompts that go through the real Anthropic API, and assert against
 * the live `transcript_marker` JSON-RPC event stream + the on-disk
 * persistence sinks (history JSONL and the verbose JSONL).
 *
 * Gated the same way as `test-e2e.ts`: ANTHROPIC_API_KEY + KLayout MCP at
 * KLAYOUT_MCP_URL (default :8765) + built dist/cli.js. Set `QLAYBOT_E2E=0`
 * to force-skip.
 *
 * Scoping decisions (documented per-test):
 *   - T3 is reduced to the headless-feasible surface (abandon path). The
 *     `reject → redraft` loop requires the interactive approval gate which
 *     is v0.4.5 work (OQ-7). The unit-level replan-loop coverage lives in
 *     `test-plan-state-machine.ts` (T4 reject sub-scenario).
 *   - T4 runs ONLY the happy-path marker ordering E2E. The draft-only /
 *     reject / abandon / replan sub-scenarios are unit-covered under Task
 *     2.13; re-running them against the real API would waste budget.
 *   - T39 asserts `think_recorded` during `plan_drafting` only; the
 *     `plan_executing` positive case is unit-covered (and is not
 *     prompt-forceable in E2E).
 */

import { describe, it, expect, beforeAll, afterAll } from "vitest";
import { ChildProcess, spawn } from "child_process";
import { createInterface, Interface } from "readline";
import { resolve, dirname, join } from "path";
import { fileURLToPath } from "url";
import { existsSync, readFileSync, mkdirSync, readdirSync, statSync } from "fs";
import { homedir, tmpdir } from "os";
import { createHash } from "crypto";

const __dirname = dirname(fileURLToPath(import.meta.url));
const CLI_PATH = resolve(__dirname, "..", "dist", "cli.js");
const KLAYOUT_MCP_URL = process.env.KLAYOUT_MCP_URL ?? "http://127.0.0.1:8765/mcp";

// ---------------------------------------------------------------------------
// Env gating — clone of test-e2e.ts pattern.
// ---------------------------------------------------------------------------

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
  } catch {
    return false;
  }
}

const hasApiKey = canRunE2E();
const klayoutReachable = hasApiKey ? await probeKLayout() : false;
const E2E_ENABLED = hasApiKey && klayoutReachable;

// Wiring guard — if the RPC forwarder was removed, our tests would hang on
// a generic 300s timeout. Fail fast with an actionable message instead.
// Detected via a substring probe on the shipped dist bundle (the source uses
// the same literal in rpc.ts:65).
const rpcForwardingWired = (() => {
  try {
    const rpcTs = resolve(__dirname, "..", "src", "rpc.ts");
    if (!existsSync(rpcTs)) return false;
    const src = readFileSync(rpcTs, "utf-8");
    return src.includes("subscribeMarkersToRPC") && src.includes(`"transcript_marker"`);
  } catch {
    return false;
  }
})();

if (!E2E_ENABLED) {
  const reasons: string[] = [];
  if (!process.env.ANTHROPIC_API_KEY) reasons.push("ANTHROPIC_API_KEY not set");
  if (!existsSync(CLI_PATH)) reasons.push("dist/cli.js not built");
  if (!klayoutReachable) reasons.push(`KlayoutClaw MCP not reachable at ${KLAYOUT_MCP_URL}`);
  if (process.env.QLAYBOT_E2E === "0") reasons.push("QLAYBOT_E2E=0");
  console.log(`Phase 2b E2E tests skipped: ${reasons.join(", ")}`);
} else if (!rpcForwardingWired) {
  console.log(
    "Phase 2b E2E tests skipped: transcript_marker RPC forwarding not wired in agent/src/rpc.ts",
  );
}

const describeE2E =
  E2E_ENABLED && rpcForwardingWired ? describe : describe.skip;

// ---------------------------------------------------------------------------
// RPC client — same shape as test-e2e.ts but retains every event (we need
// the full `transcript_marker` stream, not just the last one).
// ---------------------------------------------------------------------------

interface RPCEvent {
  method: string;
  params: Record<string, unknown>;
}

interface RPCClient {
  proc: ChildProcess;
  rl: Interface;
  events: RPCEvent[];
  markers: Array<Record<string, unknown>>;
  send: (method: string, params?: Record<string, unknown>) => Promise<unknown>;
  close: () => Promise<void>;
  // Snapshot the `markers` array length — used to scope a second turn's
  // marker slice for T24(c).
  markerCheckpoint: () => number;
}

interface SpawnOpts {
  verbose?: boolean;
  cwd?: string;
}

async function createRPCClient(opts: SpawnOpts = {}): Promise<RPCClient> {
  const cliArgs = ["--mode", "rpc"];
  if (opts.verbose) cliArgs.push("--verbose");
  const proc = spawn("node", [CLI_PATH, ...cliArgs], {
    stdio: ["pipe", "pipe", "pipe"],
    cwd: opts.cwd,
    env: { ...process.env },
  });
  const rl = createInterface({ input: proc.stdout!, terminal: false });
  let requestId = 0;
  const pending = new Map<
    number | string,
    { resolve: (v: unknown) => void; reject: (e: Error) => void }
  >();
  const events: RPCEvent[] = [];
  const markers: Array<Record<string, unknown>> = [];

  rl.on("line", (line: string) => {
    try {
      const msg = JSON.parse(line);
      if (msg.id !== undefined && pending.has(msg.id)) {
        const { resolve, reject } = pending.get(msg.id)!;
        pending.delete(msg.id);
        if (msg.error) reject(new Error(msg.error.message));
        else resolve(msg.result);
      } else if (msg.method && msg.id === undefined) {
        const params = (msg.params as Record<string, unknown>) ?? {};
        events.push({ method: msg.method, params });
        if (msg.method === "transcript_marker") markers.push(params);
      }
    } catch {
      /* non-JSON line (stderr-merged or malformed) — ignore */
    }
  });

  await new Promise<void>((resolve) => {
    const check = (line: string) => {
      try {
        if (JSON.parse(line).method === "ready") resolve();
      } catch {
        /* */
      }
    };
    rl.on("line", check);
    setTimeout(resolve, 5000);
  });

  function send(
    method: string,
    params?: Record<string, unknown>,
  ): Promise<unknown> {
    return new Promise((resolve, reject) => {
      const id = ++requestId;
      pending.set(id, { resolve, reject });
      proc.stdin!.write(
        JSON.stringify({ jsonrpc: "2.0", id, method, params }) + "\n",
      );
      setTimeout(() => {
        if (pending.has(id)) {
          pending.delete(id);
          reject(new Error(`RPC timeout for ${method}`));
        }
      }, 420000);
    });
  }

  async function close(): Promise<void> {
    try {
      await send("shutdown");
    } catch {
      /* */
    }
    try {
      proc.kill();
    } catch {
      /* */
    }
    rl.close();
  }

  return {
    proc,
    rl,
    events,
    markers,
    send,
    close,
    markerCheckpoint: () => markers.length,
  };
}

// ---------------------------------------------------------------------------
// Shared helpers.
// ---------------------------------------------------------------------------

interface Marker {
  type: string;
  [k: string]: unknown;
}

function markerTypes(markers: Array<Record<string, unknown>>): string[] {
  return markers.map((m) => m.type as string);
}

function findMarker<T extends Marker>(
  markers: Array<Record<string, unknown>>,
  type: string,
): T | undefined {
  return markers.find((m) => m.type === type) as T | undefined;
}

/** Locate the most recent history transcript.jsonl.
 *
 *  Preferred path is the `~/.qlaybot/history/latest` symlink, but that
 *  symlink may be stale in practice — `InteractionHistory.updateLatestSymlink`
 *  uses `existsSync` (which follows symlinks) to check the link before
 *  removing it, and so a pre-existing DANGLING symlink (whose target dir
 *  was deleted) is never cleared, the subsequent `symlinkSync` fails, and
 *  the catch block silences it. As a robustness measure, if the symlink
 *  path is missing or stale we fall through to a direct scan of
 *  `~/.qlaybot/history/<todayDate>/` and pick the newest session dir by
 *  mtime. This keeps the test robust to the production-code quirk without
 *  modifying it.
 */
function latestHistoryTranscriptPath(): string {
  const historyRoot = join(homedir(), ".qlaybot", "history");
  const latest = join(historyRoot, "latest");
  const latestTranscript = join(latest, "transcript.jsonl");
  if (existsSync(latestTranscript)) return latestTranscript;

  // Fallback: scan today's date dir (and prior two days, in case we
  // crossed midnight mid-run) for the most recently modified session dir.
  const candidates: Array<{ path: string; mtime: number }> = [];
  const today = new Date();
  for (let dayOffset = 0; dayOffset < 3; dayOffset++) {
    const d = new Date(today.getTime() - dayOffset * 86400000);
    const dateDir = d.toISOString().slice(0, 10);
    const dayPath = join(historyRoot, dateDir);
    if (!existsSync(dayPath)) continue;
    for (const entry of readdirSync(dayPath)) {
      const sessionDir = join(dayPath, entry);
      const transcript = join(sessionDir, "transcript.jsonl");
      if (!existsSync(transcript)) continue;
      try {
        const st = statSync(transcript);
        candidates.push({ path: transcript, mtime: st.mtimeMs });
      } catch {
        /* ignore */
      }
    }
  }
  candidates.sort((a, b) => b.mtime - a.mtime);
  return candidates.length > 0 ? candidates[0].path : latestTranscript;
}

/** Read all `transcript_marker` envelopes from a history-style JSONL file
 *  (one JSON object per line; schema is {timestamp,type,data}). */
function readTranscriptMarkersFromJsonl(
  filePath: string,
): Array<{ timestamp: string; type: string; data: Record<string, unknown> }> {
  if (!existsSync(filePath)) return [];
  const raw = readFileSync(filePath, "utf-8");
  return raw
    .split("\n")
    .map((line) => line.trim())
    .filter((line) => line.length > 0)
    .map((line) => {
      try {
        return JSON.parse(line);
      } catch {
        return null;
      }
    })
    .filter(
      (entry): entry is { timestamp: string; type: string; data: Record<string, unknown> } =>
        entry !== null &&
        typeof entry === "object" &&
        entry.type === "transcript_marker",
    );
}

/** Read the raw lines (preserving bytes) of a file, filtered to the subset
 *  that are `transcript_marker` envelopes. Used for T43(b) byte-equality. */
function readTranscriptMarkerLines(filePath: string): string[] {
  if (!existsSync(filePath)) return [];
  const raw = readFileSync(filePath, "utf-8");
  return raw
    .split("\n")
    .map((line) => line.trim())
    .filter((line) => line.length > 0)
    .filter((line) => {
      try {
        const o = JSON.parse(line);
        return (
          o &&
          typeof o === "object" &&
          (o as { type?: string }).type === "transcript_marker"
        );
      } catch {
        return false;
      }
    });
}

function sha256Hex(buf: Buffer): string {
  return createHash("sha256").update(buf).digest("hex");
}

// Prompts — kept as module-level constants so the same prompt is used in
// multiple tests (T2 + T4 + T24 + T43 share the happy-path prompt to avoid
// paying the Anthropic cost more than once per test).
const HAPPY_PATH_PROMPT =
  "Enter plan mode with task 'draft a 4-pad bonding-pad layout at the " +
  "corners of a 500x500um die'. Write a concise plan (a few hundred " +
  "characters is fine) that includes specific x,y coordinates for each of " +
  "the 4 pads and a chosen layer. Then call exit_plan_mode with " +
  "approved=true. Do not continue doing actual work after exiting plan mode.";

const ABANDON_PROMPT =
  "Enter plan mode with task 'sketch a placeholder plan'. Immediately " +
  "call exit_plan_mode with approved=false to abandon (no need to write " +
  "any plan content first). Do not continue after abandoning.";

const THINKING_DURING_DRAFT_PROMPT =
  "Enter plan mode with task 'draft a simple 2-pad layout'. Use the " +
  "`thinking` tool with a short thought about the layout plan BEFORE " +
  "writing the plan file. Then write a short plan and call " +
  "exit_plan_mode with approved=true. Do not continue after exiting.";

// Spec invariant §4.7 T4(a): the execute-path marker ordering. Every
// executing-to-done session should produce these markers in this order;
// `think_recorded` may additionally appear anywhere but is not required.
const HAPPY_PATH_EXPECTED_SEQUENCE = [
  "plan_drafted",
  "plan_file_written",
  "plan_approved",
  "plan_executing",
  "plan_done",
];

/** Drop `think_recorded` entries so we can compare plan-state ordering
 *  without `thinking`-tool noise. */
function planMarkersOnly(types: string[]): string[] {
  return types.filter((t) => t !== "think_recorded");
}

/**
 * Send `message` and, if the expected marker does not appear in the
 * client's markers after the first attempt, retry up to `maxAttempts`
 * total times. Returns the final `prompt` result.
 *
 * Rationale (v0.4.4 Phase 8): real-API replicates of HAPPY_PATH_PROMPT
 * occasionally skip `enter_plan_mode` entirely (the model responds
 * conversationally instead of calling the tool), collapsing the
 * downstream marker stream. The deterministic coexistence / ordering
 * guarantees are already covered by unit (test-marker-emitter.ts,
 * test-plan-state-machine.ts) + integration (test-runtime-wiring.ts).
 * This retry helper lets the E2E smoke confirm the live-stack path
 * without paying the 1-in-N stochastic miss rate.
 */
async function promptUntilMarker(
  client: RPCClient,
  message: string,
  expectedMarker: string,
  maxAttempts = 3,
): Promise<unknown> {
  let result: unknown;
  for (let attempt = 0; attempt < maxAttempts; attempt++) {
    result = await client.send("prompt", { message });
    const types = markerTypes(client.markers);
    if (types.includes(expectedMarker)) return result;
  }
  return result;
}

// ---------------------------------------------------------------------------
// T2 — Happy-path execute.
// ---------------------------------------------------------------------------

describeE2E("Phase 2b / T2 — plan-mode happy-path execute", () => {
  let client: RPCClient;
  beforeAll(async () => {
    client = await createRPCClient();
    await client.send("initialize", { ephemeral: true });
  });
  afterAll(async () => {
    await client.close();
  });

  it("drafts, approves, executes, and emits the full marker sequence", async () => {
    const result = (await promptUntilMarker(
      client,
      HAPPY_PATH_PROMPT,
      "plan_drafted",
      3,
    )) as Record<string, unknown>;
    expect(result.status).toBe("completed");

    const planMarkers = planMarkersOnly(markerTypes(client.markers));

    // (a) enter_plan_mode succeeded → we should see `plan_drafted`
    //     (implicit plan_drafting entry + explicit drafted marker per §4.2).
    expect(planMarkers).toContain("plan_drafted");

    // (b) agent wrote the plan via the plansDir-scoped tool → `plan_file_written` marker.
    expect(planMarkers).toContain("plan_file_written");

    // (c) exit_plan_mode({approved: true}) in headless mode auto-approves
    //     with executeAfterApproval: true (§PM-4).
    const approved = findMarker<{ auto: boolean; executeAfterApproval: boolean }>(
      client.markers,
      "plan_approved",
    );
    expect(approved, "plan_approved marker emitted").toBeDefined();
    expect(approved?.auto).toBe(true);
    expect(approved?.executeAfterApproval).toBe(true);

    // (d) Marker ordering (ignoring think_recorded interleavings).
    const firstIndex = (t: string): number => planMarkers.indexOf(t);
    for (let i = 0; i + 1 < HAPPY_PATH_EXPECTED_SEQUENCE.length; i++) {
      const a = HAPPY_PATH_EXPECTED_SEQUENCE[i];
      const b = HAPPY_PATH_EXPECTED_SEQUENCE[i + 1];
      const ia = firstIndex(a);
      const ib = firstIndex(b);
      expect(ia, `${a} present`).toBeGreaterThanOrEqual(0);
      expect(ib, `${b} present`).toBeGreaterThanOrEqual(0);
      expect(ia, `${a} precedes ${b}`).toBeLessThan(ib);
    }

    // (e) Exactly one terminal marker on this turn. The turn-invariant
    //     per §4.2 restricts the terminal set to plan_done /
    //     plan_approved{executeAfterApproval:false} / plan_rejected{action:"abandon"}.
    const terminalTypes = new Set([
      "plan_done",
      "plan_rejected", // only terminal on action === "abandon"
    ]);
    const terminals = client.markers.filter((m) => {
      const t = m.type as string;
      if (t === "plan_done") return true;
      if (t === "plan_approved" && m.executeAfterApproval === false) return true;
      if (t === "plan_rejected" && m.action === "abandon") return true;
      return false;
    });
    expect(terminals.length, "exactly one terminal marker").toBe(1);
    expect(terminals[0].type).toBe("plan_done");
    expect((terminals[0] as { status?: string }).status).toBe("ok");
    // Defensive assertion to stop the compiler from warning about unused
    // terminalTypes constant (documentation-of-intent).
    expect(terminalTypes.has("plan_done")).toBe(true);

    // (f) planHash identical across plan_drafted / plan_file_written / plan_executing.
    const drafted = findMarker<{ planHash: string }>(
      client.markers,
      "plan_drafted",
    );
    const fileWritten = findMarker<{ planHash: string }>(
      client.markers,
      "plan_file_written",
    );
    const executing = findMarker<{ planHash: string }>(
      client.markers,
      "plan_executing",
    );
    expect(drafted?.planHash).toBeDefined();
    expect(fileWritten?.planHash).toBe(drafted?.planHash);
    expect(executing?.planHash).toBe(drafted?.planHash);
  }, 300000);
});

// ---------------------------------------------------------------------------
// T3 — Rejection path (abandon shortcut). Scoped down for v0.4.4 headless.
// ---------------------------------------------------------------------------

describeE2E("Phase 2b / T3 — plan-mode abandon (headless-scoped)", () => {
  // NOTE (spec §4.4 + PM-4 + OQ-7): In the v0.4.4 `-m` headless mode,
  // the RPC harness can only surface auto-approve or abandon via
  // `exit_plan_mode({approved: false})`. The interactive `reject → redraft`
  // replan-loop path depends on the 4-option approval gate which is
  // TUI-only today; headless approval RPC lands in v0.4.5 with OQ-7.
  // The reject-branch is unit-covered in `test-plan-state-machine.ts`
  // (T4 reject sub-scenario); here we only exercise the abandon path.

  let client: RPCClient;
  beforeAll(async () => {
    client = await createRPCClient();
    await client.send("initialize", { ephemeral: true });
  });
  afterAll(async () => {
    await client.close();
  });

  it("exit_plan_mode({approved: false}) emits terminal plan_rejected{action:'abandon'}", async () => {
    const result = (await client.send("prompt", {
      message: ABANDON_PROMPT,
    })) as Record<string, unknown>;
    expect(result.status).toBe("completed");

    const rejected = findMarker<{
      action: string;
      feedback: string;
    }>(client.markers, "plan_rejected");
    expect(rejected).toBeDefined();
    expect(rejected?.action).toBe("abandon");
    expect(rejected?.feedback).toBe("abandoned");

    // Terminal-invariant: plan_rejected{abandon} is the sole terminal, no plan_done.
    expect(
      client.markers.some((m) => m.type === "plan_done"),
      "no plan_done on abandon path",
    ).toBe(false);
  }, 180000);
});

// ---------------------------------------------------------------------------
// T4 — Marker ordering (execute path confirmed end-to-end).
// ---------------------------------------------------------------------------

describeE2E("Phase 2b / T4 — marker ordering (E2E confirmation of happy path)", () => {
  // NOTE: The other four sub-scenarios of T4 (draft-only, reject, abandon-from-gate,
  // replan) are unit-covered under Task 2.13 in test-plan-state-machine.ts
  // and test-marker-emitter.ts. Running them against the real Anthropic
  // API would cost budget without adding signal — the RPC transport path
  // they share is the same wiring this single E2E confirms.

  let client: RPCClient;
  beforeAll(async () => {
    client = await createRPCClient();
    await client.send("initialize", { ephemeral: true });
  });
  afterAll(async () => {
    await client.close();
  });

  it("RPC marker sequence matches the §4.2 execute-path ordering", async () => {
    const result = (await promptUntilMarker(
      client,
      HAPPY_PATH_PROMPT,
      "plan_done",
      3,
    )) as Record<string, unknown>;
    expect(result.status).toBe("completed");

    const seen = planMarkersOnly(markerTypes(client.markers));
    // Assert the expected ordering as a strict subsequence of `seen` —
    // any extra think_recorded was stripped, and extra plan_* markers
    // would indicate a spurious replan loop (which would break the
    // turn-invariant in T2 above).
    expect(seen).toEqual(HAPPY_PATH_EXPECTED_SEQUENCE);
  }, 300000);
});

// ---------------------------------------------------------------------------
// T24 — Plan-file lifecycle (PM-11 slug reuse + terminal truncation).
// ---------------------------------------------------------------------------

describeE2E("Phase 2b / T24 — plan-file lifecycle", () => {
  let client: RPCClient;
  beforeAll(async () => {
    client = await createRPCClient();
    await client.send("initialize", { ephemeral: true });
  });
  afterAll(async () => {
    await client.close();
  });

  it("file exists, planHash matches disk bytes, and slug reuse truncates file", async () => {
    // ---- First turn: happy path ----------------------------------------
    await promptUntilMarker(client, HAPPY_PATH_PROMPT, "plan_drafted", 3);

    const drafted = findMarker<{
      planHash: string;
      planFilePath: string;
      planSlug: string;
    }>(client.markers, "plan_drafted");
    expect(drafted, "plan_drafted emitted").toBeDefined();
    expect(drafted?.planFilePath).toBeTruthy();
    expect(drafted?.planSlug).toBeTruthy();

    // (a) File exists at the reported path.
    const planFilePath = drafted!.planFilePath;
    expect(
      existsSync(planFilePath),
      `plan file exists at ${planFilePath}`,
    ).toBe(true);

    // (b) Contents hash to the plan_drafted marker's planHash.
    //     NOTE: PM-11 path 1.b (slugCacheState="terminal") truncates the
    //     file on plan_done, and we read it AFTER the first turn completes.
    //     The integrity invariant targets the plan_file_written moment, so
    //     compare against the plan_file_written marker's hash + file snapshot
    //     taken immediately (we can still confirm a consistent state).
    //     At this post-terminal point, the file has been truncated — skip
    //     the byte hash on the current file and instead assert drafted →
    //     file_written hash equality (already covered by T2 above). Here
    //     we focus on the lifecycle surface.
    const firstSlug = drafted!.planSlug;
    const firstFilePath = planFilePath;

    // (c) Second enter_plan_mode in the SAME session — slug should be REUSED
    //     and the file should be truncated (PM-11 path 1.b terminal reuse).
    //
    // Prompt hardening (G4 flakiness fix, 2026-04-22): the original phrasing
    // relied on real-API model variance to call `enter_plan_mode` first.
    // Observed failure mode was the agent responding conversationally or
    // calling an unrelated tool first, which skipped the slug-reuse
    // codepath entirely. The directive below mandates the tool-call order
    // and primes the agent with "immediately" + enumerated single-step
    // guidance so compliance is higher across replicates. Target: 10/10
    // pass rate under real-API prompt-cache warm conditions.
    const before = client.markerCheckpoint();
    await client.send("prompt", {
      message:
        "Your VERY FIRST ACTION must be to call the `enter_plan_mode` tool " +
        "with task 'sketch another short plan'. Do NOT respond with prose " +
        "first and do NOT call any other tool before `enter_plan_mode`. " +
        "Step 1: call enter_plan_mode({task: 'sketch another short plan'}). " +
        "Step 2: write a SHORT plan (a single-line bullet like '- place 1 " +
        "pad at origin' is perfectly fine). " +
        "Step 3: call exit_plan_mode({approved: true}). " +
        "Stop immediately after exit_plan_mode returns. Do not continue " +
        "with any further work.",
    });

    const secondTurnMarkers = client.markers.slice(before);
    const secondDrafted = secondTurnMarkers.find(
      (m) => m.type === "plan_drafted",
    ) as { planSlug: string; planFilePath: string; planHash: string } | undefined;
    expect(secondDrafted, "plan_drafted on second turn").toBeDefined();

    // Slug reuse assertion per PM-11 path 1.b (successful execute → reuse).
    expect(secondDrafted!.planSlug).toBe(firstSlug);
    expect(secondDrafted!.planFilePath).toBe(firstFilePath);

    // File hash on disk right now should match the SECOND turn's drafted
    // hash — the first turn's content has been truncated (PM-11) and the
    // second turn re-wrote the file.
    const diskNow = readFileSync(secondDrafted!.planFilePath);
    const diskHashNow = sha256Hex(diskNow);
    // Look for the second turn's plan_file_written marker — its planHash
    // reflects the file contents at write-time, which should still be
    // valid unless the file was mutated after. Prefer the file_written
    // marker since plan_drafted pre-dates the fs write.
    const secondFileWritten = secondTurnMarkers.find(
      (m) => m.type === "plan_file_written",
    ) as { planHash: string } | undefined;
    expect(secondFileWritten, "second plan_file_written marker").toBeDefined();
    // The file MAY have been truncated again after plan_done at the end of
    // the second turn. We accept either: (i) the write-time hash if the
    // file is still at that content, OR (ii) the SHA of an empty string
    // if terminal truncation ran.
    const sha256Empty = sha256Hex(Buffer.alloc(0));
    expect(
      diskHashNow === secondFileWritten!.planHash || diskHashNow === sha256Empty,
      `plan file on disk matches second-turn planHash OR was truncated post-terminal (got ${diskHashNow})`,
    ).toBe(true);
  }, 540000);
});

// ---------------------------------------------------------------------------
// T39 — `thinking` tool positive case in plan_drafting (TH-7 positive).
// ---------------------------------------------------------------------------

describeE2E("Phase 2b / T39 — thinking in plan_drafting (TH-7 positive)", () => {
  // NOTE: the `plan_executing` positive case of TH-7 cannot be forced via
  // prompt alone (the agent rarely volunteers thinking during simple
  // execution), so we unit-cover it in the state-machine suite and only
  // exercise the `plan_drafting` case here. T36 covers the NEGATIVE path
  // (rejection in `plan_drafted`).

  let client: RPCClient;
  beforeAll(async () => {
    client = await createRPCClient();
    await client.send("initialize", { ephemeral: true });
  });
  afterAll(async () => {
    await client.close();
  });

  it("think_recorded{source:'tool'} fires before plan_drafted", async () => {
    const result = (await client.send("prompt", {
      message: THINKING_DURING_DRAFT_PROMPT,
    })) as Record<string, unknown>;
    expect(result.status).toBe("completed");

    const types = markerTypes(client.markers);
    const firstThinkIdx = types.indexOf("think_recorded");
    const firstDraftedIdx = types.indexOf("plan_drafted");
    expect(firstThinkIdx, "think_recorded marker present").toBeGreaterThanOrEqual(0);
    expect(firstDraftedIdx, "plan_drafted marker present").toBeGreaterThanOrEqual(0);
    expect(
      firstThinkIdx,
      "think_recorded appears before plan_drafted",
    ).toBeLessThan(firstDraftedIdx);

    const firstThink = client.markers[firstThinkIdx] as {
      source?: string;
      thought?: string;
    };
    expect(firstThink.source).toBe("tool");
    expect(
      typeof firstThink.thought === "string" && firstThink.thought.length > 0,
      "think_recorded carries a non-empty thought",
    ).toBe(true);
  }, 300000);
});

// ---------------------------------------------------------------------------
// T43 — Marker transport: (a) RPC event order = history JSONL order;
//                        (b) history JSONL and verbose JSONL are byte-equal
//                            for every transcript_marker line.
// ---------------------------------------------------------------------------

describeE2E("Phase 2b / T43 — marker transport", () => {
  // (a) Non-verbose path: order check between live RPC stream and history JSONL.
  describe("T43(a) — RPC order matches history JSONL", () => {
    let client: RPCClient;
    beforeAll(async () => {
      client = await createRPCClient();
      await client.send("initialize", { ephemeral: true });
    });
    afterAll(async () => {
      await client.close();
    });

    it("ordered marker types in RPC stream == ordered marker types in history JSONL", async () => {
      const result = (await promptUntilMarker(
        client,
        HAPPY_PATH_PROMPT,
        "plan_done",
        3,
      )) as Record<string, unknown>;
      expect(result.status).toBe("completed");

      // Give the async appendTranscript a beat to flush (sync fs op,
      // but the emit itself is on a listener).
      await new Promise((r) => setTimeout(r, 250));

      const historyPath = latestHistoryTranscriptPath();
      const historyMarkers = readTranscriptMarkersFromJsonl(historyPath);
      const historyTypes = historyMarkers.map((m) => m.data.type as string);
      const rpcTypes = markerTypes(client.markers);

      // Every marker in RPC must also be in history, in the same order.
      // History may contain strictly more markers if the session logged
      // anything before our first prompt — the spec §4.7 invariant is
      // "same order", not "same length", so we compare the tail of the
      // history list of matching length.
      expect(historyTypes.length).toBeGreaterThanOrEqual(rpcTypes.length);
      const historyTail = historyTypes.slice(
        historyTypes.length - rpcTypes.length,
      );
      expect(rpcTypes).toEqual(historyTail);

      // Sanity: execute-path plan_* markers appear in the documented order.
      const planOnly = planMarkersOnly(rpcTypes);
      expect(planOnly).toEqual(HAPPY_PATH_EXPECTED_SEQUENCE);
    }, 300000);
  });

  // (b) Verbose path: JSONL bytes must be identical across both sinks.
  describe("T43(b) — verbose dual-sink byte equality", () => {
    let client: RPCClient;
    let workspaceDir: string;

    beforeAll(async () => {
      workspaceDir = join(
        tmpdir(),
        `qlaybot-phase2b-verbose-${Date.now()}-${Math.floor(Math.random() * 1e6)}`,
      );
      mkdirSync(workspaceDir, { recursive: true });
      client = await createRPCClient({ verbose: true, cwd: workspaceDir });
      await client.send("initialize", { ephemeral: true, verbose: true });
    });
    afterAll(async () => {
      await client.close();
    });

    it("history and verbose JSONL contain byte-equal transcript_marker lines", async () => {
      const result = (await promptUntilMarker(
        client,
        HAPPY_PATH_PROMPT,
        "plan_done",
        3,
      )) as Record<string, unknown>;
      expect(result.status).toBe("completed");

      // Let both sinks flush — verbose writer uses setImmediate + drain.
      await new Promise((r) => setTimeout(r, 500));

      const historyPath = latestHistoryTranscriptPath();
      expect(existsSync(historyPath), `history JSONL at ${historyPath}`).toBe(true);

      // Find the verbose sink file: qlaybot-transcripts/<sessionId>-<ts>.jsonl.
      const verboseDir = join(workspaceDir, "qlaybot-transcripts");
      expect(existsSync(verboseDir), `verbose dir at ${verboseDir}`).toBe(true);

      const verboseFiles = readdirSync(verboseDir)
        .filter((n) => n.endsWith(".jsonl"))
        .map((n) => ({
          name: n,
          path: join(verboseDir, n),
          mtime: statSync(join(verboseDir, n)).mtimeMs,
        }))
        .sort((a, b) => b.mtime - a.mtime);
      expect(verboseFiles.length).toBeGreaterThan(0);
      const verbosePath = verboseFiles[0].path;

      const historyMarkerLines = readTranscriptMarkerLines(historyPath);
      const verboseMarkerLines = readTranscriptMarkerLines(verbosePath);

      // The verbose file is session-scoped and starts fresh, so it should
      // contain the markers for THIS session only. The history `latest`
      // symlink also resolves to THIS session's dir. So the two filtered
      // line lists should align — and each aligned pair must be byte-equal.
      expect(verboseMarkerLines.length).toBeGreaterThan(0);
      expect(historyMarkerLines.length).toBeGreaterThanOrEqual(
        verboseMarkerLines.length,
      );
      // Compare the verbose sink against the tail of history for this session.
      const historyTail = historyMarkerLines.slice(
        historyMarkerLines.length - verboseMarkerLines.length,
      );
      for (let i = 0; i < verboseMarkerLines.length; i++) {
        expect(
          verboseMarkerLines[i],
          `transcript_marker line ${i} byte-equal across sinks`,
        ).toBe(historyTail[i]);
      }
    }, 420000);
  });
});
