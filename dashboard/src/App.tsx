import { startTransition, useCallback, useEffect, useMemo, useState, type ChangeEvent } from "react";
import {
  CONTROL_JOB_STATUS,
  EVENT_TYPES,
  isControlState,
  isReplayData,
  type CaseLeaderboardEntry,
  type ControlJob,
  type ControlState,
  type ReplayAgentSummary,
  type ReplayData,
  type ReplayEvent,
  type RoundInsight,
} from "./types";
import "./styles.css";

const LIVE_REPLAY_SOURCE_LABEL = "Local live replay-data.json";
const DEMO_REPLAY_SOURCE_LABEL = "Bundled demo-replay.json";
const LIVE_REPLAY_FILE = "replay-data.json";
const DEMO_REPLAY_FILE = "demo-replay.json";
const CONTROL_API_BASE = (import.meta.env.VITE_AUTOSOLVER_API_BASE as string | undefined) ?? "http://127.0.0.1:8765";
const RUN_DRAFT_STORAGE_KEY = "autosolver.dashboard.runDraft.v1";
const PLAYBACK_SPEED_STORAGE_KEY = "autosolver.dashboard.playbackSpeed.v1";

const PROCESS_STEPS = [
  {
    step: "01",
    title: "Normalize Inputs",
    description: "Build one canonical view over orders, riders, probabilities, costs, and constraints.",
  },
  {
    step: "02",
    title: "Propose Strategy",
    description: "Agent proposes hypotheses instead of repeating one fixed algorithm end-to-end.",
  },
  {
    step: "03",
    title: "Run Solvers",
    description: "Local solver executes real benchmark runs and returns measurable outcomes.",
  },
  {
    step: "04",
    title: "Keep or Discard",
    description: "System compares outcomes, keeps winners, and carries lessons into next round.",
  },
] as const;

type StoryTone = "system" | "agent" | "tool" | "judge";

interface StoryBeat {
  id: string;
  ts: string;
  type: string;
  tone: StoryTone;
  label: string;
  title: string;
  body: string;
  meta: string[];
  experimentId: string | null;
  roundIndex: number | null;
  payload: Record<string, unknown>;
}

interface MetricCardProps {
  label: string;
  value: string;
  detail: string;
  tone?: "default" | "accent" | "quiet";
}

interface ControlBarProps {
  sourceLabel: string;
  benchmarkId: string | null;
  provider: string;
  autoRefreshEnabled: boolean;
  lastReloadedAt: string | null;
  onLoadLocalReplay: (event: ChangeEvent<HTMLInputElement>) => void;
  onUseDemoReplay: () => void;
  onReloadLiveReplay: () => void;
  onToggleAutoRefresh: () => void;
}

interface PlaybackControlsProps {
  totalBeats: number;
  visibleBeats: number;
  isPlaying: boolean;
  isFinished: boolean;
  speedMs: number;
  onStartPlayback: () => void;
  onTogglePlayback: () => void;
  onResetPlayback: () => void;
  onShowAll: () => void;
  onSpeedChange: (event: ChangeEvent<HTMLSelectElement>) => void;
}

interface SessionViewerProps {
  beats: StoryBeat[];
  currentBeat: StoryBeat | null;
  rounds: RoundInsight[];
  totalBeats: number;
  visibleBeats: number;
  isPlaybackMode: boolean;
  isPlaying: boolean;
  onOpenDetails: (beat: StoryBeat) => void;
}

interface SessionBubbleProps {
  beat: StoryBeat;
  isActive: boolean;
  onOpenDetails: (beat: StoryBeat) => void;
}

interface DetailModalProps {
  beat: StoryBeat | null;
  onClose: () => void;
}

interface RoundCardProps {
  round: RoundInsight;
}

interface CaseCardProps {
  row: CaseLeaderboardEntry;
}

interface RunDraft {
  benchmarkPath: string;
  instancePath: string;
  searchSpacePath: string;
  rounds: number;
  timeBudgetMs: number;
  seed: number;
  allowRuleBasedFallback: boolean;
}

const DEFAULT_RUN_DRAFT: RunDraft = {
  benchmarkPath: "examples/benchmarks/benchmark_manifest.json",
  instancePath: "examples/instances/sample_instance.json",
  searchSpacePath: "examples/research_search_space.json",
  rounds: 2,
  timeBudgetMs: 10_000,
  seed: 0,
  allowRuleBasedFallback: false,
};

type ControlRunKind = "pytest" | "smoke" | "research" | "benchmark" | "solve" | "solve-validate" | "solve-submit";

interface RunPreset {
  id: string;
  label: string;
  description: string;
  patch: Partial<RunDraft>;
  recommendedKind: ControlRunKind;
}

type UploadTarget = "benchmark" | "instance" | "searchSpace";

interface ControlConsoleProps {
  controlState: ControlState | null;
  controlError: string | null;
  controlNotice: string | null;
  isLaunching: boolean;
  draft: RunDraft;
  presets: RunPreset[];
  artifactPreviewPath: string | null;
  artifactPreviewBody: string | null;
  artifactPreviewError: string | null;
  onDraftChange: (patch: Partial<RunDraft>) => void;
  onApplyPreset: (preset: RunPreset) => void;
  onUploadFile: (target: UploadTarget, event: ChangeEvent<HTMLInputElement>) => void;
  onRun: (kind: ControlRunKind) => void;
  onCancel: (job: ControlJob) => void;
  onRefresh: () => void;
  onInspectArtifact: (path: string) => void;
  onLoadReplayArtifact: (path: string) => void;
}

const RUN_PRESETS: RunPreset[] = [
  {
    id: "demo-research",
    label: "Demo Research",
    description: "Two rounds on the default benchmark for a clean live demonstration.",
    patch: {
      benchmarkPath: "examples/benchmarks/benchmark_manifest.json",
      searchSpacePath: "examples/research_search_space.json",
      rounds: 2,
      timeBudgetMs: 10_000,
      seed: 0,
      allowRuleBasedFallback: false,
    },
    recommendedKind: "research",
  },
  {
    id: "cloud-probe",
    label: "Cloud Probe",
    description: "Use cloud probe benchmark to stress strategy selection under tougher mix.",
    patch: {
      benchmarkPath: "examples/generated/cloud_probe/benchmark_manifest.json",
      searchSpacePath: "examples/research_search_space.json",
      rounds: 2,
      timeBudgetMs: 10_000,
      seed: 0,
      allowRuleBasedFallback: true,
    },
    recommendedKind: "research",
  },
  {
    id: "sample-closed-loop",
    label: "Sample Closed Loop",
    description: "One-click solve, validate, and submission snapshot on sample instance.",
    patch: {
      instancePath: "examples/instances/sample_instance.json",
      timeBudgetMs: 10_000,
      seed: 0,
    },
    recommendedKind: "solve-submit",
  },
  {
    id: "quick-smoke",
    label: "Quick Smoke",
    description: "Fast end-to-end loop for sanity checks before deeper experiments.",
    patch: {
      rounds: 1,
      timeBudgetMs: 500,
      seed: 9,
      allowRuleBasedFallback: true,
    },
    recommendedKind: "smoke",
  },
];

const countFormatter = new Intl.NumberFormat("zh-CN", {
  maximumFractionDigits: 2,
});

const integerFormatter = new Intl.NumberFormat("zh-CN", {
  maximumFractionDigits: 0,
});

const timeFormatter = new Intl.DateTimeFormat("zh-CN", {
  dateStyle: "medium",
  timeStyle: "short",
});

function formatCount(value: number | null | undefined): string {
  return typeof value === "number" ? countFormatter.format(value) : "N/A";
}

function formatInteger(value: number | null | undefined): string {
  return typeof value === "number" ? integerFormatter.format(value) : "N/A";
}

function formatTimestamp(value: string | null | undefined): string {
  if (!value) {
    return "N/A";
  }
  const parsed = new Date(value);
  return Number.isNaN(parsed.getTime()) ? value : timeFormatter.format(parsed);
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null;
}

function asString(value: unknown): string | null {
  return typeof value === "string" ? value : null;
}

function asNumber(value: unknown): number | null {
  return typeof value === "number" ? value : null;
}

function asBoolean(value: unknown): boolean | null {
  return typeof value === "boolean" ? value : null;
}

function asStringArray(value: unknown): string[] {
  if (!Array.isArray(value)) {
    return [];
  }
  return value.map((item) => String(item)).filter((item) => item.trim().length > 0);
}

function sanitizeRunDraft(value: Partial<RunDraft>): RunDraft {
  return {
    benchmarkPath: typeof value.benchmarkPath === "string" ? value.benchmarkPath : DEFAULT_RUN_DRAFT.benchmarkPath,
    instancePath: typeof value.instancePath === "string" ? value.instancePath : DEFAULT_RUN_DRAFT.instancePath,
    searchSpacePath: typeof value.searchSpacePath === "string" ? value.searchSpacePath : DEFAULT_RUN_DRAFT.searchSpacePath,
    rounds:
      typeof value.rounds === "number" && Number.isFinite(value.rounds)
        ? Math.max(1, Math.min(12, Math.round(value.rounds)))
        : DEFAULT_RUN_DRAFT.rounds,
    timeBudgetMs:
      typeof value.timeBudgetMs === "number" && Number.isFinite(value.timeBudgetMs)
        ? Math.max(100, Math.round(value.timeBudgetMs))
        : DEFAULT_RUN_DRAFT.timeBudgetMs,
    seed:
      typeof value.seed === "number" && Number.isFinite(value.seed) ? Math.max(0, Math.round(value.seed)) : DEFAULT_RUN_DRAFT.seed,
    allowRuleBasedFallback:
      typeof value.allowRuleBasedFallback === "boolean" ? value.allowRuleBasedFallback : DEFAULT_RUN_DRAFT.allowRuleBasedFallback,
  };
}

