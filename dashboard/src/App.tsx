import { startTransition, useEffect, useEffectEvent, useState, type ChangeEvent } from "react";
import {
  EVENT_TYPES,
  isReplayData,
  type CaseLeaderboardEntry,
  type ChartPoint,
  type ReplayAgentSummary,
  type ReplayData,
  type ReplayEvent,
  type RoundInsight,
} from "./types";
import "./styles.css";

interface MetricCardProps {
  eyebrow: string;
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
  onUseBundledReplay: () => void;
}

interface EventListProps {
  events: ReplayEvent[];
}

interface RoundCardProps {
  round: RoundInsight;
}

const countFormatter = new Intl.NumberFormat(undefined, {
  maximumFractionDigits: 2,
});

const integerFormatter = new Intl.NumberFormat(undefined, {
  maximumFractionDigits: 0,
});

const timeFormatter = new Intl.DateTimeFormat(undefined, {
  dateStyle: "medium",
  timeStyle: "short",
});

function formatCount(value: number | null | undefined): string {
  return typeof value === "number" ? countFormatter.format(value) : "n/a";
}

function formatInteger(value: number | null | undefined): string {
  return typeof value === "number" ? integerFormatter.format(value) : "n/a";
}

function formatProposalType(value: string): string {
  if (value === "llm") {
    return "LLM Proposal";
  }
  if (value === "fallback") {
    return "Fallback Proposal";
  }
  return "Unknown Proposal";
}

function formatStatus(value: string): string {
  return value ? value.replace("_", " ") : "unknown";
}

function formatTimestamp(value: string | null | undefined): string {
  if (!value) {
    return "n/a";
  }
  const parsed = new Date(value);
  return Number.isNaN(parsed.getTime()) ? value : timeFormatter.format(parsed);
}

function summarizeConfig(config: Record<string, unknown> | null): string[] {
  if (!config) {
    return [];
  }

  return [
    `top-k ${String(config.top_k_riders_per_order ?? "n/a")}`,
    `CP-SAT ${config.use_cpsat === false ? "off" : "on"}`,
    `bundles ${config.generate_bundles_if_missing === false ? "off" : "on"}`,
    `LNS ${String(config.lns_iterations ?? "n/a")} iters`,
    `radius ${String(config.bundle_distance_threshold ?? "n/a")}`,
  ];
}

function isBundledReplaySource(sourceLabel: string): boolean {
  return sourceLabel === "Bundled replay-data.json";
}

function MetricCard({ eyebrow, value, detail, tone = "default" }: MetricCardProps) {
  return (
    <article className={`metric-card metric-${tone}`}>
      <p className="metric-eyebrow">{eyebrow}</p>
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
  onUseBundledReplay,
}: ControlBarProps) {
  return (
    <section className="control-bar">
      <div className="control-copy">
        <p className="control-label">Replay Source</p>
        <strong translate="no">{sourceLabel}</strong>
        <p className="control-hint">
          Benchmark: <span translate="no">{benchmarkId ?? "n/a"}</span> · Provider: <span translate="no">{provider}</span>
        </p>
        <div className="live-status-row">
          <span className={`live-indicator ${autoRefreshEnabled ? "live-indicator-active" : "live-indicator-paused"}`}>
            {autoRefreshEnabled ? "Auto-refreshing" : "Static replay"}
          </span>
          <span className="live-timestamp">Last sync: {formatTimestamp(lastReloadedAt)}</span>
        </div>
      </div>
      <div className="control-actions">
        <button className="secondary-button" type="button" onClick={onUseBundledReplay}>
          Use Live Feed
        </button>
        <label className="upload-button">
          Load Local Replay JSON
          <input className="upload-input" type="file" accept="application/json,.json" aria-label="Load local replay JSON" onChange={onLoadLocalReplay} />
        </label>
      </div>
    </section>
  );
}

