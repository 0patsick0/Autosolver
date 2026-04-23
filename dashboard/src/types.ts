export const EVENT_TYPES = {
  SOLVE_STARTED: "solve.started",
  SOLVE_COMPLETED: "solve.completed",
  BENCHMARK_CASE_COMPLETED: "benchmark.case_completed",
  BENCHMARK_COMPLETED: "benchmark.completed",
  RESEARCH_SESSION_STARTED: "research.session_started",
  RESEARCH_SESSION_RESUMED: "research.session_resumed",
  RESEARCH_LLM_PROPOSAL: "research.llm_proposal",
  RESEARCH_FALLBACK_PROPOSAL: "research.fallback_proposal",
  RESEARCH_LLM_REFLECTION: "research.llm_reflection",
  RESEARCH_HEURISTIC_REFLECTION: "research.heuristic_reflection",
  RESEARCH_ROUND_STARTED: "research.round_started",
  RESEARCH_ROUND_COMPLETED: "research.round_completed",
  RESEARCH_ROUND_FAILED: "research.round_failed",
  RESEARCH_INCUMBENT_UPDATED: "research.incumbent_updated",
} as const;

export type EventType = (typeof EVENT_TYPES)[keyof typeof EVENT_TYPES];

export interface ReplayEventPayload {
  average_expected_completed_orders?: number;
  average_total_cost?: number;
  benchmark_id?: string;
  case_count?: number;
  case_id?: string;
  config_source?: string | null;
  elapsed_ms?: number;
  error?: string;
  experiment_id?: string;
  expected_completed_orders?: number;
  fallback_allowed?: boolean;
  benchmark_profile?: Record<string, unknown>;
  hypothesis?: string;
  instance_id?: string;
  keep_reason?: string;
  llm_enabled?: boolean;
  next_focus?: string[];
  notes?: string;
  provider?: string;
  risks?: string[];
  round_index?: number;
  solver_config?: Record<string, unknown>;
  solver_name?: string;
  source_path?: string;
  state_path?: string;
  stats?: Record<string, unknown>;
  status?: string;
  summary?: string;
  time_budget_ms?: number;
  total_cost?: number;
  total_elapsed_ms?: number;
  total_weight?: number;
  weight?: number;
}

export interface ReplayEvent {
  ts: string;
  type: string;
  payload: ReplayEventPayload;
}

export interface ReplayRound {
  experiment_id: string;
  hypothesis: string;
  events: ReplayEvent[];
}

export interface ChartPoint {
  ts: string;
  expectedCompletedOrders: number | null;
  totalCost: number | null;
  type: string;
}

export interface ReplaySummary {
  benchmarkId: string | null;
  roundCount: number;
  eventCount: number;
  keepCount: number;
  discardCount: number;
  failureCount: number;
  latestIncumbentExperimentId: string | null;
  bestExpectedCompletedOrders: number | null;
  bestTotalCost: number | null;
}

export interface ReplayAgentSummary {
  provider: string;
  llmEnabled: boolean;
  fallbackAllowed: boolean;
  benchmarkId: string | null;
  sessionStartedAt: string | null;
  proposalBreakdown: {
    llm: number;
    fallback: number;
  };
}

export interface ReplayCaseMetric {
  caseId?: string;
  instanceId?: string;
  expectedCompletedOrders?: number;
  totalCost?: number;
  elapsedMs?: number;
  solverName?: string;
  status?: string;
  weight?: number;
  candidateOptionCount?: number;
  candidateOptionBreakdown?: Record<string, unknown>;
}

export interface RoundInsight {
  experimentId: string;
  hypothesis: string;
  status: string;
  proposalType: string;
  averageExpectedCompletedOrders: number | null;
  averageTotalCost: number | null;
  totalElapsedMs: number | null;
  averageCandidateOptionCount?: number;
  averageBundleOptionCount?: number;
  reflectionSummary: string | null;
  keepReason: string | null;
  risks: string[];
  nextFocus: string[];
  avoidPatterns: string[];
  solverConfig: Record<string, unknown> | null;
  caseMetrics: ReplayCaseMetric[];
}

export interface CaseLeaderboardEntry {
  caseId?: string;
  instanceId?: string;
  sourcePath?: string;
  runs: number;
  averageExpectedCompletedOrders: number;
  averageTotalCost: number;
  averageElapsedMs: number;
  averageCandidateOptionCount?: number;
  averageBundleOptionCount?: number;
  lastSolverName?: string;
  lastStatus?: string;
}

export interface ReplayData {
  events: ReplayEvent[];
  rounds: ReplayRound[];
  chartPoints: ChartPoint[];
  summary: ReplaySummary;
  roundInsights?: RoundInsight[];
  caseLeaderboard?: CaseLeaderboardEntry[];
  agent?: ReplayAgentSummary;
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null;
}

function isReplayEventPayload(value: unknown): value is ReplayEventPayload {
  return isRecord(value);
}