function loadStoredRunDraft(): RunDraft {
  try {
    const raw = window.localStorage.getItem(RUN_DRAFT_STORAGE_KEY);
    if (!raw) {
      return DEFAULT_RUN_DRAFT;
    }
    const parsed: unknown = JSON.parse(raw);
    if (!isRecord(parsed)) {
      return DEFAULT_RUN_DRAFT;
    }
    return sanitizeRunDraft(parsed as Partial<RunDraft>);
  } catch {
    return DEFAULT_RUN_DRAFT;
  }
}

function loadStoredPlaybackSpeed(): number {
  try {
    const raw = window.localStorage.getItem(PLAYBACK_SPEED_STORAGE_KEY);
    const parsed = Number(raw);
    if (!Number.isFinite(parsed)) {
      return 1200;
    }
    return Math.max(300, Math.min(1800, parsed));
  } catch {
    return 1200;
  }
}

function summarizeConfig(config: Record<string, unknown> | null): string[] {
  if (!config) {
    return [];
  }

  return [
    `top_k=${String(config["top_k_riders_per_order"] ?? "N/A")}`,
    `cpsat=${config["use_cpsat"] === false ? "off" : "on"}`,
    `lns=${config["use_lns"] === false ? "off" : "on"}`,
    `bundle_gen=${config["generate_bundles_if_missing"] === false ? "off" : "on"}`,
    `bundle_pool=${String(config["bundle_candidate_pool_size"] ?? "N/A")}`,
    `lns_iter=${String(config["lns_iterations"] ?? "N/A")}`,
  ];
}

function formatStatus(status: string | null | undefined): string {
  if (!status) {
    return "Unknown";
  }
  if (status === "keep") {
    return "Keep";
  }
  if (status === "discard") {
    return "Discard";
  }
  if (status === "crash") {
    return "Crash";
  }
  if (status === "pending") {
    return "Pending";
  }
  if (status === CONTROL_JOB_STATUS.QUEUED) {
    return "Queued";
  }
  if (status === CONTROL_JOB_STATUS.RUNNING) {
    return "Running";
  }
  if (status === CONTROL_JOB_STATUS.CANCELLING) {
    return "Cancelling";
  }
  if (status === CONTROL_JOB_STATUS.SUCCEEDED) {
    return "Succeeded";
  }
  if (status === CONTROL_JOB_STATUS.FAILED) {
    return "Failed";
  }
  if (status === CONTROL_JOB_STATUS.CANCELLED) {
    return "Cancelled";
  }
  return status;
}

function toStatusClass(status: string | null | undefined): string {
  if (!status) {
    return "unknown";
  }
  return status.toLowerCase().replace(/[^a-z0-9_-]+/g, "-");
}

function canCancelControlJob(job: ControlJob | null): boolean {
  if (!job) {
    return false;
  }
  return job.status === CONTROL_JOB_STATUS.RUNNING || job.status === CONTROL_JOB_STATUS.QUEUED || job.status === CONTROL_JOB_STATUS.CANCELLING;
}

function formatProposalType(value: string): string {
  if (value === "llm") {
    return "LLM";
  }
  if (value === "fallback") {
    return "Fallback";
  }
  return "Unknown";
}

function toProposalClass(value: string): string {
  if (value === "llm") {
    return "llm";
  }
  if (value === "fallback") {
    return "fallback";
  }
  return "fallback";
}

function formatRunKind(kind: ControlRunKind | string): string {
  if (kind === "pytest") {
    return "Pytest";
  }
  if (kind === "smoke") {
    return "Smoke";
  }
  if (kind === "research") {
    return "Research";
  }
  if (kind === "benchmark") {
    return "Benchmark";
  }
  if (kind === "solve") {
    return "Solve";
  }
  if (kind === "solve-validate") {
    return "Solve + Validate";
  }
  if (kind === "solve-submit") {
    return "Solve + Submit";
  }
  return kind;
}

function isLiveReplaySource(sourceLabel: string): boolean {
  return sourceLabel === LIVE_REPLAY_SOURCE_LABEL;
}

function errorToMessage(error: unknown, fallback: string): string {
  return error instanceof Error ? error.message : fallback;
}

function readApiError(payload: unknown, fallback: string): string {
  if (!isRecord(payload)) {
    return fallback;
  }
  return asString(payload.error) ?? fallback;
}

function parseControlStateFromEnvelope(payload: unknown): ControlState {
  if (isControlState(payload)) {
    return payload;
  }
  if (isRecord(payload) && isControlState(payload.state)) {
    return payload.state;
  }
  throw new Error("Control API response schema is invalid.");
}

function shouldTreatAsReplayArtifact(label: string, path: string): boolean {
  const merged = `${label} ${path}`.toLowerCase();
  return merged.includes("replay") && path.toLowerCase().endsWith(".json");
}

async function loadReplayPayload(filename: string): Promise<ReplayData> {
  const replayUrl = `${import.meta.env.BASE_URL}${filename}?ts=${Date.now()}`;
  const response = await fetch(replayUrl, { cache: "no-store" });
  if (!response.ok) {
    throw new Error(`Failed to load ${filename}: ${response.status} ${response.statusText}`);
  }
  const payload: unknown = await response.json();
  if (!isReplayData(payload)) {
    throw new Error(`${filename} is not a valid replay JSON payload.`);
  }
  return payload;
}

async function loadControlSnapshot(): Promise<ControlState> {
  const response = await fetch(`${CONTROL_API_BASE}/api/control/status?ts=${Date.now()}`, { cache: "no-store" });
  if (!response.ok) {
    throw new Error(`Failed to load control state: ${response.status} ${response.statusText}`);
  }
  const payload: unknown = await response.json();
  return parseControlStateFromEnvelope(payload);
}

async function runControlJob(kind: ControlRunKind, draft: RunDraft): Promise<ControlState> {
  const response = await fetch(`${CONTROL_API_BASE}/api/control/run`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      kind,
      benchmarkPath: draft.benchmarkPath,
      instancePath: draft.instancePath,
      searchSpacePath: draft.searchSpacePath,
      rounds: draft.rounds,
      timeBudgetMs: draft.timeBudgetMs,
      seed: draft.seed,
      allowRuleBasedFallback: draft.allowRuleBasedFallback,
    }),
  });

  const payload: unknown = await response.json().catch(() => null);
  if (!response.ok) {
    throw new Error(readApiError(payload, "Control API rejected this run request."));
  }
  return parseControlStateFromEnvelope(payload);
}

async function cancelControlJob(jobId: string): Promise<ControlState> {
  const response = await fetch(`${CONTROL_API_BASE}/api/control/jobs/${jobId}/cancel`, {
    method: "POST",
  });
  const payload: unknown = await response.json().catch(() => null);
  if (!response.ok) {
    throw new Error(readApiError(payload, "Control API rejected this cancel request."));
  }
  return parseControlStateFromEnvelope(payload);
}

async function uploadControlFile(target: UploadTarget, file: File): Promise<string> {
  const response = await fetch(`${CONTROL_API_BASE}/api/control/upload`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      target,
      filename: file.name,
      content: await file.text(),
    }),
  });

  const payload: unknown = await response.json().catch(() => null);
  if (!response.ok) {
    throw new Error(readApiError(payload, "Failed to upload file to control API."));
  }
  if (!isRecord(payload) || typeof payload.path !== "string") {
    throw new Error("Upload succeeded but response path is missing.");
  }
  return payload.path;
}

async function loadControlArtifactText(path: string): Promise<{ body: string; contentType: string }> {
  const response = await fetch(`${CONTROL_API_BASE}/api/control/file?path=${encodeURIComponent(path)}`, { cache: "no-store" });
  if (!response.ok) {
    throw new Error(`Failed to load artifact: ${response.status} ${response.statusText}`);
  }
  return {
    body: await response.text(),
    contentType: response.headers.get("content-type") ?? "text/plain",
  };
}

