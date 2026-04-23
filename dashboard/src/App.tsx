import { startTransition, useEffect, useEffectEvent, useState, type ChangeEvent } from "react";
import {
  EVENT_TYPES,
  isReplayData,
  type CaseLeaderboardEntry,
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

const PROCESS_STEPS = [
  {
    step: "01",
    title: "读取配送场景",
    description: "先把订单、骑手、接单概率、成本分数和业务约束统一整理成一个可求解的问题。",
  },
  {
    step: "02",
    title: "Agent 提出策略",
    description: "LLM 会结合历史经验形成下一轮假设，而不是机械地重复同一套算法。",
  },
  {
    step: "03",
    title: "工具执行求解",
    description: "本地 solver 会真的跑组合求解，把每个样例的结果和候选池信息算出来。",
  },
  {
    step: "04",
    title: "自动评估与复盘",
    description: "系统会自动 keep 或 discard，并把经验写回记忆，推动下一轮继续变聪明。",
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
  onUseBundledReplay: () => void;
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

function formatTimestamp(value: string | null | undefined): string {
  if (!value) {
    return "暂无";
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
  return Array.isArray(value) ? value.map((item) => String(item)) : [];
}

function summarizeConfig(config: Record<string, unknown> | null): string[] {
  if (!config) {
    return [];
  }

  return [
    `Top-K ${String(config.top_k_riders_per_order ?? "暂无")}`,
    `CP-SAT ${config.use_cpsat === false ? "关闭" : "开启"}`,
    `补合单 ${config.generate_bundles_if_missing === false ? "关闭" : "开启"}`,
    `合单池 ${String(config.bundle_candidate_pool_size ?? "暂无")}`,
    `LNS ${String(config.lns_iterations ?? "暂无")} 次`,
  ];
}

function extractExperimentId(event: ReplayEvent): string | null {
  return asString(event.payload.experiment_id);
}

function extractRoundIndex(event: ReplayEvent): number | null {
  return asNumber(event.payload.round_index);
}

function formatStatus(status: string | null | undefined): string {
  if (status === "keep") {
    return "保留";
  }
  if (status === "discard") {
    return "淘汰";
  }
  if (status === "crash") {
    return "失败";
  }
  if (status === "pending") {
    return "进行中";
  }
  return status ?? "未知";
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

function isBundledReplaySource(sourceLabel: string): boolean {
  return sourceLabel === LIVE_REPLAY_SOURCE_LABEL;
}

async function loadReplayPayload(filename: string): Promise<ReplayData> {
  const replayUrl = `${import.meta.env.BASE_URL}${filename}?ts=${Date.now()}`;
  const response = await fetch(replayUrl, { cache: "no-store" });
  if (!response.ok) {
    throw new Error(`加载 ${filename} 失败：${response.status} ${response.statusText}`);
  }
  const payload: unknown = await response.json();
  if (!isReplayData(payload)) {
    throw new Error(`${filename} 不是合法的 replay JSON。`);
  }
  return payload;
}

function buildStoryBeats(events: ReplayEvent[]): StoryBeat[] {
  const beats: StoryBeat[] = [];

  for (const event of events) {
    const payload = isRecord(event.payload) ? event.payload : {};
    const experimentId = extractExperimentId(event);
    const roundIndex = extractRoundIndex(event);

    if (event.type === EVENT_TYPES.RESEARCH_SESSION_STARTED) {
      const benchmarkId = asString(event.payload.benchmark_id) ?? "当前基准集";
      const provider = asString(event.payload.provider) ?? "未知模型";
      const llmEnabled = asBoolean(event.payload.llm_enabled);
      beats.push({
        id: `${event.ts}-${event.type}`,
        ts: event.ts,
        type: event.type,
        tone: "system",
        label: "系统",
        title: "Agent 接到新的配送优化任务",
        body: `系统已载入 ${benchmarkId}，现在开始自主探索更好的分配策略。接下来你会看到它如何提案、调用工具、评估结果，再自己修改方向。`,
        meta: [provider, llmEnabled ? "LLM 已启用" : "当前为离线回退模式"],
        experimentId,
        roundIndex,
        payload,
      });
      continue;
    }

    if (event.type === EVENT_TYPES.RESEARCH_SESSION_RESUMED) {
      beats.push({
        id: `${event.ts}-${event.type}`,
        ts: event.ts,
        type: event.type,
        tone: "system",
        label: "系统",
        title: "继续上一次会话",
        body: "系统不是从零开始，而是带着已有经验和状态继续往前跑。",
        meta: [asString(event.payload.state_path) ?? "已恢复历史状态"],
        experimentId,
        roundIndex,
        payload,
      });
      continue;
    }

    if (event.type === EVENT_TYPES.RESEARCH_LLM_PROPOSAL || event.type === EVENT_TYPES.RESEARCH_FALLBACK_PROPOSAL) {
      beats.push({
        id: `${event.ts}-${event.type}`,
        ts: event.ts,
        type: event.type,
        tone: "agent",
        label: event.type === EVENT_TYPES.RESEARCH_LLM_PROPOSAL ? "Agent" : "回退策略",
        title: event.type === EVENT_TYPES.RESEARCH_LLM_PROPOSAL ? "我准备试一组新策略" : "切回保底提案继续搜索",
        body: asString(event.payload.hypothesis) ?? "这一轮没有留下可展示的假设说明。",
        meta: summarizeConfig(isRecord(event.payload.solver_config) ? event.payload.solver_config : null),
        experimentId,
        roundIndex,
        payload,
      });
      continue;
    }

    if (event.type === EVENT_TYPES.RESEARCH_ROUND_STARTED) {
      beats.push({
        id: `${event.ts}-${event.type}`,
        ts: event.ts,
        type: event.type,
        tone: "tool",
        label: "工具",
        title: "本地求解器开始执行",
        body: "Agent 已把这组参数交给本地 solver。现在它会真正去跑 benchmark，而不是只停留在口头推理。",
        meta: summarizeConfig(isRecord(event.payload.solver_config) ? event.payload.solver_config : null),
        experimentId,
        roundIndex,
        payload,
      });
      continue;
    }

    if (event.type === EVENT_TYPES.BENCHMARK_CASE_COMPLETED) {
      const stats = isRecord(payload.stats) ? payload.stats : null;
      const candidateBreakdown = isRecord(stats?.candidate_option_breakdown) ? stats.candidate_option_breakdown : null;
      const strategy = asString(stats?.strategy) ?? "portfolio";
      const candidateCount = asNumber(stats?.candidate_option_count);
      const bundleCount = asNumber(candidateBreakdown?.bundle);
      beats.push({
        id: `${event.ts}-${event.type}-${asString(event.payload.case_id) ?? "case"}`,
        ts: event.ts,
        type: event.type,
        tone: "tool",
        label: "工具回传",
        title: `${asString(event.payload.case_id) ?? "样例"} 已完成`,
        body: `这一个样例的结果已经算出来了：预计完单 ${formatCount(event.payload.expected_completed_orders)}，总成本 ${formatCount(event.payload.total_cost)}，耗时 ${formatInteger(event.payload.elapsed_ms)} ms。`,
        meta: [
          `求解策略 ${strategy}`,
          candidateCount !== null ? `候选池 ${formatInteger(candidateCount)}` : "候选池 暂无",
          bundleCount !== null ? `合单候选 ${formatInteger(bundleCount)}` : "合单候选 暂无",
        ],
        experimentId,
        roundIndex,
        payload,
      });
      continue;
    }

    if (event.type === EVENT_TYPES.BENCHMARK_COMPLETED) {
      beats.push({
        id: `${event.ts}-${event.type}`,
        ts: event.ts,
        type: event.type,
        tone: "judge",
        label: "结果汇总",
        title: "这一轮 benchmark 已跑完",
        body: `系统把所有样例汇总后发现：这轮的加权平均预计完单是 ${formatCount(event.payload.average_expected_completed_orders)}，平均成本是 ${formatCount(event.payload.average_total_cost)}。`,
        meta: [`总耗时 ${formatInteger(event.payload.total_elapsed_ms)} ms`, `样例数 ${formatInteger(event.payload.case_count)}`],
        experimentId,
        roundIndex,
        payload,
      });
      continue;
    }

    if (event.type === EVENT_TYPES.RESEARCH_ROUND_COMPLETED) {
      const status = asString(event.payload.status);
      beats.push({
        id: `${event.ts}-${event.type}`,
        ts: event.ts,
        type: event.type,
        tone: "judge",
        label: "Judge",
        title: `自动判定：${formatStatus(status)}`,
        body:
          status === "keep"
            ? "这一轮打赢了当前最优解，所以系统会把它保留下来，作为后面继续探索的新锚点。"
            : status === "discard"
              ? "这一轮没有打赢当前最优解，所以会被淘汰，但系统仍会记住它失败的原因。"
              : "这一轮执行异常。系统会记录失败模式，然后换条路继续往前试。",
        meta: [
          `预计完单 ${formatCount(event.payload.average_expected_completed_orders)}`,
          `总成本 ${formatCount(event.payload.average_total_cost)}`,
          `耗时 ${formatInteger(event.payload.total_elapsed_ms)} ms`,
        ],
        experimentId,
        roundIndex,
        payload,
      });
      continue;
    }

    if (event.type === EVENT_TYPES.RESEARCH_INCUMBENT_UPDATED) {
      beats.push({
        id: `${event.ts}-${event.type}`,
        ts: event.ts,
        type: event.type,
        tone: "system",
        label: "系统更新",
        title: "当前最优方案被刷新",
        body: `现在的 incumbent 已经切换成 ${experimentId ?? "新的实验"}。后面的提案会围绕它继续做更精细的尝试。`,
        meta: [
          `预计完单 ${formatCount(event.payload.average_expected_completed_orders)}`,
          `总成本 ${formatCount(event.payload.average_total_cost)}`,
        ],
        experimentId,
        roundIndex,
        payload,
      });
      continue;
    }

    if (event.type === EVENT_TYPES.RESEARCH_LLM_REFLECTION || event.type === EVENT_TYPES.RESEARCH_HEURISTIC_REFLECTION) {
      const nextFocus = asStringArray(event.payload.next_focus);
      beats.push({
        id: `${event.ts}-${event.type}`,
        ts: event.ts,
        type: event.type,
        tone: "agent",
        label: event.type === EVENT_TYPES.RESEARCH_LLM_REFLECTION ? "Agent 复盘" : "启发式复盘",
        title: "我来复盘这一轮，并决定下一步",
        body: asString(event.payload.summary) ?? "这一轮没有留下可展示的复盘摘要。",
        meta: [
          ...(asString(event.payload.keep_reason) ? [asString(event.payload.keep_reason) ?? ""] : []),
          ...nextFocus.slice(0, 2),
        ].filter(Boolean),
        experimentId,
        roundIndex,
        payload,
      });
      continue;
    }

    if (event.type === EVENT_TYPES.RESEARCH_ROUND_FAILED) {
      beats.push({
        id: `${event.ts}-${event.type}`,
        ts: event.ts,
        type: event.type,
        tone: "judge",
        label: "异常处理",
        title: "这一轮执行失败",
        body: asString(event.payload.error) ?? "出现了未记录的执行错误。",
        meta: ["失败不会终止会话，系统会吸收这次经验继续搜索。"],
        experimentId,
        roundIndex,
        payload,
      });
    }
  }

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
    .filter(
      (round) =>
        typeof round.averageExpectedCompletedOrders === "number" &&
        typeof round.averageTotalCost === "number",
    )
    .map((round, index) => ({
      x: index,
      expected: round.averageExpectedCompletedOrders ?? 0,
      cost: round.averageTotalCost ?? 0,
    }));
}

function useTypedText(text: string, active: boolean) {
  const [visibleLength, setVisibleLength] = useState(active ? 0 : text.length);

  useEffect(() => {
    if (!active) {
      setVisibleLength(text.length);
      return;
    }

    setVisibleLength(0);
    const timerId = window.setInterval(() => {
      setVisibleLength((current) => {
        if (current >= text.length) {
          window.clearInterval(timerId);
          return text.length;
        }
        return current + 2;
      });
    }, 18);

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
  onUseBundledReplay,
}: ControlBarProps) {
  return (
    <section className="control-bar">
      <div className="control-copy">
        <p className="section-eyebrow">数据来源</p>
        <strong className="control-source" translate="no">
          {sourceLabel}
        </strong>
        <p className="control-meta">
          基准集：<span translate="no">{benchmarkId ?? "暂无"}</span>
          <span className="control-sep" aria-hidden="true">
            /
          </span>
          模型：<span translate="no">{provider}</span>
        </p>
        <div className="live-row">
          <span className={`live-pill ${autoRefreshEnabled ? "live-active" : "live-static"}`}>
            {autoRefreshEnabled ? "正在自动刷新" : "当前为静态快照"}
          </span>
          <span className="live-time">最近同步：{formatTimestamp(lastReloadedAt)}</span>
        </div>
      </div>
      <div className="control-actions">
        <button className="ghost-button" type="button" onClick={onUseBundledReplay}>
          重新读取默认回放
        </button>
        <label className="primary-button upload-button">
          上传本地 JSON
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
  const shownCount = visibleBeats === 0 ? totalBeats : visibleBeats;
  const progress = totalBeats === 0 ? 0 : (shownCount / totalBeats) * 100;

  return (
    <section className="playback-panel">
      <div className="playback-copy">
        <p className="section-eyebrow">动画演示</p>
        <h2>像看 Agent 会话直播一样，看见每一次思考和工具调用</h2>
        <p className="section-text">
          参考你给的 Kaggle Agent Session 示例，这里把主舞台改成了“会话播放器”。播放时会一条条生成气泡，
          当前步骤会高亮、逐字出现，工具调用还能点开看结构化细节。
        </p>
        <div className="progress-track" aria-hidden="true">
          <div className="progress-fill" style={{ width: `${progress}%` }} />
        </div>
        <div className="progress-meta">
          <span className="meta-chip">总步骤 {formatInteger(totalBeats)}</span>
          <span className="meta-chip">已展示 {formatInteger(shownCount)}</span>
          <span className="meta-chip">{isPlaying ? "正在播放" : isFinished ? "播放完成" : visibleBeats > 0 ? "已暂停" : "完整视图"}</span>
        </div>
      </div>
      <div className="playback-actions">
        <div className="button-row">
          <button className="primary-button" type="button" onClick={onStartPlayback} disabled={totalBeats === 0}>
            开始演示
          </button>
          <button className="ghost-button" type="button" onClick={onTogglePlayback} disabled={totalBeats === 0}>
            {isPlaying ? "暂停" : visibleBeats > 0 ? "继续" : "从头播放"}
          </button>
        </div>
        <div className="button-row">
          <button className="ghost-button" type="button" onClick={onResetPlayback} disabled={totalBeats === 0}>
            清空动画
          </button>
          <button className="ghost-button" type="button" onClick={onShowAll} disabled={totalBeats === 0}>
            直接看全量
          </button>
        </div>
        <label className="speed-box">
          播放节奏
          <select value={String(speedMs)} onChange={onSpeedChange}>
            <option value="700">快</option>
            <option value="1200">中</option>
            <option value="1800">慢</option>
          </select>
        </label>
      </div>
    </section>
  );
}

function ProcessSteps() {
  return (
    <section className="process-panel">
      <div className="panel-head">
        <p className="section-eyebrow">先看全局</p>
        <h2>这个 Agent 是如何一轮轮变聪明的</h2>
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
  const isDetailWorthy = Object.keys(beat.payload).length > 0 && beat.tone !== "agent";

  return (
    <article
      className={`session-row session-${beat.tone} ${isActive ? "session-row-active" : ""}`}
      id={`beat-${beat.id}`}
    >
      <div className="session-bubble">
        <div className="session-bubble-head">
          <div>
            <p className="session-bubble-label">{beat.label}</p>
            <strong>{beat.title}</strong>
          </div>
          <div className="session-bubble-tags">
            {beat.roundIndex !== null ? <span className="meta-chip">第 {beat.roundIndex + 1} 轮</span> : null}
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
              查看详情
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
      <div
        className="detail-modal"
        role="dialog"
        aria-modal="true"
        aria-label="查看事件详情"
        onClick={(event) => event.stopPropagation()}
      >
        <div className="detail-modal-head">
          <div>
            <p className="section-eyebrow">结构化详情</p>
            <h3>{beat.title}</h3>
          </div>
          <button className="detail-close" type="button" onClick={onClose}>
            ×
          </button>
        </div>
        <div className="detail-modal-grid">
          <div className="detail-card">
            <strong>事件信息</strong>
            <p>类型：{beat.type}</p>
            <p>时间：{formatTimestamp(beat.ts)}</p>
            <p>轮次：{beat.roundIndex !== null ? `第 ${beat.roundIndex + 1} 轮` : "暂无"}</p>
            <p>实验：{beat.experimentId ?? "暂无"}</p>
          </div>
          <div className="detail-card detail-card-code">
            <strong>原始 payload</strong>
            <pre>{JSON.stringify(beat.payload, null, 2)}</pre>
          </div>
        </div>
      </div>
    </div>
  );
}

function SessionViewer({
  beats,
  currentBeat,
  rounds,
  totalBeats,
  visibleBeats,
  isPlaybackMode,
  isPlaying,
  onOpenDetails,
}: SessionViewerProps) {
  useEffect(() => {
    if (!currentBeat) {
      return;
    }

    const element = document.getElementById(`beat-${currentBeat.id}`);
    if (!element) {
      return;
    }

    window.requestAnimationFrame(() => {
      element.scrollIntoView({ block: "nearest", behavior: "smooth" });
    });
  }, [currentBeat]);

  const activeRoundIndex = currentBeat?.roundIndex ?? (rounds.length > 0 ? rounds.length - 1 : null);
  const shownCount = visibleBeats === 0 ? totalBeats : visibleBeats;

  return (
    <section className="session-viewer" id="story-stage">
      <aside className="session-sidebar">
        <div className="session-sidebar-head">
          <p className="section-eyebrow">会话导航</p>
          <h2>Agent Session</h2>
          <p>当前主舞台参考你给的示例，改成了真正的会话播放器。</p>
        </div>
        <div className="session-sidebar-block">
          <strong>播放进度</strong>
          <p>
            已展示 {formatInteger(shownCount)} / {formatInteger(totalBeats)} 个动作节点
          </p>
        </div>
        <div className="session-sidebar-block">
          <strong>轮次切片</strong>
          <div className="session-round-list">
            {rounds.length > 0 ? (
              rounds.map((round, index) => (
                <article
                  className={`session-round-item ${activeRoundIndex === index ? "session-round-item-active" : ""}`}
                  key={round.experimentId}
                >
                  <div className="session-round-row">
                    <span className={`status-pill status-${round.status}`}>{formatStatus(round.status)}</span>
                    <span className="session-round-index">第 {index + 1} 轮</span>
                  </div>
                  <strong translate="no">{round.experimentId}</strong>
                  <p>{round.hypothesis}</p>
                </article>
              ))
            ) : (
              <div className="empty-state dark-empty">播放还没走到完整轮次。</div>
            )}
          </div>
        </div>
      </aside>

      <div className="session-main">
        <div className="session-topbar">
          <div>
            <span className="session-title">AutoSolver 会话回放</span>
            <p className="session-subtitle">
              {currentBeat
                ? `当前镜头：${currentBeat.title}`
                : "当前是完整视图，你可以直接滚动查看整个 Agent 链路。"}
            </p>
          </div>
          <div className="session-topbar-tags">
            <span className="meta-chip">{isPlaybackMode ? "动画模式" : "完整模式"}</span>
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
              <SessionBubble
                key={beat.id}
                beat={beat}
                isActive={Boolean(isPlaying && currentBeat?.id === beat.id)}
                onOpenDetails={onOpenDetails}
              />
            ))
          ) : (
            <div className="empty-state dark-empty">当前没有可展示的会话消息。</div>
          )}
        </div>
      </div>
    </section>
  );
}

function ScoreChart({ rounds }: { rounds: RoundInsight[] }) {
  const points = buildChartPoints(rounds);
  if (points.length === 0) {
    return <div className="empty-state">需要至少一轮完整结果，才能画出分数变化曲线。</div>;
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

  const costPath = points
    .map((point, index) => `${index === 0 ? "M" : "L"} ${positionX(index)} ${positionY(point.cost, minCost, maxCost)}`)
    .join(" ");

  return (
    <div className="chart-shell">
      <div className="chart-legend">
        <span className="legend-chip legend-primary">预计完单</span>
        <span className="legend-chip legend-secondary">总成本</span>
      </div>
      <svg className="chart-svg" viewBox={`0 0 ${width} ${height}`} role="img" aria-label="实验轮次分数变化曲线">
        <rect className="chart-backdrop" width={width} height={height} rx="26" />
        <path className="chart-line chart-line-primary" d={expectedPath} />
        <path className="chart-line chart-line-secondary" d={costPath} />
        {points.map((point, index) => (
          <circle
            className="chart-dot chart-dot-primary"
            key={`expected-${index}`}
            cx={positionX(index)}
            cy={positionY(point.expected, minExpected, maxExpected)}
            r="5"
          />
        ))}
      </svg>
      <div className="chart-footer">
        <span>最优预计完单：{formatCount(Math.max(...expectedValues))}</span>
        <span>最低总成本：{formatCount(Math.min(...costValues))}</span>
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
          <span className={`status-pill status-${round.status}`}>{formatStatus(round.status)}</span>
          <span className={`proposal-pill proposal-${round.proposalType}`}>{formatProposalType(round.proposalType)}</span>
        </div>
        <strong translate="no">{round.experimentId}</strong>
      </div>
      <p className="round-title">{round.hypothesis}</p>
      <div className="round-stats">
        <span>预计完单 {formatCount(round.averageExpectedCompletedOrders)}</span>
        <span>总成本 {formatCount(round.averageTotalCost)}</span>
        <span>耗时 {formatInteger(round.totalElapsedMs)} ms</span>
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
        <span className="meta-chip">运行 {formatInteger(row.runs)} 次</span>
      </div>
      <p className="case-copy">
        平均预计完单 {formatCount(row.averageExpectedCompletedOrders)}，平均成本 {formatCount(row.averageTotalCost)}，平均耗时{" "}
        {formatInteger(row.averageElapsedMs)} ms。
      </p>
      <div className="story-meta">
        <span className="config-chip">候选池 {formatInteger(row.averageCandidateOptionCount)}</span>
        <span className="config-chip">合单候选 {formatInteger(row.averageBundleOptionCount)}</span>
        {row.lastSolverName ? <span className="config-chip">{row.lastSolverName}</span> : null}
      </div>
    </article>
  );
}

function DebugDrawer({ events }: { events: ReplayEvent[] }) {
  return (
    <details className="debug-drawer">
      <summary>查看原始事件流</summary>
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
  const [visibleBeatCount, setVisibleBeatCount] = useState(0);
  const [isPlaying, setIsPlaying] = useState(false);
  const [playbackSpeedMs, setPlaybackSpeedMs] = useState(1200);
  const [detailBeat, setDetailBeat] = useState<StoryBeat | null>(null);
  const [hasAutoStarted, setHasAutoStarted] = useState(false);

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

    const isFirstLoad = data === null;

    startTransition(() => {
      setData(payload);
      setError(null);
      setSourceLabel(loadedSourceLabel);
      setLastReloadedAt(new Date().toISOString());
      if (isFirstLoad) {
        setVisibleBeatCount(0);
        setIsPlaying(false);
      }
    });
  });

  useEffect(() => {
    let cancelled = false;

    async function bootstrap() {
      try {
        await loadBundledReplay();
      } catch (loadError) {
        if (!cancelled) {
          setError(loadError instanceof Error ? loadError.message : "加载 replay 数据时发生未知错误。");
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
  const roundInsights = data?.roundInsights ?? [];
  const caseLeaderboard = data?.caseLeaderboard ?? [];
  const summary = data?.summary;
  const agent: ReplayAgentSummary | undefined = data?.agent;
  const storyBeats = buildStoryBeats(events);
  const totalBeats = storyBeats.length;
  const isPlaybackFinished = totalBeats > 0 && visibleBeatCount >= totalBeats && !isPlaying;
  const isPartialPlayback = visibleBeatCount > 0 && visibleBeatCount < totalBeats;
  const isPlaybackMode = isPartialPlayback;
  const shownBeats = isPartialPlayback ? storyBeats.slice(0, visibleBeatCount) : storyBeats;
  const completedRoundCount = shownBeats.filter((beat) => beat.type === EVENT_TYPES.RESEARCH_ROUND_COMPLETED).length;
  const shownRounds = isPartialPlayback ? roundInsights.slice(0, completedRoundCount) : roundInsights;
  const currentBeat = isPartialPlayback ? shownBeats.at(-1) ?? null : null;
  const visibleKeepCount = shownRounds.filter((round) => round.status === "keep").length;
  const visibleDiscardCount = shownRounds.filter((round) => round.status === "discard").length;
  const visibleFailureCount = shownRounds.filter((round) => round.status === "crash").length;
  const bestRound = selectBestRound(shownRounds.length > 0 ? shownRounds : roundInsights);
  const autoRefreshEnabled = isBundledReplaySource(sourceLabel);

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
        throw new Error("你选择的文件不是合法的 replay JSON。");
      }
      startTransition(() => {
        setData(payload);
        setError(null);
        setSourceLabel(file.name);
        setLastReloadedAt(new Date().toISOString());
        setVisibleBeatCount(0);
        setIsPlaying(false);
        setHasAutoStarted(false);
      });
    } catch (loadError) {
      setError(loadError instanceof Error ? loadError.message : "加载本地 JSON 失败。");
    } finally {
      event.target.value = "";
    }
  }

  function handleUseBundledReplay() {
    setVisibleBeatCount(0);
    setIsPlaying(false);
    setHasAutoStarted(false);
    void loadBundledReplay();
  }

  function handleStartPlayback() {
    if (totalBeats === 0) {
      return;
    }
    setVisibleBeatCount(1);
    setIsPlaying(true);
  }

  function handleTogglePlayback() {
    if (totalBeats === 0) {
      return;
    }
    if (visibleBeatCount === 0) {
      setVisibleBeatCount(1);
      setIsPlaying(true);
      return;
    }
    setIsPlaying((current) => !current);
  }

  function handleResetPlayback() {
    setVisibleBeatCount(0);
    setIsPlaying(false);
  }

  function handleShowAll() {
    setVisibleBeatCount(0);
    setIsPlaying(false);
  }

  function handleSpeedChange(event: ChangeEvent<HTMLSelectElement>) {
    setPlaybackSpeedMs(Number(event.target.value));
  }

  function handleOpenDetails(beat: StoryBeat) {
    setDetailBeat(beat);
  }

  function handleCloseDetails() {
    setDetailBeat(null);
  }

  return (
    <main className="page-shell" id="main-content">
      <a className="skip-link" href="#story-stage">
        跳到会话主舞台
      </a>

      <section className="hero">
        <div className="hero-copy">
          <p className="section-eyebrow">AutoSolver Agent 展示页</p>
          <h1>把 AI Agent 的策略探索过程，做成一场外行也能看懂的会话演示</h1>
          <p className="hero-text">
            这一版不再像监控面板，而是更接近你给的 Kaggle Agent Session 示例：页面会把提案、工具调用、样例回传、判定和复盘串成一段连续动画，
            让观众像看直播一样理解 Agent 是怎么一步步找到更优策略的。
          </p>
          <div className="hero-chips">
            <span className="meta-chip">基准集 {summary?.benchmarkId ?? agent?.benchmarkId ?? "暂无"}</span>
            <span className="meta-chip">模型 {agent?.provider ?? "暂无"}</span>
            <span className="meta-chip">{agent?.llmEnabled ? "LLM 驱动" : "离线回退"}</span>
            <span className="meta-chip">{autoRefreshEnabled ? "本地实时联动" : "静态回放"}</span>
          </div>
        </div>
        <div className="hero-metrics">
          <MetricCard label="故事步骤" value={formatInteger(totalBeats)} detail="一场完整会话里，页面会展示的动作节点数。" tone="accent" />
          <MetricCard label="保留轮次" value={formatInteger(visibleKeepCount)} detail="这些轮次被自动 judge 认可为更优结果。" />
          <MetricCard label="淘汰轮次" value={formatInteger(visibleDiscardCount)} detail="这些尝试没赢，但会变成下一轮的经验。" />
          <MetricCard label="失败轮次" value={formatInteger(visibleFailureCount)} detail="异常不会打断会话，而会被系统记住。" tone="quiet" />
          <MetricCard label="最佳预计完单" value={formatCount(bestRound?.averageExpectedCompletedOrders ?? summary?.bestExpectedCompletedOrders)} detail="当前视图里最好的主目标成绩。" />
          <MetricCard label="对应总成本" value={formatCount(bestRound?.averageTotalCost ?? summary?.bestTotalCost)} detail="在最优完单结果下，对应的成本表现。" />
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
            <p className="section-eyebrow">走势</p>
            <h2>每一轮实验的成绩是如何变化的</h2>
          </div>
          <ScoreChart rounds={shownRounds} />
        </section>

        <section className="panel">
          <div className="panel-head">
            <p className="section-eyebrow">轮次摘要</p>
            <h2>每一轮到底改了什么</h2>
          </div>
          <div className="round-grid">
            {shownRounds.length > 0 ? (
              shownRounds.map((round) => <RoundCard key={round.experimentId} round={round} />)
            ) : (
              <div className="empty-state">播放还没走到完整结果，或者当前 replay 里没有轮次摘要。</div>
            )}
          </div>
        </section>
      </section>

      <section className="panel">
        <div className="panel-head">
          <p className="section-eyebrow">案例观察</p>
          <h2>哪些 benchmark case 最难</h2>
        </div>
        <div className="case-grid">
          {caseLeaderboard.length > 0 ? (
            caseLeaderboard.slice(0, 6).map((row, index) => (
              <CaseCard key={row.caseId ?? row.instanceId ?? `case-${index}`} row={row} />
            ))
          ) : (
            <div className="empty-state">当前 replay 还没有生成 case 排行数据。</div>
          )}
        </div>
      </section>

      <section className="panel">
        <div className="panel-head">
          <p className="section-eyebrow">给技术同学看</p>
          <h2>保留原始事件流，方便核对和排查</h2>
        </div>
        <DebugDrawer events={events} />
      </section>

      <DetailModal beat={detailBeat} onClose={handleCloseDetails} />
    </main>
  );
}

export default App;
