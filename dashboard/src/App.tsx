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

const LIVE_REPLAY_SOURCE_LABEL = "本地实时 replay-data.json";
const DEMO_REPLAY_SOURCE_LABEL = "云端内置 demo-replay.json";
const LIVE_REPLAY_FILE = "replay-data.json";
const DEMO_REPLAY_FILE = "demo-replay.json";

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
  emptyLabel: string;
}

interface RoundCardProps {
  round: RoundInsight;
}

interface PlaybackPanelProps {
  totalRounds: number;
  visibleRounds: number;
  isPlaying: boolean;
  speedMs: number;
  onStartPlayback: () => void;
  onTogglePlayback: () => void;
  onResetPlayback: () => void;
  onShowAllRounds: () => void;
  onSpeedChange: (event: ChangeEvent<HTMLSelectElement>) => void;
}

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
  return typeof value === "number" ? countFormatter.format(value) : "暂无";
}

function formatInteger(value: number | null | undefined): string {
  return typeof value === "number" ? integerFormatter.format(value) : "暂无";
}

function formatProposalType(value: string): string {
  if (value === "llm") {
    return "LLM 提案";
  }
  if (value === "fallback") {
    return "回退提案";
  }
  return "未知提案";
}

function formatStatus(value: string): string {
  if (value === "keep") {
    return "保留";
  }
  if (value === "discard") {
    return "淘汰";
  }
  if (value === "crash") {
    return "失败";
  }
  if (value === "pending") {
    return "进行中";
  }
  return value ? value.replace("_", " ") : "未知";
}

function formatTimestamp(value: string | null | undefined): string {
  if (!value) {
    return "暂无";
  }
  const parsed = new Date(value);
  return Number.isNaN(parsed.getTime()) ? value : timeFormatter.format(parsed);
}

function summarizeConfig(config: Record<string, unknown> | null): string[] {
  if (!config) {
    return [];
  }

  return [
    `Top-K ${String(config.top_k_riders_per_order ?? "暂无")}`,
    `CP-SAT ${config.use_cpsat === false ? "关闭" : "开启"}`,
    `合单 ${config.generate_bundles_if_missing === false ? "关闭" : "开启"}`,
    `LNS ${String(config.lns_iterations ?? "暂无")} 次`,
    `半径 ${String(config.bundle_distance_threshold ?? "暂无")}`,
  ];
}

function isBundledReplaySource(sourceLabel: string): boolean {
  return sourceLabel === LIVE_REPLAY_SOURCE_LABEL;
}

async function loadReplayPayload(filename: string): Promise<ReplayData> {
  const replayUrl = `${import.meta.env.BASE_URL}${filename}?ts=${Date.now()}`;
  const response = await fetch(replayUrl, { cache: "no-store" });
  if (!response.ok) {
    throw new Error(`加载 ${filename} 失败: ${response.status} ${response.statusText}`);
  }
  const payload: unknown = await response.json();
  if (!isReplayData(payload)) {
    throw new Error(`${filename} 的格式不符合 replay 结构。`);
  }
  return payload;
}

function extractExperimentId(event: ReplayEvent): string | null {
  const experimentId = event.payload.experiment_id;
  return typeof experimentId === "string" ? experimentId : null;
}