function buildStoryBeats(events: ReplayEvent[]): StoryBeat[] {
  const beats: StoryBeat[] = [];

  events.forEach((event, index) => {
    const payload = isRecord(event.payload) ? event.payload : {};
    const experimentId = asString(payload.experiment_id);
    const roundIndex = asNumber(payload.round_index);

    if (event.type === EVENT_TYPES.RESEARCH_SESSION_STARTED) {
      const benchmarkId = asString(payload.benchmark_id) ?? "unknown-benchmark";
      const provider = asString(payload.provider) ?? "unknown-provider";
      const llmEnabled = asBoolean(payload.llm_enabled);
      beats.push({
        id: `${event.ts}-${event.type}-${index}`,
        ts: event.ts,
        type: event.type,
        tone: "system",
        label: "System",
        title: "Research Session Started",
        body: `Session opened on benchmark ${benchmarkId}. The agent will now iterate on strategies and validate outcomes.`,
        meta: [provider, llmEnabled ? "LLM enabled" : "Fallback mode"],
        experimentId,
        roundIndex,
        payload,
      });
      return;
    }

    if (event.type === EVENT_TYPES.RESEARCH_SESSION_RESUMED) {
      beats.push({
        id: `${event.ts}-${event.type}-${index}`,
        ts: event.ts,
        type: event.type,
        tone: "system",
        label: "System",
        title: "Session Resumed",
        body: "Previous state was restored. New rounds continue from prior research memory.",
        meta: [asString(payload.state_path) ?? "state_path unavailable"],
        experimentId,
        roundIndex,
        payload,
      });
      return;
    }

    if (event.type === EVENT_TYPES.RESEARCH_LLM_PROPOSAL || event.type === EVENT_TYPES.RESEARCH_FALLBACK_PROPOSAL) {
      const isLlm = event.type === EVENT_TYPES.RESEARCH_LLM_PROPOSAL;
      beats.push({
        id: `${event.ts}-${event.type}-${index}`,
        ts: event.ts,
        type: event.type,
        tone: "agent",
        label: isLlm ? "Agent" : "Fallback",
        title: isLlm ? "New Strategy Proposal" : "Fallback Strategy Proposal",
        body: asString(payload.hypothesis) ?? "No hypothesis recorded for this proposal.",
        meta: summarizeConfig(isRecord(payload.solver_config) ? payload.solver_config : null),
        experimentId,
        roundIndex,
        payload,
      });
      return;
    }

    if (event.type === EVENT_TYPES.RESEARCH_ROUND_STARTED) {
      beats.push({
        id: `${event.ts}-${event.type}-${index}`,
        ts: event.ts,
        type: event.type,
        tone: "tool",
        label: "Tool",
        title: "Round Execution Started",
        body: "Local solver is running this proposal over benchmark cases.",
        meta: summarizeConfig(isRecord(payload.solver_config) ? payload.solver_config : null),
        experimentId,
        roundIndex,
        payload,
      });
      return;
    }

    if (event.type === EVENT_TYPES.BENCHMARK_CASE_COMPLETED) {
      const stats = isRecord(payload.stats) ? payload.stats : null;
      const candidateBreakdown = isRecord(stats?.candidate_option_breakdown) ? stats.candidate_option_breakdown : null;
      const strategy = asString(stats?.strategy) ?? "portfolio";
      const candidateCount = asNumber(stats?.candidate_option_count);
      const bundleCount = asNumber(candidateBreakdown?.bundle);
      const caseId = asString(payload.case_id) ?? "unknown-case";
      beats.push({
        id: `${event.ts}-${event.type}-${index}-${caseId}`,
        ts: event.ts,
        type: event.type,
        tone: "tool",
        label: "Tool Result",
        title: `Case Completed: ${caseId}`,
        body: `expected=${formatCount(payload.expected_completed_orders as number | null)}, cost=${formatCount(payload.total_cost as number | null)}, elapsed=${formatInteger(payload.elapsed_ms as number | null)} ms`,
        meta: [
          `strategy=${strategy}`,
          candidateCount !== null ? `candidates=${formatInteger(candidateCount)}` : "candidates=N/A",
          bundleCount !== null ? `bundle_candidates=${formatInteger(bundleCount)}` : "bundle_candidates=N/A",
        ],
        experimentId,
        roundIndex,
        payload,
      });
      return;
    }

    if (event.type === EVENT_TYPES.BENCHMARK_COMPLETED) {
      beats.push({
        id: `${event.ts}-${event.type}-${index}`,
        ts: event.ts,
        type: event.type,
        tone: "judge",
        label: "Judge",
        title: "Benchmark Round Aggregated",
        body: `avg_expected=${formatCount(payload.average_expected_completed_orders as number | null)}, avg_cost=${formatCount(payload.average_total_cost as number | null)}`,
        meta: [
          `elapsed=${formatInteger(payload.total_elapsed_ms as number | null)} ms`,
          `cases=${formatInteger(payload.case_count as number | null)}`,
        ],
        experimentId,
        roundIndex,
        payload,
      });
      return;
    }

    if (event.type === EVENT_TYPES.RESEARCH_ROUND_COMPLETED) {
      const status = asString(payload.status);
      const detail =
        status === "keep"
          ? "Round improved incumbent and was kept."
          : status === "discard"
            ? "Round did not beat incumbent and was discarded."
            : "Round completed with a non-standard status.";
      beats.push({
        id: `${event.ts}-${event.type}-${index}`,
        ts: event.ts,
        type: event.type,
        tone: "judge",
        label: "Judge",
        title: `Round Completed: ${formatStatus(status)}`,
        body: detail,
        meta: [
          `expected=${formatCount(payload.average_expected_completed_orders as number | null)}`,
          `cost=${formatCount(payload.average_total_cost as number | null)}`,
          `elapsed=${formatInteger(payload.total_elapsed_ms as number | null)} ms`,
        ],
        experimentId,
        roundIndex,
        payload,
      });
      return;
    }

    if (event.type === EVENT_TYPES.RESEARCH_INCUMBENT_UPDATED) {
      beats.push({
        id: `${event.ts}-${event.type}-${index}`,
        ts: event.ts,
        type: event.type,
        tone: "system",
        label: "System",
        title: "Incumbent Updated",
        body: `New incumbent is ${experimentId ?? "unknown experiment"} with better objective performance.`,
        meta: [
          `expected=${formatCount(payload.average_expected_completed_orders as number | null)}`,
          `cost=${formatCount(payload.average_total_cost as number | null)}`,
        ],
        experimentId,
        roundIndex,
        payload,
      });
      return;
    }

    if (event.type === EVENT_TYPES.RESEARCH_LLM_REFLECTION || event.type === EVENT_TYPES.RESEARCH_HEURISTIC_REFLECTION) {
      const nextFocus = asStringArray(payload.next_focus);
      const keepReason = asString(payload.keep_reason);
      beats.push({
        id: `${event.ts}-${event.type}-${index}`,
        ts: event.ts,
        type: event.type,
        tone: "agent",
        label: event.type === EVENT_TYPES.RESEARCH_LLM_REFLECTION ? "Agent Reflection" : "Heuristic Reflection",
        title: "Reflection for Next Iteration",
        body: asString(payload.summary) ?? "No reflection summary was recorded.",
        meta: [...(keepReason ? [keepReason] : []), ...nextFocus.slice(0, 2)],
        experimentId,
        roundIndex,
        payload,
      });
      return;
    }

    if (event.type === EVENT_TYPES.RESEARCH_ROUND_FAILED) {
      beats.push({
        id: `${event.ts}-${event.type}-${index}`,
        ts: event.ts,
        type: event.type,
        tone: "judge",
        label: "Failure",
        title: "Round Failed",
        body: asString(payload.error) ?? "Round failed with unknown error.",
        meta: ["Failure is logged and the session can continue with another proposal."],
        experimentId,
        roundIndex,
        payload,
      });
      return;
    }

    beats.push({
      id: `${event.ts}-${event.type}-${index}`,
      ts: event.ts,
      type: event.type,
      tone: "tool",
      label: "Event",
      title: event.type,
      body: "Event was recorded but has no dedicated story card mapping.",
      meta: [],
      experimentId,
      roundIndex,
      payload,
    });
  });

  return beats;
}

function selectBestRound(rounds: RoundInsight[]): RoundInsight | null {
  let bestRound: RoundInsight | null = null;

  for (const round of rounds) {
    if (typeof round.averageExpectedCompletedOrders !== "number" || typeof round.averageTotalCost !== "number") {
      continue;
    }
    if (bestRound === null) {
      bestRound = round;
      continue;
    }
    if ((round.averageExpectedCompletedOrders ?? 0) > (bestRound.averageExpectedCompletedOrders ?? 0)) {
      bestRound = round;
      continue;
    }
    if (
      round.averageExpectedCompletedOrders === bestRound.averageExpectedCompletedOrders &&
      (round.averageTotalCost ?? Number.POSITIVE_INFINITY) < (bestRound.averageTotalCost ?? Number.POSITIVE_INFINITY)
    ) {
      bestRound = round;
    }
  }
  return bestRound;
}

function buildChartPoints(rounds: RoundInsight[]): { x: number; expected: number; cost: number }[] {
  return rounds
    .filter((round) => typeof round.averageExpectedCompletedOrders === "number" && typeof round.averageTotalCost === "number")
    .map((round, index) => ({
      x: index,
      expected: round.averageExpectedCompletedOrders ?? 0,
      cost: round.averageTotalCost ?? 0,
    }));
}

function useTypedText(text: string, active: boolean): string {
  const [visibleLength, setVisibleLength] = useState(active ? 0 : text.length);

  useEffect(() => {
    if (!active) {
      setVisibleLength(text.length);
      return;
    }

    setVisibleLength(0);
    const step = Math.max(3, Math.ceil(text.length / 42));
    const timerId = window.setInterval(() => {
      setVisibleLength((current) => {
        if (current >= text.length) {
          window.clearInterval(timerId);
          return text.length;
        }
        return Math.min(text.length, current + step);
      });
    }, 24);

    return () => {
      window.clearInterval(timerId);
    };
  }, [active, text]);

  return active ? text.slice(0, visibleLength) : text;
}

function MetricCard({ label, value, detail, tone = "default" }: MetricCardProps) {
  return (
    <article className={`metric-card metric-${tone}`}>
      <p className="metric-label">{label}</p>
      <strong className="metric-value">{value}</strong>
      <p className="metric-detail">{detail}</p>
    </article>
  );
}