function Chart({ points }: { points: ChartPoint[] }) {
  if (points.length === 0) {
    return <div className="empty-state">Run `autosolver replay ...` to populate the score curve.</div>;
  }

  const expectedSeries = points.filter((point) => typeof point.expectedCompletedOrders === "number");
  const costSeries = points.filter((point) => typeof point.totalCost === "number");
  if (expectedSeries.length === 0) {
    return <div className="empty-state">Replay data does not yet include chartable score points.</div>;
  }

  const width = 760;
  const height = 280;
  const paddingX = 44;
  const paddingY = 30;
  const expectedValues = expectedSeries.map((point) => point.expectedCompletedOrders ?? 0);
  const costValues = costSeries.map((point) => point.totalCost ?? 0);
  const minExpected = Math.min(...expectedValues);
  const maxExpected = Math.max(...expectedValues);
  const minCost = Math.min(...costValues, 0);
  const maxCost = Math.max(...costValues, 1);
  const gridLines = 4;

  function buildPath(series: ChartPoint[], minValue: number, maxValue: number, valueKey: "expectedCompletedOrders" | "totalCost") {
    return series
      .map((point, index) => {
        const x = paddingX + (index / Math.max(1, series.length - 1)) * (width - paddingX * 2);
        const normalized = ((point[valueKey] ?? 0) - minValue) / Math.max(0.001, maxValue - minValue || 1);
        const y = height - paddingY - normalized * (height - paddingY * 2);
        return `${index === 0 ? "M" : "L"} ${x} ${y}`;
      })
      .join(" ");
  }

  const expectedPath = buildPath(expectedSeries, minExpected, maxExpected, "expectedCompletedOrders");
  const costPath = buildPath(costSeries, minCost, maxCost, "totalCost");

  return (
    <div className="chart-shell">
      <div className="chart-legend">
        <span className="legend-chip legend-primary">Expected Completed Orders</span>
        <span className="legend-chip legend-secondary">Total Cost</span>
      </div>
      <svg className="chart-svg" viewBox={`0 0 ${width} ${height}`} role="img" aria-label="Replay score history">
        <rect x="0" y="0" width={width} height={height} rx="24" className="chart-backdrop" />
        {Array.from({ length: gridLines }, (_, index) => {
          const y = paddingY + (index / Math.max(1, gridLines - 1)) * (height - paddingY * 2);
          return <line key={index} x1={paddingX} x2={width - paddingX} y1={y} y2={y} className="chart-grid-line" />;
        })}
        <path d={expectedPath} className="chart-line chart-line-primary" />
        <path d={costPath} className="chart-line chart-line-secondary" />
        {expectedSeries.map((point, index) => {
          const x = paddingX + (index / Math.max(1, expectedSeries.length - 1)) * (width - paddingX * 2);
          const normalized = ((point.expectedCompletedOrders ?? 0) - minExpected) / Math.max(0.001, maxExpected - minExpected || 1);
          const y = height - paddingY - normalized * (height - paddingY * 2);
          return <circle key={`${point.ts}-${point.type}-expected`} cx={x} cy={y} r="5" className="chart-dot chart-dot-primary" />;
        })}
      </svg>
      <div className="chart-footer">
        <span>First point: {formatCount(expectedSeries[0]?.expectedCompletedOrders)}</span>
        <span>Best point: {formatCount(Math.max(...expectedValues))}</span>
      </div>
    </div>
  );
}

function EventList({ events }: EventListProps) {
  if (events.length === 0) {
    return <div className="empty-state">No events yet.</div>;
  }

  return (
    <div className="event-list">
      {events.map((event) => (
        <article className="event-item" key={`${event.ts}-${event.type}`}>
          <div className="event-head">
            <span className="event-type" translate="no">
              {event.type}
            </span>
            <time className="event-time">{formatTimestamp(event.ts)}</time>
          </div>
          <pre className="event-payload">{JSON.stringify(event.payload, null, 2)}</pre>
        </article>
      ))}
    </div>
  );
}