function collectScopedEvents(events: ReplayEvent[], experimentIds: Set<string>): ReplayEvent[] {
  if (experimentIds.size === 0) {
    return events;
  }

  const scoped: ReplayEvent[] = [];
  let activeExperimentId: string | null = null;

  for (const event of events) {
    const explicitExperimentId = extractExperimentId(event);
    if (event.type === EVENT_TYPES.RESEARCH_ROUND_STARTED) {
      activeExperimentId = explicitExperimentId;
    }

    const relatedExperimentId = explicitExperimentId ?? activeExperimentId;
    const isSessionEvent =
      event.type === EVENT_TYPES.RESEARCH_SESSION_STARTED || event.type === EVENT_TYPES.RESEARCH_SESSION_RESUMED;

    if (isSessionEvent || (relatedExperimentId !== null && experimentIds.has(relatedExperimentId))) {
      scoped.push(event);
    }

    if (event.type === EVENT_TYPES.RESEARCH_LLM_REFLECTION || event.type === EVENT_TYPES.RESEARCH_ROUND_FAILED) {
      activeExperimentId = null;
    }
  }

  return scoped;
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
        <p className="control-label">数据源</p>
        <strong translate="no">{sourceLabel}</strong>
        <p className="control-hint">
          基准集：<span translate="no">{benchmarkId ?? "暂无"}</span> · 模型：<span translate="no">{provider}</span>
        </p>
        <div className="live-status-row">
          <span className={`live-indicator ${autoRefreshEnabled ? "live-indicator-active" : "live-indicator-paused"}`}>
            {autoRefreshEnabled ? "正在自动刷新" : "当前是静态回放"}
          </span>
          <span className="live-timestamp">最近同步：{formatTimestamp(lastReloadedAt)}</span>
        </div>
        <p className="control-note">本地跑 `research` 时，这个页面会自动读取 `dashboard/public/replay-data.json`。</p>
      </div>
      <div className="control-actions">
        <button className="secondary-button" type="button" onClick={onUseBundledReplay}>
          加载托管回放
        </button>
        <label className="upload-button">
          上传本地回放
          <input className="upload-input" type="file" accept="application/json,.json" aria-label="上传本地回放 JSON" onChange={onLoadLocalReplay} />
        </label>
      </div>
    </section>
  );
}

function PlaybackPanel({
  totalRounds,
  visibleRounds,
  isPlaying,
  speedMs,
  onStartPlayback,
  onTogglePlayback,
  onResetPlayback,
  onShowAllRounds,
  onSpeedChange,
}: PlaybackPanelProps) {
  const playbackProgress = totalRounds === 0 ? 0 : (visibleRounds / totalRounds) * 100;
  const isPlaybackActive = visibleRounds > 0;

  return (
    <section className="panel playback-shell">
      <div className="panel-head">
        <p className="panel-kicker">网页演示</p>
        <h2>在浏览器里回放一轮 Agent 决策过程</h2>
      </div>
      <div className="playback-grid">
        <div className="playback-copy">
          <p className="hero-text playback-text">
            这个页面现在支持“网页内演示回放”：你可以直接点击开始，让轮次、关键事件和曲线按节奏展开。真实求解仍然由本地 CLI 或后端完成，
            但答辩和展示时，网页已经可以完整播放 Agent 的策略探索过程。
          </p>
          <div className="playback-track" aria-hidden="true">
            <div className="playback-fill" style={{ width: `${playbackProgress}%` }} />
          </div>
          <div className="playback-stats">
            <span className="meta-chip">总轮次 {formatInteger(totalRounds)}</span>
            <span className="meta-chip">已展示 {formatInteger(isPlaybackActive ? visibleRounds : totalRounds)}</span>
            <span className="meta-chip">{isPlaying ? "正在播放" : isPlaybackActive ? "已暂停" : "完整视图"}</span>
          </div>
        </div>
        <div className="playback-actions">
          <div className="button-row">
            <button className="secondary-button" type="button" onClick={onStartPlayback} disabled={totalRounds === 0}>
              开始演示
            </button>
            <button className="secondary-button" type="button" onClick={onTogglePlayback} disabled={totalRounds === 0 || visibleRounds === 0}>
              {isPlaying ? "暂停演示" : "继续播放"}
            </button>
          </div>
          <div className="button-row">
            <button className="secondary-button" type="button" onClick={onResetPlayback} disabled={totalRounds === 0}>
              重置回放
            </button>
            <button className="secondary-button" type="button" onClick={onShowAllRounds} disabled={totalRounds === 0}>
              查看全量
            </button>
          </div>
          <label className="speed-label">
            回放节奏
            <select className="speed-select" value={String(speedMs)} onChange={onSpeedChange}>
              <option value="800">快</option>
              <option value="1400">中</option>
              <option value="2200">慢</option>
            </select>
          </label>
        </div>
      </div>
    </section>
  );
}