function ControlBar({
  sourceLabel,
  benchmarkId,
  provider,
  autoRefreshEnabled,
  lastReloadedAt,
  onLoadLocalReplay,
  onUseDemoReplay,
  onReloadLiveReplay,
  onToggleAutoRefresh,
}: ControlBarProps) {
  return (
    <section className="control-bar">
      <div className="control-copy">
        <p className="section-eyebrow">Replay Source</p>
        <strong className="control-source" translate="no">
          {sourceLabel}
        </strong>
        <p className="control-meta">
          Benchmark: <span translate="no">{benchmarkId ?? "N/A"}</span>
          <span className="control-sep" aria-hidden="true">
            /
          </span>
          Provider: <span translate="no">{provider}</span>
        </p>
        <div className="live-row">
          <span className={`live-pill ${autoRefreshEnabled ? "live-active" : "live-static"}`}>
            {autoRefreshEnabled ? "Auto refresh on" : "Auto refresh off"}
          </span>
          <span className="live-time">Last sync: {formatTimestamp(lastReloadedAt)}</span>
        </div>
      </div>
      <div className="control-actions">
        <button className="ghost-button" type="button" onClick={onReloadLiveReplay}>
          Reload Live
        </button>
        <button className="ghost-button" type="button" onClick={onUseDemoReplay}>
          Use Demo Replay
        </button>
        <button className="ghost-button" type="button" onClick={onToggleAutoRefresh}>
          {autoRefreshEnabled ? "Pause Auto Refresh" : "Resume Auto Refresh"}
        </button>
        <label className="primary-button upload-button">
          Upload Replay JSON
          <input className="upload-input" type="file" accept="application/json,.json" onChange={onLoadLocalReplay} />
        </label>
      </div>
    </section>
  );
}

function PlaybackControls({
  totalBeats,
  visibleBeats,
  isPlaying,
  isFinished,
  speedMs,
  onStartPlayback,
  onTogglePlayback,
  onResetPlayback,
  onShowAll,
  onSpeedChange,
}: PlaybackControlsProps) {
  const shownCount = visibleBeats <= 0 ? totalBeats : Math.min(visibleBeats, totalBeats);
  const progress = totalBeats === 0 ? 0 : (shownCount / totalBeats) * 100;

  return (
    <section className="playback-panel">
      <div className="playback-copy">
        <p className="section-eyebrow">Playback</p>
        <h2>Follow The Agent Session As A Playable Story</h2>
        <p className="section-text">
          Use playback mode to reveal proposals, solver runs, benchmark outcomes, and reflection decisions in order.
        </p>
        <div className="progress-track" aria-hidden="true">
          <div className="progress-fill" style={{ width: `${progress}%` }} />
        </div>
        <div className="progress-meta">
          <span className="meta-chip">Total beats {formatInteger(totalBeats)}</span>
          <span className="meta-chip">Shown {formatInteger(shownCount)}</span>
          <span className="meta-chip">
            {isPlaying ? "Playing" : isFinished ? "Finished" : visibleBeats > 0 ? "Paused" : "Full view"}
          </span>
        </div>
      </div>
      <div className="playback-actions">
        <div className="button-row">
          <button className="primary-button" type="button" onClick={onStartPlayback} disabled={totalBeats === 0}>
            Start Playback
          </button>
          <button className="ghost-button" type="button" onClick={onTogglePlayback} disabled={totalBeats === 0}>
            {isPlaying ? "Pause" : visibleBeats > 0 ? "Resume" : "Play From Start"}
          </button>
        </div>
        <div className="button-row">
          <button className="ghost-button" type="button" onClick={onResetPlayback} disabled={totalBeats === 0}>
            Clear Animation
          </button>
          <button className="ghost-button" type="button" onClick={onShowAll} disabled={totalBeats === 0}>
            Show All At Once
          </button>
        </div>
        <label className="speed-box">
          Playback speed
          <select value={String(speedMs)} onChange={onSpeedChange}>
            <option value="700">Fast</option>
            <option value="1200">Normal</option>
            <option value="1800">Slow</option>
          </select>
        </label>
      </div>
    </section>
  );
}