function isReplayEvent(value: unknown): value is ReplayEvent {
  return isRecord(value) && typeof value.ts === "string" && typeof value.type === "string" && isReplayEventPayload(value.payload);
}

function isReplayRound(value: unknown): value is ReplayRound {
  return (
    isRecord(value) &&
    typeof value.experiment_id === "string" &&
    typeof value.hypothesis === "string" &&
    Array.isArray(value.events) &&
    value.events.every(isReplayEvent)
  );
}

function isChartPoint(value: unknown): value is ChartPoint {
  return (
    isRecord(value) &&
    typeof value.ts === "string" &&
    typeof value.type === "string" &&
    (typeof value.expectedCompletedOrders === "number" || value.expectedCompletedOrders === null) &&
    (typeof value.totalCost === "number" || value.totalCost === null)
  );
}

function isReplaySummary(value: unknown): value is ReplaySummary {
  return (
    isRecord(value) &&
    (typeof value.benchmarkId === "string" || value.benchmarkId === null) &&
    typeof value.roundCount === "number" &&
    typeof value.eventCount === "number" &&
    typeof value.keepCount === "number" &&
    typeof value.discardCount === "number" &&
    typeof value.failureCount === "number" &&
    (typeof value.latestIncumbentExperimentId === "string" || value.latestIncumbentExperimentId === null) &&
    (typeof value.bestExpectedCompletedOrders === "number" || value.bestExpectedCompletedOrders === null) &&
    (typeof value.bestTotalCost === "number" || value.bestTotalCost === null)
  );
}

function isReplayCaseMetric(value: unknown): value is ReplayCaseMetric {
  return isRecord(value);
}

function isRoundInsight(value: unknown): value is RoundInsight {
  return (
    isRecord(value) &&
    typeof value.experimentId === "string" &&
    typeof value.hypothesis === "string" &&
    typeof value.status === "string" &&
    typeof value.proposalType === "string" &&
    (typeof value.averageExpectedCompletedOrders === "number" || value.averageExpectedCompletedOrders === null) &&
    (typeof value.averageTotalCost === "number" || value.averageTotalCost === null) &&
    (typeof value.totalElapsedMs === "number" || value.totalElapsedMs === null) &&
    (typeof value.averageCandidateOptionCount === "number" || value.averageCandidateOptionCount === undefined) &&
    (typeof value.averageBundleOptionCount === "number" || value.averageBundleOptionCount === undefined) &&
    (typeof value.reflectionSummary === "string" || value.reflectionSummary === null) &&
    (typeof value.keepReason === "string" || value.keepReason === null) &&
    Array.isArray(value.risks) &&
    Array.isArray(value.nextFocus) &&
    Array.isArray(value.avoidPatterns) &&
    (isRecord(value.solverConfig) || value.solverConfig === null) &&
    Array.isArray(value.caseMetrics) &&
    value.caseMetrics.every(isReplayCaseMetric)
  );
}

function isCaseLeaderboardEntry(value: unknown): value is CaseLeaderboardEntry {
  return (
    isRecord(value) &&
    typeof value.runs === "number" &&
    typeof value.averageExpectedCompletedOrders === "number" &&
    typeof value.averageTotalCost === "number" &&
    typeof value.averageElapsedMs === "number" &&
    (typeof value.averageCandidateOptionCount === "number" || value.averageCandidateOptionCount === undefined) &&
    (typeof value.averageBundleOptionCount === "number" || value.averageBundleOptionCount === undefined)
  );
}

function isReplayAgentSummary(value: unknown): value is ReplayAgentSummary {
  return (
    isRecord(value) &&
    typeof value.provider === "string" &&
    typeof value.llmEnabled === "boolean" &&
    typeof value.fallbackAllowed === "boolean" &&
    (typeof value.benchmarkId === "string" || value.benchmarkId === null) &&
    (typeof value.sessionStartedAt === "string" || value.sessionStartedAt === null) &&
    isRecord(value.proposalBreakdown) &&
    typeof value.proposalBreakdown.llm === "number" &&
    typeof value.proposalBreakdown.fallback === "number"
  );
}

export function isReplayData(value: unknown): value is ReplayData {
  return (
    isRecord(value) &&
    Array.isArray(value.events) &&
    value.events.every(isReplayEvent) &&
    Array.isArray(value.rounds) &&
    value.rounds.every(isReplayRound) &&
    Array.isArray(value.chartPoints) &&
    value.chartPoints.every(isChartPoint) &&
    isReplaySummary(value.summary) &&
    (value.roundInsights === undefined || (Array.isArray(value.roundInsights) && value.roundInsights.every(isRoundInsight))) &&
    (value.caseLeaderboard === undefined || (Array.isArray(value.caseLeaderboard) && value.caseLeaderboard.every(isCaseLeaderboardEntry))) &&
    (value.agent === undefined || isReplayAgentSummary(value.agent))
  );
}