function Chart({ points }: { points: ChartPoint[] }) {
  if (points.length === 0) {
    return <div className="empty-state">当前还没有可以绘图的分数曲线。</div>;
  }

  const expectedSeries = points.filter((point) => typeof point.expectedCompletedOrders === "number");
  const costSeries = points.filter((point) => typeof point.totalCost === "number");
  if (expectedSeries.length === 0) {
    return <div className="empty-state">当前回放还没有可展示的曲线点。</div>;
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
        <span className="legend-chip legend-primary">预计完单数</span>
        <span className="legend-chip legend-secondary">总成本</span>
      </div>
      <svg className="chart-svg" viewBox={`0 0 ${width} ${height}`} role="img" aria-label="回放分数曲线">
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
        <span>起点：{formatCount(expectedSeries[0]?.expectedCompletedOrders)}</span>
        <span>最好：{formatCount(Math.max(...expectedValues))}</span>
      </div>
    </div>
  );
}

function EventList({ events, emptyLabel }: EventListProps) {
  if (events.length === 0) {
    return <div className="empty-state">{emptyLabel}</div>;
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
        <span>预计完单：{formatCount(round.averageExpectedCompletedOrders)}</span>
        <span>总成本：{formatCount(round.averageTotalCost)}</span>
        <span>耗时：{formatInteger(round.totalElapsedMs)} ms</span>
        {typeof round.averageCandidateOptionCount === "number" ? <span>候选池：{formatInteger(round.averageCandidateOptionCount)}</span> : null}
        {typeof round.averageBundleOptionCount === "number" ? <span>合单候选：{formatInteger(round.averageBundleOptionCount)}</span> : null}
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
          <p className="list-label">下一步</p>
          <ul>
            {round.nextFocus.slice(0, 3).map((item) => (
              <li key={item}>{item}</li>
            ))}
          </ul>
        </div>
      ) : null}
      {round.risks.length > 0 ? (
        <div className="list-block">
          <p className="list-label">风险</p>
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
    return <div className="empty-state">当前回放没有可排序的 benchmark case。</div>;
  }

  return (
    <div className="table-shell">
      <table className="case-table">
        <caption>按平均预计完单数排序的 benchmark case。</caption>
        <thead>
          <tr>
            <th scope="col">Case</th>
            <th scope="col">平均预计完单</th>
            <th scope="col">平均成本</th>
            <th scope="col">平均耗时</th>
            <th scope="col">平均候选池</th>
            <th scope="col">平均合单</th>
            <th scope="col">运行次数</th>
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
  const [sourceLabel, setSourceLabel] = useState(LIVE_REPLAY_SOURCE_LABEL);
  const [lastReloadedAt, setLastReloadedAt] = useState<string | null>(null);
  const [visibleRoundCount, setVisibleRoundCount] = useState(0);
  const [isPlaying, setIsPlaying] = useState(false);
  const [playbackSpeedMs, setPlaybackSpeedMs] = useState(1400);

  const loadBundledReplay = useEffectEvent(async () => {
    let payload: ReplayData;
    let loadedSourceLabel = LIVE_REPLAY_SOURCE_LABEL;

    try {
      payload = await loadReplayPayload(LIVE_REPLAY_FILE);
    } catch (liveError) {
      payload = await loadReplayPayload(DEMO_REPLAY_FILE);
      loadedSourceLabel = DEMO_REPLAY_SOURCE_LABEL;
      if (liveError instanceof Error) {
        console.warn(`Falling back to ${DEMO_REPLAY_FILE}: ${liveError.message}`);
      }
    }

    startTransition(() => {
      setData(payload);
      setError(null);
      setSourceLabel(loadedSourceLabel);
      setLastReloadedAt(new Date().toISOString());
      setVisibleRoundCount(0);
      setIsPlaying(false);
    });
  });

  useEffect(() => {
    let cancelled = false;

    async function bootstrap() {
      try {
        await loadBundledReplay();
      } catch (loadError) {
        if (!cancelled) {
          setError(loadError instanceof Error ? loadError.message : "加载回放数据时发生未知错误。");
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

  const totalRounds = data?.roundInsights?.length ?? 0;

  useEffect(() => {
    if (!isPlaying || totalRounds === 0) {
      return;
    }

    const timerId = window.setTimeout(() => {
      setVisibleRoundCount((current) => {
        const nextValue = current <= 0 ? 1 : current + 1;
        if (nextValue >= totalRounds) {
          setIsPlaying(false);
          return totalRounds;
        }
        return nextValue;
      });
    }, playbackSpeedMs);

    return () => {
      window.clearTimeout(timerId);
    };
  }, [isPlaying, playbackSpeedMs, totalRounds, visibleRoundCount]);

  const events = data?.events ?? [];
  const summary = data?.summary;
  const agent: ReplayAgentSummary | undefined = data?.agent;
  const roundInsights = data?.roundInsights ?? [];
  const chartPoints = data?.chartPoints ?? [];
  const caseLeaderboard = data?.caseLeaderboard ?? [];

  const isPlaybackMode = visibleRoundCount > 0 && totalRounds > 0;
  const shownRounds = isPlaybackMode ? roundInsights.slice(0, visibleRoundCount) : roundInsights;
  const shownExperimentIds = new Set(shownRounds.map((round) => round.experimentId));
  const scopedEvents = isPlaybackMode ? collectScopedEvents(events, shownExperimentIds) : events;
  const shownChartPoints = isPlaybackMode ? chartPoints.slice(0, Math.min(chartPoints.length, Math.max(1, visibleRoundCount * 2))) : chartPoints;

  const visibleKeepCount = shownRounds.filter((round) => round.status === "keep").length;
  const visibleDiscardCount = shownRounds.filter((round) => round.status === "discard").length;
  const visibleFailureCount = shownRounds.filter((round) => round.status === "crash").length;
  const visibleBestRound = selectBestRound(shownRounds);
  const latestReflection = [...shownRounds].reverse().find((round) => round.reflectionSummary);
  const latestIncumbentRound = [...shownRounds].reverse().find((round) => round.status === "keep");
  const autoRefreshEnabled = isBundledReplaySource(sourceLabel);

  const highlightTypes = new Set<string>([
    EVENT_TYPES.RESEARCH_LLM_PROPOSAL,
    EVENT_TYPES.RESEARCH_FALLBACK_PROPOSAL,
    EVENT_TYPES.RESEARCH_LLM_REFLECTION,
    EVENT_TYPES.RESEARCH_INCUMBENT_UPDATED,
    EVENT_TYPES.RESEARCH_ROUND_FAILED,
  ]);

  const highlightEvents = scopedEvents
    .filter((event) => highlightTypes.has(event.type))
    .slice(-8)
    .reverse();
  const failureEvents = scopedEvents.filter((event) => event.type === EVENT_TYPES.RESEARCH_ROUND_FAILED);
  const rawEvents = scopedEvents.slice(-12).reverse();

  async function handleLoadLocalReplay(event: ChangeEvent<HTMLInputElement>) {
    const file = event.target.files?.[0];
    if (!file) {
      return;
    }

    try {
      const text = await file.text();
      const payload: unknown = JSON.parse(text);
      if (!isReplayData(payload)) {
        throw new Error("选择的文件不是合法的 replay JSON。");
      }
      startTransition(() => {
        setData(payload);
        setError(null);
        setSourceLabel(file.name);
        setLastReloadedAt(new Date().toISOString());
        setVisibleRoundCount(0);
        setIsPlaying(false);
      });
    } catch (loadError) {
      setError(loadError instanceof Error ? loadError.message : "加载本地回放文件失败。");
    } finally {
      event.target.value = "";
    }
  }

  function handleUseBundledReplay() {
    void loadBundledReplay();
  }

  function handleStartPlayback() {
    if (totalRounds === 0) {
      return;
    }
    setVisibleRoundCount(1);
    setIsPlaying(true);
  }

  function handleTogglePlayback() {
    if (totalRounds === 0) {
      return;
    }
    if (visibleRoundCount === 0) {
      setVisibleRoundCount(1);
      setIsPlaying(true);
      return;
    }
    setIsPlaying((current) => !current);
  }

  function handleResetPlayback() {
    setVisibleRoundCount(0);
    setIsPlaying(false);
  }

  function handleShowAllRounds() {
    setVisibleRoundCount(0);
    setIsPlaying(false);
  }

  function handleSpeedChange(event: ChangeEvent<HTMLSelectElement>) {
    setPlaybackSpeedMs(Number(event.target.value));
  }

  return (
    <main className="page-shell" id="main-content">
      <a className="skip-link" href="#dashboard-panels">
        跳转到看板主体
      </a>

      <section className="hero">
        <div className="hero-copy">
          <p className="hero-kicker">AutoSolver 智能体看板</p>
          <h1>在一个页面里看清 Agent 的提案、试错、淘汰和保留</h1>
          <p className="hero-text">
            这个 dashboard 现在已经偏向答辩展示风格：上面是核心指标和网页演示入口，中间是轮次决策、关键事件和曲线，下面保留原始事件流，
            既能讲故事，也能对照数据排查问题。
          </p>
          <div className="hero-meta">
            <span className="meta-chip">基准集 {summary?.benchmarkId ?? agent?.benchmarkId ?? "暂无"}</span>
            <span className="meta-chip">模型 {agent?.provider ?? "暂无"}</span>
            <span className="meta-chip">{agent?.llmEnabled ? "LLM 已启用" : "仅回退模式"}</span>
            <span className="meta-chip">{isPlaybackMode ? "网页演示中" : autoRefreshEnabled ? "实时跟踪" : "静态快照"}</span>
          </div>
        </div>
        <div className="hero-metrics">
          <MetricCard eyebrow="轮次" value={formatInteger(shownRounds.length)} detail="当前页面正在展示的实验轮次。" tone="accent" />
          <MetricCard eyebrow="保留" value={formatInteger(visibleKeepCount)} detail="被 judge 接受并成为候选最优的实验。" />
          <MetricCard eyebrow="淘汰" value={formatInteger(visibleDiscardCount)} detail="自动评估后被判定为退化的实验。" />
          <MetricCard eyebrow="失败" value={formatInteger(visibleFailureCount)} detail="崩溃、超时或无效运行。" tone="quiet" />
          <MetricCard eyebrow="最好完单" value={formatCount(visibleBestRound?.averageExpectedCompletedOrders ?? summary?.bestExpectedCompletedOrders)} detail="当前视图中的最优预计完单数。" />
          <MetricCard eyebrow="最好成本" value={formatCount(visibleBestRound?.averageTotalCost ?? summary?.bestTotalCost)} detail="在当前最优视图下的对应成本。" />
        </div>
      </section>

      <ControlBar
        sourceLabel={sourceLabel}
        benchmarkId={summary?.benchmarkId ?? agent?.benchmarkId ?? null}
        provider={agent?.provider ?? "暂无"}
        autoRefreshEnabled={autoRefreshEnabled}
        lastReloadedAt={lastReloadedAt}
        onLoadLocalReplay={handleLoadLocalReplay}
        onUseBundledReplay={handleUseBundledReplay}
      />

      <PlaybackPanel
        totalRounds={totalRounds}
        visibleRounds={isPlaybackMode ? visibleRoundCount : totalRounds}
        isPlaying={isPlaying}
        speedMs={playbackSpeedMs}
        onStartPlayback={handleStartPlayback}
        onTogglePlayback={handleTogglePlayback}
        onResetPlayback={handleResetPlayback}
        onShowAllRounds={handleShowAllRounds}
        onSpeedChange={handleSpeedChange}
      />

      {error ? (
        <div className="error-banner" role="status" aria-live="polite">
          {error}
        </div>
      ) : null}

      <section className="analytics-strip" aria-label="回放摘要">
        <MetricCard
          eyebrow="会话开始"
          value={formatTimestamp(agent?.sessionStartedAt)}
          detail={agent?.fallbackAllowed ? "当前会话允许 fallback 提案。" : "当前会话要求优先使用 LLM。"}
        />
        <MetricCard
          eyebrow="提案比例"
          value={`${formatInteger(agent?.proposalBreakdown.llm)} / ${formatInteger(agent?.proposalBreakdown.fallback)}`}
          detail="LLM 提案数 / fallback 提案数。"
        />
        <MetricCard
          eyebrow="当前 Incumbent"
          value={latestIncumbentRound?.experimentId ?? summary?.latestIncumbentExperimentId ?? "暂无"}
          detail="当前视图里最近一次成为最优的实验。"
        />
        <MetricCard
          eyebrow="事件数量"
          value={formatInteger(scopedEvents.length)}
          detail="当前页面正在使用的事件条数。"
        />
      </section>

      <section className="panel-grid" id="dashboard-panels">
        <section className="panel chart-panel">
          <div className="panel-head">
            <p className="panel-kicker">分数曲线</p>
            <h2>Incumbent 演化过程</h2>
          </div>
          <Chart points={shownChartPoints} />
        </section>

        <section className="panel incumbent-panel">
          <div className="panel-head">
            <p className="panel-kicker">当前焦点</p>
            <h2>最新反思与当前最优</h2>
          </div>
          <div className="incumbent-card">
            <strong translate="no">{latestIncumbentRound?.experimentId ?? summary?.latestIncumbentExperimentId ?? "暂无最优实验"}</strong>
            <p>预计完单数：{formatCount(latestIncumbentRound?.averageExpectedCompletedOrders ?? visibleBestRound?.averageExpectedCompletedOrders)}</p>
            <p>总成本：{formatCount(latestIncumbentRound?.averageTotalCost ?? visibleBestRound?.averageTotalCost)}</p>
            <p>
              Provider：<span translate="no">{agent?.provider ?? "暂无"}</span>
            </p>
          </div>
          {latestReflection ? (
            <div className="insight-card">
              <p className="insight-kicker">最新反思</p>
              <strong translate="no">{latestReflection.experimentId}</strong>
              <p>{latestReflection.reflectionSummary}</p>
              {latestReflection.nextFocus.length > 0 ? (
                <div className="list-block compact-list">
                  <p className="list-label">下一步</p>
                  <ul>
                    {latestReflection.nextFocus.slice(0, 2).map((item) => (
                      <li key={item}>{item}</li>
                    ))}
                  </ul>
                </div>
              ) : null}
            </div>
          ) : (
            <div className="empty-state">当前还没有可展示的 reflection 事件。</div>
          )}
        </section>
      </section>

      <section className="panel-grid">
        <section className="panel">
          <div className="panel-head">
            <p className="panel-kicker">轮次账本</p>
            <h2>每一轮到底试了什么</h2>
          </div>
          <div className="round-grid">
            {shownRounds.length > 0 ? (
              shownRounds.map((round) => <RoundCard key={round.experimentId} round={round} />)
            ) : (
              <div className="empty-state">当前没有轮次数据，先加载 replay 或上传本地 JSON。</div>
            )}
          </div>
        </section>

        <section className="panel">
          <div className="panel-head">
            <p className="panel-kicker">Benchmark Case</p>
            <h2>最难优化的样例</h2>
          </div>
          <CaseLeaderboard rows={caseLeaderboard} />
        </section>
      </section>

      <section className="panel-grid">
        <section className="panel">
          <div className="panel-head">
            <p className="panel-kicker">关键事件</p>
            <h2>提案、反思与最优更新</h2>
          </div>
          <EventList events={highlightEvents} emptyLabel="当前还没有关键事件可展示。" />
        </section>

        <section className="panel">
          <div className="panel-head">
            <p className="panel-kicker">失败信号</p>
            <h2>需要优先排查的异常</h2>
          </div>
          <EventList events={failureEvents} emptyLabel="当前视图里没有失败事件。" />
        </section>
      </section>

      <section className="panel">
        <div className="panel-head">
          <p className="panel-kicker">原始事件流</p>
          <h2>最后 12 条事件</h2>
        </div>
        <EventList events={rawEvents} emptyLabel="当前还没有原始事件可展示。" />
      </section>
    </main>
  );
}

export default App;