function ControlConsole({
  controlState,
  controlError,
  controlNotice,
  isLaunching,
  draft,
  presets,
  artifactPreviewPath,
  artifactPreviewBody,
  artifactPreviewError,
  onDraftChange,
  onApplyPreset,
  onUploadFile,
  onRun,
  onCancel,
  onRefresh,
  onInspectArtifact,
  onLoadReplayArtifact,
}: ControlConsoleProps) {
  const currentJob = controlState?.currentJob ?? null;
  const queuedJobs = controlState?.queuedJobs ?? [];
  const recentJobs = controlState?.recentJobs ?? [];
  const queuedJobIds = new Set(queuedJobs.map((job) => job.jobId));
  const historyJobs = recentJobs.filter((job) => job.jobId !== currentJob?.jobId && !queuedJobIds.has(job.jobId));
  const previewVisible = Boolean(artifactPreviewPath || artifactPreviewError);
  const canLaunchJobs = Boolean(controlState) && !isLaunching;

  return (
    <section className="panel control-console-panel">
      <div className="panel-head">
        <p className="section-eyebrow">Web Control Console</p>
        <h2>Run Pytest, Research, Solve, And Submission Flows From The Browser</h2>
      </div>

      <div className="control-console-grid">
        <div className="control-console-copy">
          <p className="section-text">
            This panel talks to local <code>autosolver-web</code>. You can queue runs, inspect artifacts, and load replay outputs without switching to terminal.
          </p>
          <div className="hero-chips">
            <span className={`meta-chip ${controlState ? "" : "meta-chip-muted"}`}>{controlState ? "Control API connected" : "Control API unavailable"}</span>
            <span className="meta-chip">API {controlState?.apiBase ?? CONTROL_API_BASE}</span>
            <span className="meta-chip">{controlState?.provider.llmConfigured ? "LLM configured" : "LLM not configured"}</span>
          </div>
        </div>

        <div className="control-console-fields">
          <div className="control-preset-block">
            <span>Run Presets</span>
            <div className="preset-grid">
              {presets.map((preset) => (
                <button className="preset-card" key={preset.id} type="button" onClick={() => onApplyPreset(preset)}>
                  <strong>{preset.label}</strong>
                  <p>{preset.description}</p>
                  <span className="config-chip">Recommended: {formatRunKind(preset.recommendedKind)}</span>
                </button>
              ))}
            </div>
          </div>

          <label className="control-field">
            <span>Benchmark Path</span>
            <input
              type="text"
              value={draft.benchmarkPath}
              onChange={(event) => onDraftChange({ benchmarkPath: event.target.value })}
              placeholder="examples/benchmarks/benchmark_manifest.json"
            />
            <div className="control-field-actions">
              <label className="ghost-button upload-inline-button">
                Upload benchmark / manifest
                <input className="upload-input" type="file" accept=".json,.jsonl,.txt" onChange={(event) => onUploadFile("benchmark", event)} />
              </label>
            </div>
          </label>

          <label className="control-field">
            <span>Instance Path</span>
            <input
              type="text"
              value={draft.instancePath}
              onChange={(event) => onDraftChange({ instancePath: event.target.value })}
              placeholder="examples/instances/sample_instance.json"
            />
            <div className="control-field-actions">
              <label className="ghost-button upload-inline-button">
                Upload instance JSON
                <input className="upload-input" type="file" accept=".json,.jsonl,.txt" onChange={(event) => onUploadFile("instance", event)} />
              </label>
            </div>
          </label>

          <label className="control-field">
            <span>Search Space Path</span>
            <input
              type="text"
              value={draft.searchSpacePath}
              onChange={(event) => onDraftChange({ searchSpacePath: event.target.value })}
              placeholder="examples/research_search_space.json"
            />
            <div className="control-field-actions">
              <label className="ghost-button upload-inline-button">
                Upload search space
                <input className="upload-input" type="file" accept=".json,.jsonl,.txt" onChange={(event) => onUploadFile("searchSpace", event)} />
              </label>
            </div>
          </label>

          <label className="control-field control-field-small">
            <span>Rounds</span>
            <input type="number" min={1} max={12} value={draft.rounds} onChange={(event) => onDraftChange({ rounds: Number(event.target.value) || 1 })} />
          </label>

          <label className="control-field control-field-small">
            <span>Time Budget (ms)</span>
            <input
              type="number"
              min={100}
              step={100}
              value={draft.timeBudgetMs}
              onChange={(event) => onDraftChange({ timeBudgetMs: Number(event.target.value) || 10_000 })}
            />
          </label>

          <label className="control-field control-field-small">
            <span>Seed</span>
            <input type="number" min={0} value={draft.seed} onChange={(event) => onDraftChange({ seed: Number(event.target.value) || 0 })} />
          </label>

          <label className="control-checkbox">
            <input
              type="checkbox"
              checked={draft.allowRuleBasedFallback}
              onChange={(event) => onDraftChange({ allowRuleBasedFallback: event.target.checked })}
            />
            <span>Allow rule-based fallback</span>
          </label>
        </div>
      </div>

      <div className="control-console-actions">
        <button className="ghost-button" type="button" onClick={() => onRun("pytest")} disabled={!canLaunchJobs}>
          Run Pytest
        </button>
        <button className="ghost-button" type="button" onClick={() => onRun("smoke")} disabled={!canLaunchJobs}>
          Run Smoke
        </button>
        <button className="primary-button" type="button" onClick={() => onRun("research")} disabled={!canLaunchJobs}>
          Run Research
        </button>
        <button className="ghost-button" type="button" onClick={() => onRun("benchmark")} disabled={!canLaunchJobs}>
          Run Benchmark
        </button>
        <button className="ghost-button" type="button" onClick={() => onRun("solve")} disabled={!canLaunchJobs}>
          Run Solve
        </button>
        <button className="ghost-button" type="button" onClick={() => onRun("solve-validate")} disabled={!canLaunchJobs}>
          Run Solve + Validate
        </button>
        <button className="ghost-button" type="button" onClick={() => onRun("solve-submit")} disabled={!canLaunchJobs}>
          Run Solve + Submit
        </button>
        <button className="ghost-button" type="button" onClick={onRefresh} disabled={isLaunching}>
          Refresh State
        </button>
        {currentJob ? (
          <button className="ghost-button" type="button" onClick={() => onCancel(currentJob)} disabled={!canCancelControlJob(currentJob)}>
            Stop Current Job
          </button>
        ) : null}
      </div>

      {currentJob ? (
        <div className="queue-banner" role="status" aria-live="polite">
          <strong>Worker is busy.</strong>
          <span>
            New actions will be queued automatically.
            {queuedJobs.length > 0 ? ` Waiting jobs: ${queuedJobs.length}.` : ""}
          </span>
        </div>
      ) : null}

      {controlError ? (
        <div className="error-banner" role="status" aria-live="polite">
          {controlError}
        </div>
      ) : null}

      {!controlError && controlNotice ? (
        <div className="success-banner" role="status" aria-live="polite">
          {controlNotice}
        </div>
      ) : null}

      <div className="control-console-grid control-console-status-grid">
        <section className="control-status-card">
          <div className="session-round-row">
            <span className={`status-pill status-${toStatusClass(currentJob?.status)}`}>{formatStatus(currentJob?.status ?? "pending")}</span>
            <span className="meta-chip">{currentJob ? formatRunKind(currentJob.kind) : "No running job"}</span>
          </div>
          <p className="section-text control-status-copy">
            {currentJob
              ? `Started at ${formatTimestamp(currentJob.startedAt)}${currentJob.finishedAt ? `, finished at ${formatTimestamp(currentJob.finishedAt)}.` : ", still running."}`
              : "Control service is ready. Choose any action to start."}
          </p>
          {currentJob?.outputRoot ? <p className="control-path">Output root: {currentJob.outputRoot}</p> : null}
          {currentJob?.dashboardReplayPath ? <p className="control-path">Replay path: {currentJob.dashboardReplayPath}</p> : null}
          {currentJob?.command.length ? (
            <div className="command-preview">
              <strong>Current command</strong>
              <code>{currentJob.command.join(" ")}</code>
            </div>
          ) : null}
          {currentJob && Object.keys(currentJob.artifacts).length > 0 ? (
            <div className="artifact-actions">
              <strong>Current artifacts</strong>
              <div className="artifact-chip-row">
                {Object.entries(currentJob.artifacts)
                  .filter(([, path]) => path)
                  .map(([label, path]) => (
                    <div className="artifact-chip-card" key={`${currentJob.jobId}-${label}`}>
                      <span className="config-chip">{label}</span>
                      <button className="ghost-button" type="button" onClick={() => onInspectArtifact(path)}>
                        Preview
                      </button>
                      {shouldTreatAsReplayArtifact(label, path) ? (
                        <button className="ghost-button" type="button" onClick={() => onLoadReplayArtifact(path)}>
                          Load Replay
                        </button>
                      ) : null}
                    </div>
                  ))}
              </div>
            </div>
          ) : null}
        </section>

        <section className="control-status-card">
          <strong>Live log tail</strong>
          <pre className="control-log">
            {currentJob?.logTail || "Most recent command output will appear here after a job starts."}
          </pre>
        </section>
      </div>

      <section className="control-status-card">
        <div className="session-round-row">
          <strong>Queue</strong>
          <span className={`status-pill status-${queuedJobs.length > 0 ? CONTROL_JOB_STATUS.QUEUED : "unknown"}`}>
            {queuedJobs.length > 0 ? `${queuedJobs.length} waiting` : "empty"}
          </span>
        </div>
        <p className="section-text control-status-copy">
          Web control runs jobs in sequence. You can launch multiple actions and they will execute in order.
        </p>
        <div className="job-history-list queue-history-list">
          {queuedJobs.length > 0 ? (
            queuedJobs.map((job) => (
              <article className="job-history-item queue-job-item" key={job.jobId}>
                <div className="session-round-row">
                  <span className={`status-pill status-${toStatusClass(job.status)}`}>{formatStatus(job.status)}</span>
                  <strong translate="no">{job.jobId}</strong>
                </div>
                <p>
                  {formatRunKind(job.kind)}
                  {job.outputRoot ? ` / ${job.outputRoot}` : ""}
                </p>
                {job.command.length > 0 ? (
                  <div className="command-preview queue-command-preview">
                    <strong>Queued command</strong>
                    <code>{job.command.join(" ")}</code>
                  </div>
                ) : null}
                <div className="artifact-chip-row">
                  <button className="ghost-button" type="button" onClick={() => onCancel(job)} disabled={!canCancelControlJob(job)}>
                    Cancel Queue Entry
                  </button>
                </div>
              </article>
            ))
          ) : (
            <div className="empty-state">No queued jobs.</div>
          )}
        </div>
      </section>

      <section className="control-status-card">
        <strong>Recent jobs</strong>
        <div className="job-history-list">
          {historyJobs.length > 0 ? (
            historyJobs.map((job) => (
              <article className="job-history-item" key={job.jobId}>
                <div className="session-round-row">
                  <span className={`status-pill status-${toStatusClass(job.status)}`}>{formatStatus(job.status)}</span>
                  <strong translate="no">{job.jobId}</strong>
                </div>
                <p>
                  {formatRunKind(job.kind)} / started {formatTimestamp(job.startedAt)}
                  {job.outputRoot ? ` / ${job.outputRoot}` : ""}
                </p>
                {canCancelControlJob(job) ? (
                  <div className="artifact-chip-row">
                    <button className="ghost-button" type="button" onClick={() => onCancel(job)}>
                      {job.status === CONTROL_JOB_STATUS.QUEUED ? "Cancel Queue Entry" : "Stop Job"}
                    </button>
                  </div>
                ) : null}
                {Object.keys(job.artifacts).length > 0 ? (
                  <div className="artifact-chip-row">
                    {Object.entries(job.artifacts)
                      .filter(([, path]) => path)
                      .map(([label, path]) => (
                        <div className="artifact-chip-card" key={`${job.jobId}-${label}`}>
                          <span className="config-chip">{label}</span>
                          <button className="ghost-button" type="button" onClick={() => onInspectArtifact(path)}>
                            Preview
                          </button>
                          {shouldTreatAsReplayArtifact(label, path) ? (
                            <button className="ghost-button" type="button" onClick={() => onLoadReplayArtifact(path)}>
                              Load Replay
                            </button>
                          ) : null}
                        </div>
                      ))}
                  </div>
                ) : null}
              </article>
            ))
          ) : (
            <div className="empty-state">No finished web-control jobs yet.</div>
          )}
        </div>
      </section>

      {previewVisible ? (
        <section className="control-status-card">
          <strong>Artifact preview</strong>
          {artifactPreviewPath ? <p className="control-path">Current file: {artifactPreviewPath}</p> : null}
          {artifactPreviewError ? <div className="error-banner">{artifactPreviewError}</div> : null}
          {artifactPreviewBody ? <pre className="control-log artifact-preview-log">{artifactPreviewBody}</pre> : null}
        </section>
      ) : null}
    </section>
  );
}

function ProcessSteps() {
  return (
    <section className="process-panel">
      <div className="panel-head">
        <p className="section-eyebrow">Process</p>
        <h2>How The Agent Improves Round By Round</h2>
      </div>
      <div className="process-grid">
        {PROCESS_STEPS.map((item) => (
          <article className="process-card" key={item.step}>
            <span className="process-step">{item.step}</span>
            <strong>{item.title}</strong>
            <p>{item.description}</p>
          </article>
        ))}
      </div>
    </section>
  );
}

function SessionBubble({ beat, isActive, onOpenDetails }: SessionBubbleProps) {
  const typedBody = useTypedText(beat.body, isActive);
  const isDetailWorthy = Object.keys(beat.payload).length > 0;

  return (
    <article className={`session-row session-${beat.tone} ${isActive ? "session-row-active" : ""}`} id={`beat-${beat.id}`}>
      <div className="session-bubble">
        <div className="session-bubble-head">
          <div>
            <p className="session-bubble-label">{beat.label}</p>
            <strong>{beat.title}</strong>
          </div>
          <div className="session-bubble-tags">
            {beat.roundIndex !== null ? <span className="meta-chip">Round {beat.roundIndex + 1}</span> : null}
            {beat.experimentId ? (
              <span className="meta-chip" translate="no">
                {beat.experimentId}
              </span>
            ) : null}
          </div>
        </div>
        <p className="session-bubble-body">
          {typedBody}
          {isActive ? <span className="typing-cursor" aria-hidden="true" /> : null}
        </p>
        {beat.meta.length > 0 ? (
          <div className="session-bubble-meta">
            {beat.meta.map((item) => (
              <span className="config-chip" key={`${beat.id}-${item}`}>
                {item}
              </span>
            ))}
          </div>
        ) : null}
        <div className="session-bubble-footer">
          <time>{formatTimestamp(beat.ts)}</time>
          {isDetailWorthy ? (
            <button className="detail-link" type="button" onClick={() => onOpenDetails(beat)}>
              Inspect Payload
            </button>
          ) : null}
        </div>
      </div>
    </article>
  );
}