function RoundCard({ round }: RoundCardProps) {
  const configChips = summarizeConfig(round.solverConfig);

  return (
    <article className="round-card">
      <div className="round-card-head">
        <div className="round-title">
          <span className={`status-pill status-${round.status}`}>{formatStatus(round.status)}</span>
          <span className={`proposal-pill proposal-${round.proposalType}`}>{formatProposalType(round.proposalType)}</span>
        </div>
        <strong translate="no">{round.experimentId}</strong>
      </div>
      <p className="round-hypothesis">{round.hypothesis}</p>
      <div className="round-stats">
        <span>Expected: {formatCount(round.averageExpectedCompletedOrders)}</span>
        <span>Cost: {formatCount(round.averageTotalCost)}</span>
        <span>Elapsed: {formatInteger(round.totalElapsedMs)} ms</span>
        {typeof round.averageCandidateOptionCount === "number" ? <span>Pool: {formatInteger(round.averageCandidateOptionCount)}</span> : null}
        {typeof round.averageBundleOptionCount === "number" ? <span>Bundles: {formatInteger(round.averageBundleOptionCount)}</span> : null}
      </div>
      {configChips.length > 0 ? (
        <div className="config-chip-row">
          {configChips.map((chip) => (
            <span className="config-chip" key={chip}>
              {chip}
            </span>
          ))}
        </div>
      ) : null}
      {round.reflectionSummary ? <p className="round-reflection">{round.reflectionSummary}</p> : null}
      {round.keepReason ? <p className="round-keep-reason">{round.keepReason}</p> : null}
      {round.nextFocus.length > 0 ? (
        <div className="list-block">
          <p className="list-label">Next Focus</p>
          <ul>
            {round.nextFocus.slice(0, 3).map((item) => (
              <li key={item}>{item}</li>
            ))}
          </ul>
        </div>
      ) : null}
      {round.risks.length > 0 ? (
        <div className="list-block">
          <p className="list-label">Risks</p>
          <ul>
            {round.risks.slice(0, 2).map((item) => (
              <li key={item}>{item}</li>
            ))}
          </ul>
        </div>
      ) : null}
    </article>
  );
}