function DetailModal({ beat, onClose }: DetailModalProps) {
  useEffect(() => {
    if (!beat) {
      return;
    }

    function onKeyDown(event: KeyboardEvent) {
      if (event.key === "Escape") {
        onClose();
      }
    }

    window.addEventListener("keydown", onKeyDown);
    return () => {
      window.removeEventListener("keydown", onKeyDown);
    };
  }, [beat, onClose]);

  if (!beat) {
    return null;
  }

  return (
    <div className="detail-modal-overlay" role="presentation" onClick={onClose}>
      <div className="detail-modal" role="dialog" aria-modal="true" aria-label="Inspect event payload" onClick={(event) => event.stopPropagation()}>
        <div className="detail-modal-head">
          <div>
            <p className="section-eyebrow">Event Detail</p>
            <h3>{beat.title}</h3>
          </div>
          <button className="detail-close" type="button" onClick={onClose}>
            ×
          </button>
        </div>
        <div className="detail-modal-grid">
          <div className="detail-card">
            <strong>Event metadata</strong>
            <p>Type: {beat.type}</p>
            <p>Timestamp: {formatTimestamp(beat.ts)}</p>
            <p>Round: {beat.roundIndex !== null ? `Round ${beat.roundIndex + 1}` : "N/A"}</p>
            <p>Experiment: {beat.experimentId ?? "N/A"}</p>
          </div>
          <div className="detail-card detail-card-code">
            <strong>Raw payload</strong>
            <pre>{JSON.stringify(beat.payload, null, 2)}</pre>
          </div>
        </div>
      </div>
    </div>
  );
}

function SessionViewer({ beats, currentBeat, rounds, totalBeats, visibleBeats, isPlaybackMode, isPlaying, onOpenDetails }: SessionViewerProps) {
  const activeRoundIndex = currentBeat?.roundIndex ?? (rounds.length > 0 ? rounds.length - 1 : null);
  const shownCount = visibleBeats <= 0 ? totalBeats : Math.min(visibleBeats, totalBeats);

  return (
    <section className="session-viewer" id="story-stage">
      <aside className="session-sidebar">
        <div className="session-sidebar-head">
          <p className="section-eyebrow">Session Navigator</p>
          <h2>Agent Session</h2>
          <p>Watch strategy proposals, tool calls, judge decisions, and reflections as one stream.</p>
        </div>
        <div className="session-sidebar-block">
          <strong>Progress</strong>
          <p>
            Showing {formatInteger(shownCount)} / {formatInteger(totalBeats)} beats
          </p>
        </div>
        <div className="session-sidebar-block">
          <strong>Rounds</strong>
          <div className="session-round-list">
            {rounds.length > 0 ? (
              rounds.map((round, index) => (
                <article className={`session-round-item ${activeRoundIndex === index ? "session-round-item-active" : ""}`} key={round.experimentId}>
                  <div className="session-round-row">
                    <span className={`status-pill status-${toStatusClass(round.status)}`}>{formatStatus(round.status)}</span>
                    <span className="session-round-index">Round {index + 1}</span>
                  </div>
                  <strong translate="no">{round.experimentId}</strong>
                  <p>{round.hypothesis}</p>
                </article>
              ))
            ) : (
              <div className="empty-state dark-empty">No round insights available yet.</div>
            )}
          </div>
        </div>
      </aside>

      <div className="session-main">
        <div className="session-topbar">
          <div>
            <span className="session-title">AutoSolver Session Playback</span>
            <p className="session-subtitle">
              {currentBeat ? `Current beat: ${currentBeat.title}` : "Showing full timeline. Scroll to inspect any event."}
            </p>
          </div>
          <div className="session-topbar-tags">
            <span className="meta-chip">{isPlaybackMode ? "Playback mode" : "Full mode"}</span>
            <span className="meta-chip">{isPlaying ? "Playing" : "Idle"}</span>
            {currentBeat?.experimentId ? (
              <span className="meta-chip" translate="no">
                {currentBeat.experimentId}
              </span>
            ) : null}
          </div>
        </div>

        <div className="session-chat" aria-live="polite">
          {beats.length > 0 ? (
            beats.map((beat) => (
              <SessionBubble key={beat.id} beat={beat} isActive={Boolean(isPlaying && currentBeat?.id === beat.id)} onOpenDetails={onOpenDetails} />
            ))
          ) : (
            <div className="empty-state dark-empty">No replay beats available.</div>
          )}
        </div>
      </div>
    </section>
  );
}

function ScoreChart({ rounds }: { rounds: RoundInsight[] }) {
  const points = buildChartPoints(rounds);
  if (points.length === 0) {
    return <div className="empty-state">Need at least one completed round to render chart.</div>;
  }

  const width = 760;
  const height = 280;
  const paddingX = 46;
  const paddingY = 34;
  const expectedValues = points.map((point) => point.expected);
  const costValues = points.map((point) => point.cost);
  const minExpected = Math.min(...expectedValues);
  const maxExpected = Math.max(...expectedValues);
  const minCost = Math.min(...costValues);
  const maxCost = Math.max(...costValues);

  function positionX(index: number): number {
    return paddingX + (index / Math.max(1, points.length - 1)) * (width - paddingX * 2);
  }

  function positionY(value: number, minValue: number, maxValue: number): number {
    const normalized = (value - minValue) / Math.max(0.001, maxValue - minValue || 1);
    return height - paddingY - normalized * (height - paddingY * 2);
  }

  const expectedPath = points
    .map((point, index) => `${index === 0 ? "M" : "L"} ${positionX(index)} ${positionY(point.expected, minExpected, maxExpected)}`)
    .join(" ");
  const costPath = points.map((point, index) => `${index === 0 ? "M" : "L"} ${positionX(index)} ${positionY(point.cost, minCost, maxCost)}`).join(" ");

  return (
    <div className="chart-shell">
      <div className="chart-legend">
        <span className="legend-chip legend-primary">Expected completed orders</span>
        <span className="legend-chip legend-secondary">Total cost</span>
      </div>
      <svg className="chart-svg" viewBox={`0 0 ${width} ${height}`} role="img" aria-label="Round trend chart">
        <rect className="chart-backdrop" width={width} height={height} rx="26" />
        <path className="chart-line chart-line-primary" d={expectedPath} />
        <path className="chart-line chart-line-secondary" d={costPath} />
        {points.map((point, index) => (
          <circle className="chart-dot chart-dot-primary" key={`expected-${index}`} cx={positionX(index)} cy={positionY(point.expected, minExpected, maxExpected)} r="5" />
        ))}
      </svg>
      <div className="chart-footer">
        <span>Best expected: {formatCount(Math.max(...expectedValues))}</span>
        <span>Lowest cost: {formatCount(Math.min(...costValues))}</span>
      </div>
    </div>
  );
}

function RoundCard({ round }: RoundCardProps) {
  const configChips = summarizeConfig(round.solverConfig);

  return (
    <article className="round-card">
      <div className="round-head">
        <div className="round-tags">
          <span className={`status-pill status-${toStatusClass(round.status)}`}>{formatStatus(round.status)}</span>
          <span className={`proposal-pill proposal-${toProposalClass(round.proposalType)}`}>{formatProposalType(round.proposalType)}</span>
        </div>
        <strong translate="no">{round.experimentId}</strong>
      </div>
      <p className="round-title">{round.hypothesis}</p>
      <div className="round-stats">
        <span>expected {formatCount(round.averageExpectedCompletedOrders)}</span>
        <span>cost {formatCount(round.averageTotalCost)}</span>
        <span>elapsed {formatInteger(round.totalElapsedMs)} ms</span>
      </div>
      {configChips.length > 0 ? (
        <div className="story-meta">
          {configChips.map((chip) => (
            <span className="config-chip" key={`${round.experimentId}-${chip}`}>
              {chip}
            </span>
          ))}
        </div>
      ) : null}
      {round.reflectionSummary ? <p className="round-copy">{round.reflectionSummary}</p> : null}
    </article>
  );
}

function CaseCard({ row }: CaseCardProps) {
  return (
    <article className="case-card">
      <div className="case-head">
        <strong translate="no">{row.caseId ?? row.instanceId ?? "unknown-case"}</strong>
        <span className="meta-chip">runs {formatInteger(row.runs)}</span>
      </div>
      <p className="case-copy">
        avg expected {formatCount(row.averageExpectedCompletedOrders)}, avg cost {formatCount(row.averageTotalCost)}, avg elapsed {formatInteger(row.averageElapsedMs)} ms.
      </p>
      <div className="story-meta">
        <span className="config-chip">candidate_pool {formatInteger(row.averageCandidateOptionCount)}</span>
        <span className="config-chip">bundle_pool {formatInteger(row.averageBundleOptionCount)}</span>
        {row.lastSolverName ? <span className="config-chip">{row.lastSolverName}</span> : null}
      </div>
    </article>
  );
}

function DebugDrawer({ events }: { events: ReplayEvent[] }) {
  return (
    <details className="debug-drawer">
      <summary>View raw event log</summary>
      <div className="raw-log-list">
        {events.map((event) => (
          <article className="raw-log-item" key={`${event.ts}-${event.type}`}>
            <div className="raw-log-head">
              <strong translate="no">{event.type}</strong>
              <time>{formatTimestamp(event.ts)}</time>
            </div>
            <pre>{JSON.stringify(event.payload, null, 2)}</pre>
          </article>
        ))}
      </div>
    </details>
  );
}

function App() {
  const [data, setData] = useState<ReplayData | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [sourceLabel, setSourceLabel] = useState(LIVE_REPLAY_SOURCE_LABEL);
  const [lastReloadedAt, setLastReloadedAt] = useState<string | null>(null);
  const [autoRefreshEnabled, setAutoRefreshEnabled] = useState(true);
  const [visibleBeatCount, setVisibleBeatCount] = useState(0);
  const [isPlaying, setIsPlaying] = useState(false);
  const [playbackSpeedMs, setPlaybackSpeedMs] = useState<number>(() => loadStoredPlaybackSpeed());
  const [detailBeat, setDetailBeat] = useState<StoryBeat | null>(null);
  const [hasAutoStarted, setHasAutoStarted] = useState(false);
  const [controlState, setControlState] = useState<ControlState | null>(null);
  const [controlError, setControlError] = useState<string | null>(null);
  const [controlNotice, setControlNotice] = useState<string | null>(null);
  const [isLaunching, setIsLaunching] = useState(false);
  const [artifactPreviewPath, setArtifactPreviewPath] = useState<string | null>(null);
  const [artifactPreviewBody, setArtifactPreviewBody] = useState<string | null>(null);
  const [artifactPreviewError, setArtifactPreviewError] = useState<string | null>(null);
  const [runDraft, setRunDraft] = useState<RunDraft>(() => loadStoredRunDraft());

  const commitReplay = useCallback((payload: ReplayData, label: string, resetPlayback: boolean, enableAutoRefresh: boolean) => {
    startTransition(() => {
      setData(payload);
      setError(null);
      setSourceLabel(label);
      setLastReloadedAt(new Date().toISOString());
      setAutoRefreshEnabled(enableAutoRefresh);
      if (resetPlayback) {
        setVisibleBeatCount(0);
        setIsPlaying(false);
        setHasAutoStarted(false);
      }
    });
  }, []);

  const loadLiveReplayWithFallback = useCallback(
    async (resetPlayback: boolean) => {
      try {
        const payload = await loadReplayPayload(LIVE_REPLAY_FILE);
        commitReplay(payload, LIVE_REPLAY_SOURCE_LABEL, resetPlayback, true);
      } catch (liveError) {
        try {
          const payload = await loadReplayPayload(DEMO_REPLAY_FILE);
          commitReplay(payload, DEMO_REPLAY_SOURCE_LABEL, resetPlayback, false);
          if (liveError instanceof Error) {
            console.warn(`Live replay unavailable, using demo: ${liveError.message}`);
          }
        } catch {
          throw liveError;
        }
      }
    },
    [commitReplay],
  );

  const refreshControlState = useCallback(
    async (silent: boolean) => {
      try {
        const snapshot = await loadControlSnapshot();
        startTransition(() => {
          setControlState(snapshot);
          setControlError(null);
          setRunDraft((current) =>
            sanitizeRunDraft({
              benchmarkPath: current.benchmarkPath || snapshot.defaults.benchmarkPath,
              instancePath: current.instancePath || snapshot.defaults.instancePath,
              searchSpacePath: current.searchSpacePath || snapshot.defaults.searchSpacePath,
              rounds: current.rounds || snapshot.defaults.rounds,
              timeBudgetMs: current.timeBudgetMs || snapshot.defaults.timeBudgetMs,
              seed: current.seed ?? snapshot.defaults.seed,
              allowRuleBasedFallback: current.allowRuleBasedFallback,
            }),
          );
        });
      } catch (loadError) {
        if (!silent) {
          setControlError(errorToMessage(loadError, "Failed to fetch control status."));
        }
      }
    },
    [],
  );

  useEffect(() => {
    try {
      window.localStorage.setItem(RUN_DRAFT_STORAGE_KEY, JSON.stringify(runDraft));
    } catch {
      // Ignore storage failures in restricted environments.
    }
  }, [runDraft]);

  useEffect(() => {
    try {
      window.localStorage.setItem(PLAYBACK_SPEED_STORAGE_KEY, String(playbackSpeedMs));
    } catch {
      // Ignore storage failures in restricted environments.
    }
  }, [playbackSpeedMs]);

  useEffect(() => {
    let cancelled = false;
    async function bootstrap() {
      try {
        await loadLiveReplayWithFallback(true);
      } catch (loadError) {
        if (!cancelled) {
          setError(errorToMessage(loadError, "Failed to load replay payload."));
        }
      }
      if (!cancelled) {
        await refreshControlState(false);
      }
    }
    void bootstrap();
    return () => {
      cancelled = true;
    };
  }, [loadLiveReplayWithFallback, refreshControlState]);

  useEffect(() => {
    if (!autoRefreshEnabled || !isLiveReplaySource(sourceLabel)) {
      return;
    }
    const intervalId = window.setInterval(() => {
      void loadLiveReplayWithFallback(false).catch(() => {
        // Keep silent for polling refresh.
      });
    }, 2000);
    return () => {
      window.clearInterval(intervalId);
    };
  }, [autoRefreshEnabled, loadLiveReplayWithFallback, sourceLabel]);

  const controlPollingMs = useMemo(() => {
    const isBusy = Boolean(controlState?.currentJob) || (controlState?.queuedJobs.length ?? 0) > 0;
    return isBusy ? 1500 : 5000;
  }, [controlState]);

  useEffect(() => {
    const intervalId = window.setInterval(() => {
      void refreshControlState(true);
    }, controlPollingMs);
    return () => {
      window.clearInterval(intervalId);
    };
  }, [controlPollingMs, refreshControlState]);

  const events = data?.events ?? [];
  const roundInsights = data?.roundInsights ?? [];
  const caseLeaderboard = data?.caseLeaderboard ?? [];
  const summary = data?.summary;
  const agent: ReplayAgentSummary | undefined = data?.agent;
  const storyBeats = useMemo(() => buildStoryBeats(events), [events]);
  const totalBeats = storyBeats.length;
  const isPlaybackFinished = totalBeats > 0 && visibleBeatCount >= totalBeats && !isPlaying;
  const isPartialPlayback = visibleBeatCount > 0 && visibleBeatCount < totalBeats;
  const isPlaybackMode = isPartialPlayback;
  const shownBeats = isPartialPlayback ? storyBeats.slice(0, visibleBeatCount) : storyBeats;
  const completedRoundCount = shownBeats.filter((beat) => beat.type === EVENT_TYPES.RESEARCH_ROUND_COMPLETED || beat.type === EVENT_TYPES.RESEARCH_ROUND_FAILED).length;
  const shownRounds = isPartialPlayback ? roundInsights.slice(0, completedRoundCount) : roundInsights;
  const currentBeat = isPartialPlayback ? shownBeats.at(-1) ?? null : null;
  const visibleKeepCount = shownRounds.filter((round) => round.status === "keep").length;
  const visibleDiscardCount = shownRounds.filter((round) => round.status === "discard").length;
  const visibleFailureCount = shownRounds.filter((round) => round.status === "crash").length;
  const bestRound = selectBestRound(shownRounds.length > 0 ? shownRounds : roundInsights);

  useEffect(() => {
    if (hasAutoStarted || totalBeats === 0) {
      return;
    }
    const timerId = window.setTimeout(() => {
      setVisibleBeatCount(1);
      setIsPlaying(true);
      setHasAutoStarted(true);
    }, 700);
    return () => {
      window.clearTimeout(timerId);
    };
  }, [hasAutoStarted, totalBeats]);

  useEffect(() => {
    if (!isPlaying || totalBeats === 0) {
      return;
    }
    const timerId = window.setTimeout(() => {
      setVisibleBeatCount((current) => {
        const nextValue = current <= 0 ? 1 : current + 1;
        if (nextValue >= totalBeats) {
          setIsPlaying(false);
          return totalBeats;
        }
        return nextValue;
      });
    }, playbackSpeedMs);

    return () => {
      window.clearTimeout(timerId);
    };
  }, [isPlaying, playbackSpeedMs, totalBeats, visibleBeatCount]);

  async function handleLoadLocalReplay(event: ChangeEvent<HTMLInputElement>) {
    const file = event.target.files?.[0];
    if (!file) {
      return;
    }
    try {
      const text = await file.text();
      const payload: unknown = JSON.parse(text);
      if (!isReplayData(payload)) {
        throw new Error("Selected file is not a valid replay JSON payload.");
      }
      commitReplay(payload, file.name, true, false);
    } catch (loadError) {
      setError(errorToMessage(loadError, "Failed to load local replay JSON."));
    } finally {
      event.target.value = "";
    }
  }

  async function handleUseDemoReplay() {
    try {
      const payload = await loadReplayPayload(DEMO_REPLAY_FILE);
      commitReplay(payload, DEMO_REPLAY_SOURCE_LABEL, true, false);
    } catch (loadError) {
      setError(errorToMessage(loadError, "Failed to load demo replay."));
    }
  }

  function handleStartPlayback() {
    if (totalBeats === 0) {
      return;
    }
    setVisibleBeatCount(1);
    setIsPlaying(true);
    setHasAutoStarted(true);
  }

  function handleTogglePlayback() {
    if (totalBeats === 0) {
      return;
    }
    if (visibleBeatCount === 0) {
      setVisibleBeatCount(1);
      setIsPlaying(true);
      setHasAutoStarted(true);
      return;
    }
    setIsPlaying((current) => !current);
  }

  function handleResetPlayback() {
    setVisibleBeatCount(0);
    setIsPlaying(false);
    setHasAutoStarted(true);
  }

  function handleShowAll() {
    setVisibleBeatCount(0);
    setIsPlaying(false);
    setHasAutoStarted(true);
  }

  function handleSpeedChange(event: ChangeEvent<HTMLSelectElement>) {
    const nextValue = Number(event.target.value);
    setPlaybackSpeedMs(Number.isFinite(nextValue) ? nextValue : 1200);
  }

  function handleOpenDetails(beat: StoryBeat) {
    setDetailBeat(beat);
  }

  function handleCloseDetails() {
    setDetailBeat(null);
  }

  function handleDraftChange(patch: Partial<RunDraft>) {
    setRunDraft((current) => sanitizeRunDraft({ ...current, ...patch }));
  }

  function handleApplyPreset(preset: RunPreset) {
    setRunDraft((current) => sanitizeRunDraft({ ...current, ...preset.patch }));
    setControlNotice(`Preset applied: ${preset.label}`);
    setControlError(null);
  }

  async function handleUploadFile(target: UploadTarget, event: ChangeEvent<HTMLInputElement>) {
    const file = event.target.files?.[0];
    if (!file) {
      return;
    }
    try {
      const uploadedPath = await uploadControlFile(target, file);
      const patch: Partial<RunDraft> =
        target === "benchmark"
          ? { benchmarkPath: uploadedPath }
          : target === "instance"
            ? { instancePath: uploadedPath }
            : { searchSpacePath: uploadedPath };
      startTransition(() => {
        setRunDraft((current) => sanitizeRunDraft({ ...current, ...patch }));
        setControlError(null);
        setControlNotice(`Uploaded ${file.name} to ${uploadedPath}`);
      });
    } catch (uploadError) {
      setControlNotice(null);
      setControlError(errorToMessage(uploadError, "Failed to upload file."));
    } finally {
      event.target.value = "";
    }
  }

  async function handleRun(kind: ControlRunKind) {
    setIsLaunching(true);
    try {
      const snapshot = await runControlJob(kind, runDraft);
      startTransition(() => {
        setControlState(snapshot);
        setControlError(null);
        setControlNotice(`${formatRunKind(kind)} submitted.`);
      });
      if (kind === "research" || kind === "smoke") {
        await loadLiveReplayWithFallback(true);
      }
    } catch (launchError) {
      setControlNotice(null);
      setControlError(errorToMessage(launchError, "Failed to launch web-control job."));
    } finally {
      setIsLaunching(false);
    }
  }

  async function handleCancel(job: ControlJob) {
    try {
      const snapshot = await cancelControlJob(job.jobId);
      startTransition(() => {
        setControlState(snapshot);
        setControlError(null);
        setControlNotice(job.status === CONTROL_JOB_STATUS.QUEUED ? `Queue item cancelled: ${job.jobId}` : `Cancel requested: ${job.jobId}`);
      });
    } catch (cancelError) {
      setControlNotice(null);
      setControlError(errorToMessage(cancelError, "Failed to cancel job."));
    }
  }

  async function handleInspectArtifact(path: string) {
    try {
      const loaded = await loadControlArtifactText(path);
      let previewBody = loaded.body;
      if (loaded.contentType.includes("json")) {
        const parsed: unknown = JSON.parse(loaded.body);
        previewBody = JSON.stringify(parsed, null, 2);
      }
      startTransition(() => {
        setArtifactPreviewPath(path);
        setArtifactPreviewBody(previewBody);
        setArtifactPreviewError(null);
      });
    } catch (artifactError) {
      startTransition(() => {
        setArtifactPreviewPath(path);
        setArtifactPreviewBody(null);
        setArtifactPreviewError(errorToMessage(artifactError, "Failed to preview artifact."));
      });
    }
  }

  async function handleLoadReplayArtifact(path: string) {
    try {
      const loaded = await loadControlArtifactText(path);
      const payload: unknown = JSON.parse(loaded.body);
      if (!isReplayData(payload)) {
        throw new Error("Selected artifact is not a valid replay JSON payload.");
      }
      commitReplay(payload, path, true, false);
      startTransition(() => {
        setArtifactPreviewPath(path);
        setArtifactPreviewBody(JSON.stringify(payload, null, 2));
        setArtifactPreviewError(null);
      });
    } catch (artifactError) {
      startTransition(() => {
        setArtifactPreviewPath(path);
        setArtifactPreviewBody(null);
        setArtifactPreviewError(errorToMessage(artifactError, "Failed to load replay artifact."));
      });
    }
  }

  return (
    <main className="page-shell" id="main-content">
      <a className="skip-link" href="#story-stage">
        Skip To Session Stage
      </a>

      <section className="hero">
        <div className="hero-copy">
          <p className="section-eyebrow">AutoSolver Dashboard</p>
          <h1>Make Agent Reasoning And Solver Evidence Visible In One Place</h1>
          <p className="hero-text">
            This view turns research logs into a readable timeline. Teammates can see what was tried, what succeeded, and how the next iteration was chosen.
          </p>
          <div className="hero-chips">
            <span className="meta-chip">Benchmark {summary?.benchmarkId ?? agent?.benchmarkId ?? "N/A"}</span>
            <span className="meta-chip">Provider {agent?.provider ?? "N/A"}</span>
            <span className="meta-chip">{agent?.llmEnabled ? "LLM mode" : "Fallback mode"}</span>
            <span className="meta-chip">{autoRefreshEnabled ? "Live refresh enabled" : "Static snapshot"}</span>
          </div>
        </div>
        <div className="hero-metrics">
          <MetricCard label="Story Beats" value={formatInteger(totalBeats)} detail="Total timeline messages in this replay." tone="accent" />
          <MetricCard label="Kept Rounds" value={formatInteger(visibleKeepCount)} detail="Rounds marked as improvements." />
          <MetricCard label="Discarded Rounds" value={formatInteger(visibleDiscardCount)} detail="Rounds rejected by objective comparison." />
          <MetricCard label="Failed Rounds" value={formatInteger(visibleFailureCount)} detail="Rounds that ended with execution errors." tone="quiet" />
          <MetricCard
            label="Best Expected Orders"
            value={formatCount(bestRound?.averageExpectedCompletedOrders ?? summary?.bestExpectedCompletedOrders)}
            detail="Best value found in visible rounds."
          />
          <MetricCard
            label="Cost At Best"
            value={formatCount(bestRound?.averageTotalCost ?? summary?.bestTotalCost)}
            detail="Companion cost for the best expected-orders result."
          />
        </div>
      </section>

      <ControlBar
        sourceLabel={sourceLabel}
        benchmarkId={summary?.benchmarkId ?? agent?.benchmarkId ?? null}
        provider={agent?.provider ?? "N/A"}
        autoRefreshEnabled={autoRefreshEnabled}
        lastReloadedAt={lastReloadedAt}
        onLoadLocalReplay={handleLoadLocalReplay}
        onUseDemoReplay={() => {
          void handleUseDemoReplay();
        }}
        onReloadLiveReplay={() => {
          void loadLiveReplayWithFallback(true).catch((loadError) => {
            setError(errorToMessage(loadError, "Failed to load live replay."));
          });
        }}
        onToggleAutoRefresh={() => {
          setAutoRefreshEnabled((current) => !current);
        }}
      />

      <ControlConsole
        controlState={controlState}
        controlError={controlError}
        controlNotice={controlNotice}
        isLaunching={isLaunching}
        draft={runDraft}
        presets={RUN_PRESETS}
        artifactPreviewPath={artifactPreviewPath}
        artifactPreviewBody={artifactPreviewBody}
        artifactPreviewError={artifactPreviewError}
        onDraftChange={handleDraftChange}
        onApplyPreset={handleApplyPreset}
        onUploadFile={handleUploadFile}
        onRun={handleRun}
        onCancel={handleCancel}
        onRefresh={() => {
          void refreshControlState(false);
        }}
        onInspectArtifact={handleInspectArtifact}
        onLoadReplayArtifact={handleLoadReplayArtifact}
      />

      <ProcessSteps />

      <PlaybackControls
        totalBeats={totalBeats}
        visibleBeats={visibleBeatCount}
        isPlaying={isPlaying}
        isFinished={isPlaybackFinished}
        speedMs={playbackSpeedMs}
        onStartPlayback={handleStartPlayback}
        onTogglePlayback={handleTogglePlayback}
        onResetPlayback={handleResetPlayback}
        onShowAll={handleShowAll}
        onSpeedChange={handleSpeedChange}
      />

      {error ? (
        <div className="error-banner" role="status" aria-live="polite">
          {error}
        </div>
      ) : null}

      <SessionViewer
        beats={shownBeats}
        currentBeat={currentBeat}
        rounds={shownRounds}
        totalBeats={totalBeats}
        visibleBeats={visibleBeatCount}
        isPlaybackMode={isPlaybackMode}
        isPlaying={isPlaying}
        onOpenDetails={handleOpenDetails}
      />

      <section className="panel-grid">
        <section className="panel">
          <div className="panel-head">
            <p className="section-eyebrow">Trend</p>
            <h2>How Round Outcomes Move Over Time</h2>
          </div>
          <ScoreChart rounds={shownRounds} />
        </section>

        <section className="panel">
          <div className="panel-head">
            <p className="section-eyebrow">Round Summary</p>
            <h2>What Changed In Each Iteration</h2>
          </div>
          <div className="round-grid">
            {shownRounds.length > 0 ? (
              shownRounds.map((round) => <RoundCard key={round.experimentId} round={round} />)
            ) : (
              <div className="empty-state">No round summaries available for current replay.</div>
            )}
          </div>
        </section>
      </section>

      <section className="panel">
        <div className="panel-head">
          <p className="section-eyebrow">Case View</p>
          <h2>Which Benchmark Cases Are Hardest</h2>
        </div>
        <div className="case-grid">
          {caseLeaderboard.length > 0 ? (
            caseLeaderboard.slice(0, 6).map((row, index) => <CaseCard key={row.caseId ?? row.instanceId ?? `case-${index}`} row={row} />)
          ) : (
            <div className="empty-state">No case leaderboard data in current replay.</div>
          )}
        </div>
      </section>

      <section className="panel">
        <div className="panel-head">
          <p className="section-eyebrow">Raw Log</p>
          <h2>Full Event Payloads For Debugging</h2>
        </div>
        <DebugDrawer events={events} />
      </section>

      <DetailModal beat={detailBeat} onClose={handleCloseDetails} />
    </main>
  );
}

export default App;