function CaseLeaderboard({ rows }: { rows: CaseLeaderboardEntry[] }) {
  if (rows.length === 0) {
    return <div className="empty-state">No benchmark case metrics were found in the replay.</div>;
  }

  return (
    <div className="table-shell">
      <table className="case-table">
        <caption>Hardest benchmark cases ranked by average expected completion.</caption>
        <thead>
          <tr>
            <th scope="col">Case</th>
            <th scope="col">Avg Expected</th>
            <th scope="col">Avg Cost</th>
            <th scope="col">Avg Elapsed</th>
            <th scope="col">Avg Pool</th>
            <th scope="col">Avg Bundles</th>
            <th scope="col">Runs</th>
          </tr>
        </thead>
        <tbody>
          {rows.slice(0, 6).map((row, index) => (
            <tr key={row.caseId ?? row.instanceId ?? `case-${index}`}>
              <th scope="row" translate="no">
                {row.caseId ?? row.instanceId ?? "unknown"}
              </th>
              <td>{formatCount(row.averageExpectedCompletedOrders)}</td>
              <td>{formatCount(row.averageTotalCost)}</td>
              <td>{formatInteger(row.averageElapsedMs)} ms</td>
              <td>{formatInteger(row.averageCandidateOptionCount)}</td>
              <td>{formatInteger(row.averageBundleOptionCount)}</td>
              <td>{formatInteger(row.runs)}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function App() {
  const [data, setData] = useState<ReplayData | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [sourceLabel, setSourceLabel] = useState("Bundled replay-data.json");
  const [lastReloadedAt, setLastReloadedAt] = useState<string | null>(null);

  const loadBundledReplay = useEffectEvent(async (source = "Bundled replay-data.json") => {
    const response = await fetch(`/replay-data.json?ts=${Date.now()}`, { cache: "no-store" });
    const payload: unknown = await response.json();
    if (!isReplayData(payload)) {
      throw new Error("Replay data shape is invalid.");
    }
    startTransition(() => {
      setData(payload);
      setError(null);
      setSourceLabel(source);
      setLastReloadedAt(new Date().toISOString());
    });
  });

  useEffect(() => {
    let cancelled = false;

    async function bootstrap() {
      try {
        await loadBundledReplay();
      } catch (loadError) {
        if (!cancelled) {
          setError(loadError instanceof Error ? loadError.message : "Unknown replay load error");
        }
      }
    }

    void bootstrap();
    return () => {
      cancelled = true;
    };
  }, [loadBundledReplay]);

  useEffect(() => {
    if (!isBundledReplaySource(sourceLabel)) {
      return;
    }

    const intervalId = window.setInterval(() => {
      void loadBundledReplay();
    }, 2000);

    return () => {
      window.clearInterval(intervalId);
    };
  }, [loadBundledReplay, sourceLabel]);

  const events = data?.events ?? [];
  const summary = data?.summary;
  const agent: ReplayAgentSummary | undefined = data?.agent;
  const roundInsights = data?.roundInsights ?? [];
  const chartPoints = data?.chartPoints ?? [];
  const caseLeaderboard = data?.caseLeaderboard ?? [];
  const keepCount = summary?.keepCount ?? events.filter((event) => event.payload.status === "keep").length;
  const discardCount = summary?.discardCount ?? events.filter((event) => event.payload.status === "discard").length;
  const failureEvents = events.filter((event) => event.type === EVENT_TYPES.RESEARCH_ROUND_FAILED);
  const latestIncumbent = [...events].reverse().find((event) => event.type === EVENT_TYPES.RESEARCH_INCUMBENT_UPDATED);
  const latestReflection = [...roundInsights].reverse().find((round) => round.reflectionSummary);
  const autoRefreshEnabled = isBundledReplaySource(sourceLabel);
  const highlightTypes = new Set<string>([
    EVENT_TYPES.RESEARCH_LLM_PROPOSAL,
    EVENT_TYPES.RESEARCH_FALLBACK_PROPOSAL,
    EVENT_TYPES.RESEARCH_LLM_REFLECTION,
    EVENT_TYPES.RESEARCH_INCUMBENT_UPDATED,
    EVENT_TYPES.RESEARCH_ROUND_FAILED,
  ]);
  const highlightEvents = events
    .filter((event) => highlightTypes.has(event.type))
    .slice(-8)
    .reverse();

  async function handleLoadLocalReplay(event: ChangeEvent<HTMLInputElement>) {
    const file = event.target.files?.[0];
    if (!file) {
      return;
    }

    try {
      const text = await file.text();
      const payload: unknown = JSON.parse(text);
      if (!isReplayData(payload)) {
        throw new Error("Selected file is not a valid replay payload.");
      }
      startTransition(() => {
        setData(payload);
        setError(null);
        setSourceLabel(file.name);
        setLastReloadedAt(new Date().toISOString());
      });
    } catch (loadError) {
      setError(loadError instanceof Error ? loadError.message : "Failed to load the selected replay file.");
    } finally {
      event.target.value = "";
    }
  }

  function handleUseBundledReplay() {
    void loadBundledReplay();
  }

  return (
    <main className="page-shell" id="main-content">
      <a className="skip-link" href="#dashboard-panels">
        Skip to Dashboard Panels
      </a>

      <section className="hero">
        <div className="hero-copy">
          <p className="hero-kicker">AutoSolver Research Replay</p>
          <h1>Watch the agent propose, test, reject, and keep delivery strategies in one place.</h1>
          <p className="hero-text">
            The solver writes canonical events, the agent adds proposal and reflection traces, and this dashboard turns that stream into a decision room
            for strategy search, incumbent tracking, and failure analysis.
          </p>
          <div className="hero-meta">
            <span className="meta-chip">Benchmark {summary?.benchmarkId ?? agent?.benchmarkId ?? "n/a"}</span>
            <span className="meta-chip">Provider {agent?.provider ?? "n/a"}</span>
            <span className="meta-chip">{agent?.llmEnabled ? "LLM Live" : "Fallback Only"}</span>
            <span className="meta-chip">{autoRefreshEnabled ? "Live Replay On" : "Viewing Snapshot"}</span>
          </div>
        </div>
        <div className="hero-metrics">
          <MetricCard eyebrow="Rounds" value={formatInteger(summary?.roundCount)} detail="Research attempts replayed from JSONL." tone="accent" />
          <MetricCard eyebrow="Keeps" value={formatInteger(keepCount)} detail="Experiments that improved or established the incumbent." />
          <MetricCard eyebrow="Discards" value={formatInteger(discardCount)} detail="Experiments automatically rejected by the judge." />
          <MetricCard eyebrow="Failures" value={formatInteger(summary?.failureCount ?? failureEvents.length)} detail="Crashes, invalid runs, or timeouts." tone="quiet" />
          <MetricCard eyebrow="Best Expected" value={formatCount(summary?.bestExpectedCompletedOrders)} detail="Highest expected completion found so far." />
          <MetricCard eyebrow="Best Cost" value={formatCount(summary?.bestTotalCost)} detail="Lowest cost among incumbent-quality runs." />
        </div>
      </section>

      <ControlBar
        sourceLabel={sourceLabel}
        benchmarkId={summary?.benchmarkId ?? agent?.benchmarkId ?? null}
        provider={agent?.provider ?? "n/a"}
        autoRefreshEnabled={autoRefreshEnabled}
        lastReloadedAt={lastReloadedAt}
        onLoadLocalReplay={handleLoadLocalReplay}
        onUseBundledReplay={handleUseBundledReplay}
      />

      {error ? (
        <div className="error-banner" role="status" aria-live="polite">
          {error}
        </div>
      ) : null}

      <section className="analytics-strip" aria-label="Replay Summary">
        <MetricCard
          eyebrow="Session Started"
          value={formatTimestamp(agent?.sessionStartedAt)}
          detail={agent?.fallbackAllowed ? "Fallback path is allowed for this replay." : "LLM-first path required for this replay."}
        />
        <MetricCard
          eyebrow="Proposal Mix"
          value={`${formatInteger(agent?.proposalBreakdown.llm)} / ${formatInteger(agent?.proposalBreakdown.fallback)}`}
          detail="LLM proposals versus fallback proposals."
        />
        <MetricCard
          eyebrow="Latest Incumbent"
          value={latestIncumbent?.payload.experiment_id ?? summary?.latestIncumbentExperimentId ?? "n/a"}
          detail="Most recent experiment promoted to incumbent."
        />
        <MetricCard
          eyebrow="Tracked Events"
          value={formatInteger(summary?.eventCount)}
          detail="Important research, benchmark, and solve events captured."
        />
      </section>

      <section className="panel-grid" id="dashboard-panels">
        <section className="panel chart-panel">
          <div className="panel-head">
            <p className="panel-kicker">Score Curve</p>
            <h2>Incumbent Trend</h2>
          </div>
          <Chart points={chartPoints} />
        </section>

        <section className="panel incumbent-panel">
          <div className="panel-head">
            <p className="panel-kicker">Agent Snapshot</p>
            <h2>Latest Reflection & Incumbent</h2>
          </div>
          <div className="incumbent-card">
            <strong translate="no">{latestIncumbent?.payload.experiment_id ?? summary?.latestIncumbentExperimentId ?? "No incumbent yet"}</strong>
            <p>Expected completed orders: {formatCount(latestIncumbent?.payload.average_expected_completed_orders ?? summary?.bestExpectedCompletedOrders)}</p>
            <p>Total cost: {formatCount(latestIncumbent?.payload.average_total_cost ?? summary?.bestTotalCost)}</p>
            <p>Provider: <span translate="no">{agent?.provider ?? "n/a"}</span></p>
          </div>
          {latestReflection ? (
            <div className="insight-card">
              <p className="insight-kicker">Latest Reflection</p>
              <strong translate="no">{latestReflection.experimentId}</strong>
              <p>{latestReflection.reflectionSummary}</p>
              {latestReflection.nextFocus.length > 0 ? (
                <div className="list-block compact-list">
                  <p className="list-label">Next Focus</p>
                  <ul>
                    {latestReflection.nextFocus.slice(0, 2).map((item) => (
                      <li key={item}>{item}</li>
                    ))}
                  </ul>
                </div>
              ) : null}
            </div>
          ) : (
            <div className="empty-state">No reflection event has been recorded yet.</div>
          )}
        </section>
      </section>

      <section className="panel-grid">
        <section className="panel">
          <div className="panel-head">
            <p className="panel-kicker">Decision Ledger</p>
            <h2>Round-by-Round Strategy Notes</h2>
          </div>
          <div className="round-grid">
            {roundInsights.length > 0 ? roundInsights.map((round) => <RoundCard key={round.experimentId} round={round} />) : <div className="empty-state">No round insights are available.</div>}
          </div>
        </section>

        <section className="panel">
          <div className="panel-head">
            <p className="panel-kicker">Benchmark Cases</p>
            <h2>Hardest Cases to Improve</h2>
          </div>
          <CaseLeaderboard rows={caseLeaderboard} />
        </section>
      </section>

      <section className="panel-grid">
        <section className="panel">
          <div className="panel-head">
            <p className="panel-kicker">Key Moments</p>
            <h2>Proposal, Reflection, & Failure Feed</h2>
          </div>
          <EventList events={highlightEvents} />
        </section>

        <section className="panel">
          <div className="panel-head">
            <p className="panel-kicker">Failure Signals</p>
            <h2>Runs That Need Attention</h2>
          </div>
          <EventList events={failureEvents} />
        </section>
      </section>

      <section className="panel">
        <div className="panel-head">
          <p className="panel-kicker">Raw Events</p>
          <h2>Full Replay Stream</h2>
        </div>
        <EventList events={events.slice(-12).reverse()} />
      </section>
    </main>
  );
}

export default App;
