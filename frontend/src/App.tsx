import { useEffect, useMemo, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Activity, BarChart3, Briefcase, RefreshCw, ShieldCheck } from "lucide-react";
import { backfillJournalHistory, clearJournalHistory, closeJournalPosition, getDashboardBundle, getSession, login, logout, sendRuntimeCommand } from "./lib/api";
import { formatMoney, formatTimestamp, titleCase } from "./lib/format";
import type {
  DashboardBundle,
  StrategyActivityAnalytics,
  StrategyCandidateView,
  StrategyActivityCandle,
  StrategyActivityMarketSummary,
  StrategyActivityNamedCount,
  StrategyActivityPositionOverlay,
  StrategyActivityRankedSymbol,
  TradingModule,
  ViewId,
} from "./lib/types";
import { CandlestickChart } from "./components/CandlestickChart";
import { LoginScreen } from "./components/LoginScreen";
import { MarketModuleCard } from "./components/MarketModuleCard";
import { Panel } from "./components/Panel";
import { SettingsControlSurface } from "./components/SettingsControlSurface";
import { Sidebar } from "./components/Sidebar";
import { StatusBadge } from "./components/StatusBadge";

type ThemeMode = "dark" | "light";

const FALLBACK_BUILD_INFO = {
  version: "3",
  build_number: "---",
  build_label: "Build:---",
  update_source: {
    repo: "Unknown repository",
    branch: "main",
  },
};

const VIEW_COPY: Record<ViewId, { title: string; description: string }> = {
  dashboard: { title: "Dashboard", description: "Account summary and activity." },
  bots: { title: "Bots", description: "Choose how each market trades and how tightly risk is managed." },
  analytics: { title: "Analytics", description: "Performance, charts, and activity." },
  accounts: { title: "Journal", description: "Live positions and trade history." },
  settings: { title: "Settings", description: "Preferences, security, and updates." },
};

interface JournalTableRow {
  id: string;
  market: string;
  symbol: string;
  strategyId?: string | null;
  thesisId?: string | null;
  thesisState?: string | null;
  thesisReason?: string | null;
  thesisTransitionAt?: string | null;
  status: "open" | "closed";
  side: string;
  quantity: string;
  entryPrice: string | number | null | undefined;
  lastPrice: string | number | null | undefined;
  pnlValue: string | number | null | undefined;
  updatedAt: string | null | undefined;
  closeAvailable: boolean;
}

function pnlToneClass(value: string | number | null | undefined): string {
  const amount = Number(value);
  if (Number.isNaN(amount) || amount === 0) {
    return "journal-pnl-flat";
  }
  return amount > 0 ? "journal-pnl-positive" : "journal-pnl-negative";
}

function asArray<T>(value: T[] | undefined | null): T[] {
  return Array.isArray(value) ? value : [];
}

function isSameCandidate(
  left: { strategy_id?: string; setup_family?: string | null; side?: string | null; symbol?: string | null },
  right: { strategy_id?: string; setup_family?: string | null; side?: string | null; symbol?: string | null },
): boolean {
  return left.strategy_id === right.strategy_id
    && (left.setup_family || null) === (right.setup_family || null)
    && (left.side || null) === (right.side || null)
    && (left.symbol || null) === (right.symbol || null);
}

type FeedItem = {
  title: string;
  subtitle: string;
  meta: string[];
  details?: string[];
  tone?: "success" | "warning" | "danger" | "neutral";
};

function toneFromLevel(level?: string): "success" | "warning" | "danger" | "neutral" {
  const normalized = String(level || "").toLowerCase();
  if (normalized.includes("success") || normalized.includes("ready") || normalized.includes("active")) {
    return "success";
  }
  if (normalized.includes("error") || normalized.includes("danger") || normalized.includes("failure")) {
    return "danger";
  }
  if (normalized.includes("warning") || normalized.includes("attention") || normalized.includes("idle")) {
    return "warning";
  }
  return "neutral";
}

function loadView(): ViewId {
  const value = window.localStorage.getItem("omnibot.reactView");
  return value === "bots" || value === "analytics" || value === "accounts" || value === "settings" ? value : "dashboard";
}

function loadThemeMode(): ThemeMode {
  const value = window.localStorage.getItem("omnibot.themeMode");
  return value === "light" ? "light" : "dark";
}

function loadStoredBoolean(key: string, fallback: boolean): boolean {
  const value = window.localStorage.getItem(key);
  if (value === null) {
    return fallback;
  }
  return value === "true";
}

function OverviewMetrics({ bundle }: { bundle: DashboardBundle }) {
  const marketCount = bundle.runtime.markets?.length || 0;
  const runningMarkets = bundle.runtime.markets?.filter((market) => market.state === "RUNNING").length || 0;
  const attentionCount = bundle.health.market_reports?.filter((report) => !report.ready).length || 0;

  const items = [
    { label: "Active Markets", value: `${runningMarkets}/${marketCount || 0}`, detail: attentionCount === 0 ? "Ready" : `${attentionCount} need attention`, icon: Activity },
    { label: "Portfolio Value", value: formatMoney(bundle.portfolio.total_portfolio_value), detail: `${bundle.portfolio.snapshot_count || 0} snapshots`, icon: Briefcase },
    { label: "Open Exposure", value: formatMoney(bundle.portfolio.total_market_value), detail: `${bundle.journal.totals?.open_position_count || 0} open`, icon: BarChart3 },
    { label: "System Status", value: titleCase(bundle.runtime.state), detail: titleCase(bundle.ui_state.overall_state || "unknown"), icon: ShieldCheck },
  ];

  return (
    <div className="stat-grid">
      {items.map(({ label, value, detail, icon: Icon }) => (
        <article key={label} className="stat-card">
          <div className="stat-card-top">
            <span>{label}</span>
            <Icon size={16} />
          </div>
          <strong>{value}</strong>
          <small>{detail}</small>
        </article>
      ))}
    </div>
  );
}

function HealthList({ bundle }: { bundle: DashboardBundle }) {
  return (
    <div className="stack-list">
      {asArray(bundle.health.market_reports).map((report) => (
        <article key={report.market} className="stack-card">
          <div className="stack-card-top">
            <strong>{report.market} {report.state}</strong>
            <StatusBadge label={report.ready ? "ready" : "attention"} tone={report.ready ? "success" : "warning"} />
          </div>
          <p>{report.reason}</p>
        </article>
      ))}
    </div>
  );
}

function FeedList({ items }: { items: FeedItem[] }) {
  return (
    <div className="stack-list">
      {items.length === 0 ? <p className="empty-copy">No events recorded yet.</p> : null}
      {items.map((item, index) => (
        <article key={`${item.title}-${index}`} className={`feed-card feed-card-${item.tone || "neutral"}`}>
          <strong>{item.title}</strong>
          {item.subtitle ? <p>{item.subtitle}</p> : null}
          {asArray(item.details).length ? (
            <div className="feed-detail-list">
              {asArray(item.details).map((detail) => <span key={detail}>{detail}</span>)}
            </div>
          ) : null}
          <div className="chip-row">
            {item.meta.map((meta) => <span key={meta}>{meta}</span>)}
          </div>
        </article>
      ))}
    </div>
  );
}

function WidgetStrip({ bundle }: { bundle: DashboardBundle }) {
  const banner = bundle.ui_state.banner;
  const widgets = asArray(bundle.ui_state.widgets);

  return (
    <div className="signal-stack">
      {banner ? (
        <article className={`signal-banner signal-${toneFromLevel(banner.level)}`}>
          <div className="signal-banner-top">
            <StatusBadge label={banner.level || "status"} tone={toneFromLevel(banner.level)} />
          </div>
          <strong>{banner.title || "Status update"}</strong>
          <p>{banner.message || "No status message available."}</p>
        </article>
      ) : null}

      <div className="widget-grid">
        {widgets.map((widget) => (
          <article key={widget.widget_id || widget.title} className="support-card widget-card">
            <div className="stack-card-top">
              <span>{widget.title}</span>
              <StatusBadge label={widget.level} tone={toneFromLevel(widget.level)} />
            </div>
            <strong>{titleCase(widget.state)}</strong>
          </article>
        ))}
      </div>
    </div>
  );
}

function ChartGallery({ bundle }: { bundle: DashboardBundle }) {
  const charts = asArray(bundle.analytics.charts);

  return (
    <div className="chart-grid">
      {charts.map((chart) => {
        const points = asArray(chart.points);
        const maxValue = Math.max(...points.map((point) => Number(point.value) || 0), 0);

        return (
          <article key={chart.chart_id || chart.title} className="chart-card">
            <div className="stack-card-top">
              <strong>{chart.title}</strong>
              <span className="chart-unit">{titleCase(chart.unit || "value")}</span>
            </div>
            <div className="chart-bars">
              {points.map((point) => {
                const numericValue = Number(point.value) || 0;
                const width = maxValue > 0 ? Math.max((numericValue / maxValue) * 100, 4) : 4;

                return (
                  <div key={point.label} className="chart-row">
                    <span className="chart-label">{titleCase(point.label)}</span>
                    <div className="chart-track">
                      <div className="chart-fill" style={{ width: `${width}%` }} />
                    </div>
                    <strong className="chart-value">
                      {chart.unit === "currency" ? formatMoney(point.value) : String(point.value)}
                    </strong>
                  </div>
                );
              })}
            </div>
            <small>{chart.provenance?.aggregation || "Snapshot aggregation"}</small>
          </article>
        );
      })}
    </div>
  );
}

function Sparkline({ points }: { points: Array<{ label: string; value: string | number }> }) {
  const numericPoints = points.map((point) => Number(point.value) || 0);
  if (numericPoints.length < 2) {
    return <div className="sparkline-empty">Awaiting series data.</div>;
  }

  const maxValue = Math.max(...numericPoints);
  const minValue = Math.min(...numericPoints);
  const range = maxValue - minValue || 1;
  const path = numericPoints.map((value, index) => {
    const x = (index / Math.max(numericPoints.length - 1, 1)) * 100;
    const y = 100 - (((value - minValue) / range) * 84 + 8);
    return `${index === 0 ? "M" : "L"}${x},${y}`;
  }).join(" ");

  return (
    <div className="sparkline-shell">
      <svg className="sparkline-svg" viewBox="0 0 100 100" preserveAspectRatio="none" aria-hidden="true">
        <path d={path} className="sparkline-path" />
      </svg>
      <div className="sparkline-meta">
        <span>{points[0]?.label}</span>
        <strong>{String(points.at(-1)?.value ?? "-")}</strong>
      </div>
    </div>
  );
}

function MarketScannerCard({ summary, themeMode }: { summary: StrategyActivityMarketSummary; themeMode: ThemeMode }) {
  const rankedSymbols = asArray<StrategyActivityRankedSymbol>(summary.ranked_symbols);
  const candlesBySymbol = summary.candles_by_symbol || {};
  const positionOverlaysBySymbol = summary.position_overlays_by_symbol || {};
  const availableSymbols = asArray<string>(summary.available_symbols).length
    ? asArray<string>(summary.available_symbols)
    : Object.keys(candlesBySymbol);
  const defaultSymbol = summary.top_symbol
    || availableSymbols.find((symbol) => asArray(candlesBySymbol[symbol]).length > 0)
    || availableSymbols[0]
    || "";
  const [selectedSymbol, setSelectedSymbol] = useState(defaultSymbol);

  useEffect(() => {
    if (!selectedSymbol || !availableSymbols.includes(selectedSymbol)) {
      setSelectedSymbol(defaultSymbol);
    }
  }, [availableSymbols, defaultSymbol, selectedSymbol]);

  const selectedCandles = selectedSymbol ? asArray<StrategyActivityCandle>(candlesBySymbol[selectedSymbol]) : [];
  const selectedOverlays = selectedSymbol ? asArray<StrategyActivityPositionOverlay>(positionOverlaysBySymbol[selectedSymbol]) : [];
  const selectedCandidate = summary.selected_candidate;
  const selectedThesis = summary.selected_thesis;
  const candidateEvidence = asArray(selectedCandidate?.evidence).slice(0, 3);
  const consideredCandidates = asArray(summary.considered_candidates).slice(0, 3);

  return (
    <article className="series-card">
      <div className="stack-card-top">
        <strong>{summary.label || titleCase(summary.market)}</strong>
        <StatusBadge label={summary.warmup_status || "unknown"} tone={toneFromLevel(summary.warmup_status || "warning")} />
      </div>
      <p>{summary.last_decision || "No recent activity yet."}</p>
      {selectedCandidate ? (
        <div className="series-thesis-card">
          {selectedThesis?.thesis_id ? <small>{selectedThesis.thesis_id}</small> : null}
          <div className="module-thesis-chip-row">
            <span>{titleCase(selectedCandidate.strategy_id)}</span>
            {selectedCandidate.setup_family ? <span>{titleCase(selectedCandidate.setup_family)}</span> : null}
            {selectedCandidate.regime ? <span>{titleCase(selectedCandidate.regime)}</span> : null}
            {selectedCandidate.side ? <span>{titleCase(selectedCandidate.side)}</span> : null}
            {summary.candidate_count ? <span>{summary.candidate_count} candidate{summary.candidate_count === 1 ? "" : "s"}</span> : null}
            {summary.candidate_score ? <span>Score {summary.candidate_score}</span> : null}
            {selectedThesis?.lifecycle_state ? <span>{titleCase(selectedThesis.lifecycle_state)}</span> : null}
            {selectedThesis?.scale_out_count && selectedThesis?.scale_out_stage_total ? (
              <span>Scale {selectedThesis.scale_out_count}/{selectedThesis.scale_out_stage_total}</span>
            ) : null}
          </div>
          {selectedCandidate.summary ? <p>{selectedCandidate.summary}</p> : null}
          {selectedThesis?.lifecycle_reason ? <small>{selectedThesis.lifecycle_reason}</small> : null}
          {selectedThesis?.remaining_quantity || selectedThesis?.next_scale_out_trigger_price ? (
            <div className="module-decision-details">
              {selectedThesis.remaining_quantity ? <small>Remaining {selectedThesis.remaining_quantity}</small> : null}
              {selectedThesis.next_scale_out_trigger_price ? <small>Next scale trigger {selectedThesis.next_scale_out_trigger_price}</small> : null}
            </div>
          ) : null}
          {candidateEvidence.length ? (
            <div className="module-decision-details">
              {candidateEvidence.map((detail) => <small key={detail}>{detail}</small>)}
            </div>
          ) : null}
          {consideredCandidates.length > 1 ? (
            <div className="module-alternative-list">
              <small>Alternatives</small>
              <div className="module-thesis-chip-row">
                {consideredCandidates
                  .filter((candidate) => !isSameCandidate(candidate, selectedCandidate))
                  .map((candidate) => (
                    <span key={`${candidate.strategy_id}-${candidate.score || "na"}`}>
                      {titleCase(candidate.strategy_id)}{candidate.score ? ` (${candidate.score})` : ""}
                    </span>
                  ))}
              </div>
            </div>
          ) : null}
        </div>
      ) : null}
      <div className="series-toolbar">
        <label className="series-select-label">
          <span>Symbol</span>
          <select
            value={selectedSymbol}
            onChange={(event) => setSelectedSymbol(event.target.value)}
            disabled={availableSymbols.length === 0}
          >
            {availableSymbols.map((symbol) => (
              <option key={symbol} value={symbol}>{symbol}</option>
            ))}
          </select>
        </label>
        <small>{summary.candle_timeframe || "5m"} timeframe</small>
      </div>
      {selectedSymbol && selectedCandles.length ? (
        <CandlestickChart symbol={selectedSymbol} timeframe={summary.candle_timeframe} candles={selectedCandles} overlays={selectedOverlays} themeMode={themeMode} />
      ) : (
        <div className="sparkline-empty">No historical candles yet.</div>
      )}
      <div className="ranking-list" aria-label={`${summary.label || summary.market} rankings`}>
        {rankedSymbols.length === 0 ? <span className="empty-copy">No ranked symbols yet.</span> : null}
        {rankedSymbols.map((item) => (
          <div key={item.symbol} className="ranking-row">
            <div>
              <strong>{item.symbol}</strong>
              <small>{formatMoney(item.latest_price)}</small>
            </div>
            <div className="ranking-row-right">
              <span className="ranking-score">{Number(item.score || 0).toFixed(3)}</span>
              <small>{`${(Number(item.momentum_ratio || 0) * 100).toFixed(2)}%`}</small>
            </div>
          </div>
        ))}
      </div>
    </article>
  );
}

function ScannerInsights({ bundle, themeMode }: { bundle: DashboardBundle; themeMode: ThemeMode }) {
  const summaries = asArray(bundle.strategy_activity.market_summaries);

  return (
    <>
      <ScannerAnalyticsOverview analytics={bundle.strategy_activity.analytics} />
      <div className="series-grid">
        {summaries.map((summary) => <MarketScannerCard key={summary.market} summary={summary} themeMode={themeMode} />)}
      </div>
    </>
  );
}

function KeyValueGrid({ entries }: { entries: Array<[string, unknown]> }) {
  return (
    <div className="key-value-grid">
      {entries.map(([key, value]) => (
        <article key={key} className="support-card">
          <span>{titleCase(key)}</span>
          <strong>{String(value ?? "-")}</strong>
        </article>
      ))}
    </div>
  );
}

function NamedCountChips({ title, items }: { title: string; items: StrategyActivityNamedCount[] }) {
  if (!items.length) {
    return null;
  }

  return (
    <article className="support-card">
      <span>{title}</span>
      <div className="module-thesis-chip-row">
        {items.map((item) => (
          <span key={`${title}-${item.name}`}>
            {titleCase(item.name.replaceAll("-", " "))} ({item.count})
          </span>
        ))}
      </div>
    </article>
  );
}

function ScannerAnalyticsOverview({ analytics }: { analytics?: StrategyActivityAnalytics }) {
  if (!analytics) {
    return null;
  }

  const eventCounts = Object.entries(analytics.event_counts || {}).sort((left, right) => right[1] - left[1]).slice(0, 5);
  const driftFlags = asArray(analytics.drift_flags);
  const marketRollups = asArray(analytics.market_rollups).slice(0, 3);

  return (
    <>
      <KeyValueGrid
        entries={[
          ["Recent Events", analytics.recent_event_count ?? 0],
          ["Signals Seen", analytics.signal_count ?? 0],
          ["Orders Submitted", analytics.order_count ?? 0],
          ["Brakes Triggered", analytics.brake_count ?? 0],
          ["Ready Markets", analytics.ready_market_count ?? 0],
          ["Active Scanners", analytics.active_scanner_count ?? 0],
        ]}
      />
      <div className="series-grid">
        <NamedCountChips title="Strategy Mix" items={asArray(analytics.strategy_mix)} />
        <NamedCountChips title="Regime Mix" items={asArray(analytics.regime_mix)} />
        <NamedCountChips title="Lifecycle Mix" items={asArray(analytics.lifecycle_mix)} />
        <NamedCountChips title="Brake Reasons" items={asArray(analytics.brake_reasons)} />
        {driftFlags.length ? (
          <article className="support-card">
            <span>Drift Flags</span>
            <div className="module-decision-details">
              {driftFlags.map((flag) => (
                <small key={`${flag.market}-${flag.state}`}>
                  {titleCase(flag.market)}: {titleCase(flag.state.replaceAll("-", " "))} - {flag.reason}
                </small>
              ))}
            </div>
          </article>
        ) : null}
        {eventCounts.length ? (
          <article className="support-card">
            <span>Event Types</span>
            <div className="module-thesis-chip-row">
              {eventCounts.map(([name, count]) => (
                <span key={name}>{titleCase(name.replaceAll("-", " "))} ({count})</span>
              ))}
            </div>
          </article>
        ) : null}
      </div>
      {marketRollups.length ? (
        <div className="series-grid">
          {marketRollups.map((rollup) => (
            <article key={rollup.market} className="support-card">
              <div className="stack-card-top">
                <strong>{rollup.label || titleCase(rollup.market)}</strong>
                <StatusBadge label={rollup.warmup_status || "unknown"} tone={toneFromLevel(rollup.warmup_status || "warning")} />
              </div>
              <p>{rollup.last_decision || "No recent decision available."}</p>
              <div className="module-thesis-chip-row">
                {rollup.execution_mode ? <span>{titleCase(rollup.execution_mode)}</span> : null}
                {rollup.top_symbol ? <span>{rollup.top_symbol}</span> : null}
                {rollup.top_strategy ? <span>{titleCase(rollup.top_strategy)}</span> : null}
                {rollup.top_regime ? <span>{titleCase(rollup.top_regime)}</span> : null}
                {rollup.drift_state ? <span>{titleCase(rollup.drift_state)}</span> : null}
                {rollup.lifecycle_state ? <span>{titleCase(rollup.lifecycle_state)}</span> : null}
              </div>
              {rollup.drift_reason ? <small>{rollup.drift_reason}</small> : null}
              <div className="module-decision-details">
                <small>Signals {rollup.signals_seen ?? 0}</small>
                <small>Orders {rollup.orders_submitted ?? 0}</small>
                <small>Recent brakes {rollup.recent_brake_count ?? 0}</small>
                <small>Execution blocks {rollup.recent_execution_block_count ?? 0}</small>
                <small>Risk rejections {rollup.recent_risk_rejection_count ?? 0}</small>
              </div>
            </article>
          ))}
        </div>
      ) : null}
    </>
  );
}

function normalizeFeedItems(items: unknown[] | undefined): FeedItem[] {
  return asArray(items).map((item, index) => {
    const event = (item && typeof item === "object" ? item : {}) as Record<string, unknown>;
    const title = typeof event.title === "string" ? event.title : null;
    const subtitle = typeof event.subtitle === "string" ? event.subtitle : null;
    const meta = Array.isArray(event.meta) ? event.meta.map((value) => String(value)) : [];
    const actorId = typeof event.actor_id === "string" ? event.actor_id : null;
    const outcome = typeof event.outcome === "string" ? event.outcome : null;
    const mechanism = typeof event.mechanism === "string" ? event.mechanism : null;
    const occurredAt = typeof event.occurred_at === "string" ? event.occurred_at : null;
    const market = typeof event.market === "string" ? event.market : null;
    const message = typeof event.message === "string" ? event.message : null;
    const eventType = typeof event.event_type === "string" ? event.event_type : null;
    const symbol = typeof event.symbol === "string" ? event.symbol : null;
    const strategyId = typeof event.strategy_id === "string" ? event.strategy_id : null;
    const profileId = typeof event.profile_id === "string" ? event.profile_id : null;
    const price = typeof event.price === "string" ? event.price : null;
    const level = typeof event.level === "string" ? event.level : null;
    const details = Array.isArray(event.details) ? event.details.map((value) => String(value)).filter(Boolean) : [];
    const selectedCandidate = event.selected_candidate && typeof event.selected_candidate === "object"
      ? event.selected_candidate as StrategyCandidateView
      : null;
    const candidateCount = typeof event.candidate_count === "number" ? event.candidate_count : null;
    const candidateScore = typeof event.candidate_score === "string" ? event.candidate_score : null;

    if (title && subtitle) {
      return {
        title,
        subtitle,
        meta,
        details,
        tone: toneFromLevel(level || "neutral"),
      };
    }

    if (actorId || outcome || mechanism) {
      return {
        title: `${actorId || "User"} ${titleCase(outcome || "event")}`,
        subtitle: `${titleCase(mechanism || "session")} sign-in activity`,
        meta: [formatTimestamp(occurredAt)].filter((value) => value !== "-"),
        tone: toneFromLevel(level || "neutral"),
      };
    }

    if (market || message || eventType) {
      const strategyMeta = [
        market ? `Market: ${titleCase(market)}` : null,
        symbol ? `Symbol: ${symbol}` : null,
        strategyId ? `Strategy: ${strategyId}` : null,
        profileId ? `Profile: ${profileId}` : null,
        selectedCandidate?.setup_family ? `Setup: ${titleCase(selectedCandidate.setup_family)}` : null,
        selectedCandidate?.side ? `Side: ${titleCase(selectedCandidate.side)}` : null,
        candidateCount ? `Candidates: ${candidateCount}` : null,
        candidateScore ? `Score: ${candidateScore}` : null,
        price ? `Price: ${price}` : null,
        formatTimestamp(occurredAt) !== "-" ? formatTimestamp(occurredAt) : null,
      ].filter((value): value is string => Boolean(value));
      return {
        title: titleCase(eventType || market || `event ${index + 1}`),
        subtitle: selectedCandidate?.summary || String(message || market || "Activity update"),
        meta: strategyMeta.length
          ? strategyMeta
          : Object.entries(event)
              .filter(([key, value]) => !["event_type", "message", "details", "level"].includes(key) && value !== null && value !== undefined && value !== "")
              .slice(0, 3)
              .map(([key, value]) => `${titleCase(key)}: ${String(value)}`),
        details,
        tone: toneFromLevel(level || eventType || "neutral"),
      };
    }

    return {
      title: `Event ${index + 1}`,
      subtitle: "No formatted event copy available.",
      meta: [],
      details,
      tone: toneFromLevel(level || "neutral"),
    };
  });
}

function applyOptimisticMarketCommand(
  bundle: DashboardBundle | undefined,
  marketName: string,
  command: "start-market" | "stop-market",
): DashboardBundle | undefined {
  if (!bundle) {
    return bundle;
  }

  const nextRuntimeState = command === "start-market" ? "RUNNING" : "IDLE";
  const nextAutomationState = command === "start-market" ? "actively-scanning" : "connected-only";
  const nextDecision = command === "start-market" ? "Scanner started." : "Scanner stopped.";

  return {
    ...bundle,
    runtime: {
      ...bundle.runtime,
      markets: asArray(bundle.runtime.markets).map((market) => (
        market.market === marketName ? { ...market, state: nextRuntimeState } : market
      )),
    },
    modules: {
      ...bundle.modules,
      modules: asArray(bundle.modules.modules).map((module) => (
        module.market === marketName
          ? {
              ...module,
              automation_state: nextAutomationState,
              last_decision: nextDecision,
              status_message: nextDecision,
            }
          : module
      )),
    },
  };
}

function mergeUpdatedModule(
  bundle: DashboardBundle | undefined,
  updatedModule: TradingModule,
): DashboardBundle | undefined {
  if (!bundle) {
    return bundle;
  }

  return {
    ...bundle,
    modules: {
      ...bundle.modules,
      modules: asArray(bundle.modules.modules).map((module) => (
        module.market === updatedModule.market ? { ...module, ...updatedModule } : module
      )),
    },
  };
}

function DashboardContent({
  view,
  bundle,
  csrfToken,
  onRefresh,
  themeMode,
}: {
  view: ViewId;
  bundle: DashboardBundle;
  csrfToken: string;
  onRefresh: () => void;
  themeMode: ThemeMode;
}) {
  const buildInfo = bundle.build || FALLBACK_BUILD_INFO;
  const queryClient = useQueryClient();
  const [commandFeedback, setCommandFeedback] = useState<string | null>(null);
  const [pendingCommands, setPendingCommands] = useState<Record<string, "start-market" | "stop-market" | undefined>>({});
  const [recentDecisionsExpanded, setRecentDecisionsExpanded] = useState(() => loadStoredBoolean("omnibot.analytics.recentDecisionsExpanded.v2", false));
  const [rankedMarketsExpanded, setRankedMarketsExpanded] = useState(() => loadStoredBoolean("omnibot.analytics.rankedMarketsExpanded", true));

  const modulesByMarket = useMemo(
    () => new Map(asArray(bundle.modules.modules).map((module) => [module.market, module])),
    [bundle.modules.modules],
  );
  const strategyEventsByMarket = useMemo(() => {
    const grouped = new Map<string, Array<typeof bundle.strategy_activity.events[number]>>();
    for (const event of asArray(bundle.strategy_activity.events)) {
      if (!event?.market) {
        continue;
      }
      const current = grouped.get(event.market) || [];
      current.push(event);
      grouped.set(event.market, current);
    }
    return grouped;
  }, [bundle.strategy_activity.events]);

  const commandMutation = useMutation({
    mutationFn: ({ market, command }: { market: string; command: "start-market" | "stop-market" }) => sendRuntimeCommand(csrfToken, market, command),
    onMutate: ({ market, command }) => {
      const previousBundle = queryClient.getQueryData<DashboardBundle>(["dashboard-bundle"]);
      setPendingCommands((current) => ({ ...current, [market]: command }));
      setCommandFeedback(`${titleCase(market)} ${command === "start-market" ? "start" : "stop"} requested.`);
      queryClient.setQueryData<DashboardBundle | undefined>(["dashboard-bundle"], (current) => applyOptimisticMarketCommand(current, market, command));
      return { previousBundle };
    },
    onSuccess: async (_payload, variables) => {
      setCommandFeedback(`${titleCase(variables.market)} ${variables.command === "start-market" ? "start" : "stop"} command sent.`);
      await queryClient.invalidateQueries({ queryKey: ["dashboard-bundle"] });
      await queryClient.refetchQueries({ queryKey: ["dashboard-bundle"], type: "active" });
    },
    onError: (error, variables, context) => {
      if (context?.previousBundle) {
        queryClient.setQueryData(["dashboard-bundle"], context.previousBundle);
      }
      setPendingCommands((current) => ({ ...current, [variables.market]: undefined }));
      setCommandFeedback(`${titleCase(variables.market)} ${variables.command === "start-market" ? "start" : "stop"} failed: ${error.message}`);
    },
  });

  const [closingRowId, setClosingRowId] = useState<string | null>(null);

  const closePositionMutation = useMutation({
    mutationFn: ({ market, symbol }: { market: string; symbol: string }) => closeJournalPosition(csrfToken, market, symbol),
    onMutate: ({ market, symbol }) => {
      setClosingRowId(`${market}-${symbol}`);
    },
    onSuccess: () => void queryClient.invalidateQueries({ queryKey: ["dashboard-bundle"] }),
    onSettled: () => {
      setClosingRowId(null);
    },
  });

  const backfillHistoryMutation = useMutation({
    mutationFn: () => backfillJournalHistory(csrfToken, { market: "crypto", limit: 200 }),
    onMutate: () => {
      setCommandFeedback("Crypto broker history backfill requested.");
    },
    onSuccess: async (payload) => {
      setCommandFeedback(payload.message || "Broker history backfill completed.");
      await queryClient.invalidateQueries({ queryKey: ["dashboard-bundle"] });
      await queryClient.refetchQueries({ queryKey: ["dashboard-bundle"], type: "active" });
    },
    onError: (error) => {
      setCommandFeedback(`Broker history backfill failed: ${error.message}`);
    },
  });

  const clearHistoryMutation = useMutation({
    mutationFn: () => clearJournalHistory(csrfToken),
    onMutate: () => {
      setCommandFeedback("Clearing closed-trade table history.");
    },
    onSuccess: async (payload) => {
      setCommandFeedback(payload.message || "Closed-trade table history cleared.");
      await queryClient.invalidateQueries({ queryKey: ["dashboard-bundle"] });
      await queryClient.refetchQueries({ queryKey: ["dashboard-bundle"], type: "active" });
    },
    onError: (error) => {
      setCommandFeedback(`Clearing closed-trade table history failed: ${error.message}`);
    },
  });

  useEffect(() => {
    if (!commandFeedback) {
      return undefined;
    }
    const handle = window.setTimeout(() => setCommandFeedback(null), 5000);
    return () => window.clearTimeout(handle);
  }, [commandFeedback]);

  useEffect(() => {
    const marketStates = new Map(asArray(bundle.runtime.markets).map((market) => [market.market, market.state]));
    setPendingCommands((current) => {
      let changed = false;
      const next = { ...current };
      for (const [market, command] of Object.entries(current)) {
        if (!command) {
          continue;
        }
        const state = marketStates.get(market);
        if ((command === "start-market" && state === "RUNNING") || (command === "stop-market" && state && state !== "RUNNING")) {
          next[market] = undefined;
          changed = true;
        }
      }
      return changed ? next : current;
    });
  }, [bundle.runtime.markets]);

  useEffect(() => {
    window.localStorage.setItem("omnibot.analytics.recentDecisionsExpanded.v2", String(recentDecisionsExpanded));
  }, [recentDecisionsExpanded]);

  useEffect(() => {
    window.localStorage.setItem("omnibot.analytics.rankedMarketsExpanded", String(rankedMarketsExpanded));
  }, [rankedMarketsExpanded]);

  const marketsPanel = (
    <Panel
      eyebrow="Markets"
      title="Manage markets"
    >
      <div className="module-list">
        {asArray(bundle.runtime.markets).map((market) => (
          <MarketModuleCard
            key={market.market}
            market={market}
            module={modulesByMarket.get(market.market)}
            recentEvents={strategyEventsByMarket.get(market.market)}
            pendingCommand={pendingCommands[market.market]}
            onCommand={(target, command) => commandMutation.mutate({ market: target, command })}
          />
        ))}
      </div>
    </Panel>
  );

  const dashboardPage = (
    <>
      {commandFeedback ? (
        <div className="command-toast" role="status" aria-live="polite">{commandFeedback}</div>
      ) : null}
      <Panel eyebrow="Status" title="At a glance">
        <WidgetStrip bundle={bundle} />
      </Panel>
      <Panel
        eyebrow="Overview"
        title="Overview"
      >
        <OverviewMetrics bundle={bundle} />
      </Panel>
      <div className="two-column-grid">
        <Panel eyebrow="Portfolio" title="Snapshot totals" note={<p>{formatTimestamp(bundle.portfolio.generated_at || null)}</p>}>
          <div className="stat-grid stat-grid-compact">
            <article className="stat-card"><span>Total Value</span><strong>{formatMoney(bundle.portfolio.total_portfolio_value)}</strong></article>
            <article className="stat-card"><span>Equity</span><strong>{formatMoney(bundle.portfolio.total_equity)}</strong></article>
            <article className="stat-card"><span>Cash</span><strong>{formatMoney(bundle.portfolio.total_cash)}</strong></article>
            <article className="stat-card"><span>Buying Power</span><strong>{formatMoney(bundle.portfolio.total_buying_power)}</strong></article>
          </div>
        </Panel>
        <Panel eyebrow="System" title="System status">
          <HealthList bundle={bundle} />
        </Panel>
      </div>
    </>
  );

  const analyticsPage = (
    <div className="two-column-grid">
      <Panel eyebrow="Analytics" title="Stats">
        <div className="stat-grid stat-grid-compact">
          {asArray(bundle.analytics.stats).map((stat) => (
            <article key={stat.label} className="stat-card">
              <span>{stat.label}</span>
              <strong>{stat.unit === "currency" ? formatMoney(stat.value) : String(stat.value ?? "-")}</strong>
            </article>
          ))}
        </div>
      </Panel>
      <Panel eyebrow="Sessions" title="Market hours">
        <div className="stack-list">
          {asArray(bundle.market_hours.markets).map((item) => (
            <article key={item.market} className="stack-card">
              <div className="stack-card-top">
                <strong>{item.label || titleCase(item.market)}</strong>
                <StatusBadge
                  label={String(item.status || item.state || (item.is_open ? "open" : "closed"))}
                  tone={String(item.status || item.state || "").toLowerCase().includes("open") || item.is_open ? "success" : "warning"}
                />
              </div>
              <p>{item.detail}</p>
            </article>
          ))}
        </div>
      </Panel>
      <Panel
        eyebrow="Insights"
        title="Ranked markets"
        className="panel-span-full analytics-signals-panel"
        actions={(
          <button
            type="button"
            className="utility-button analytics-toggle-button"
            aria-expanded={rankedMarketsExpanded}
            onClick={() => setRankedMarketsExpanded((current) => !current)}
          >
            {rankedMarketsExpanded ? "Collapse" : "Expand"}
          </button>
        )}
      >
        <div hidden={!rankedMarketsExpanded}>
              <ScannerInsights bundle={bundle} themeMode={themeMode} />
        </div>
      </Panel>
      <Panel eyebrow="Charts" title="By market">
        <ChartGallery bundle={bundle} />
      </Panel>
      <Panel eyebrow="Activity" title="Recent events">
        <FeedList items={normalizeFeedItems(bundle.runtime_audit.events)} />
      </Panel>
      <Panel
        eyebrow="Insights"
        title="Recent decisions"
        className="analytics-decisions-panel panel-span-full"
        bodyClassName={recentDecisionsExpanded ? "panel-body-scrollable" : undefined}
        actions={(
          <button
            type="button"
            className="utility-button analytics-toggle-button"
            aria-expanded={recentDecisionsExpanded}
            onClick={() => setRecentDecisionsExpanded((current) => !current)}
          >
            {recentDecisionsExpanded ? "Collapse" : "Expand"}
          </button>
        )}
      >
        <div hidden={!recentDecisionsExpanded}>
          <FeedList items={normalizeFeedItems(bundle.strategy_activity.events)} />
        </div>
      </Panel>
    </div>
  );

  const botsPage = (
    <>
      {commandFeedback ? (
        <div className="command-toast" role="status" aria-live="polite">{commandFeedback}</div>
      ) : null}
      <Panel
        eyebrow="Bots"
        title="Run each market with the auto engine"
        note={<p>The bot should scan for opportunities, apply whichever internal setup fits the symbol and regime, and manage several different theses at the same time. This view now focuses on runtime state, guardrails, and recent decisions instead of fixed strategy labels.</p>}
      >
        <div className="module-list module-list-bots">
          {asArray(bundle.runtime.markets).map((market) => (
            <MarketModuleCard
              key={market.market}
              market={market}
              module={modulesByMarket.get(market.market)}
              recentEvents={strategyEventsByMarket.get(market.market)}
              pendingCommand={pendingCommands[market.market]}
              onCommand={(target, command) => commandMutation.mutate({ market: target, command })}
            />
          ))}
        </div>
      </Panel>
    </>
  );

  const openJournalRows: JournalTableRow[] = asArray(bundle.journal.open_positions).map((position) => ({
      id: `${position.market}-${position.symbol}`,
      market: position.market,
      symbol: position.symbol,
      strategyId: position.strategy_id,
      thesisId: position.thesis_id,
      thesisState: position.thesis_state,
      thesisReason: position.thesis_reason,
      thesisTransitionAt: position.thesis_transition_at,
      status: "open" as const,
      side: titleCase(position.side || "-"),
      quantity: String(position.quantity ?? "-"),
      entryPrice: position.entry_price,
      lastPrice: position.market_price ?? position.current_price,
      pnlValue: position.unrealized_pnl,
      updatedAt: position.updated_at || position.opened_at || null,
      closeAvailable: Boolean(position.close_available),
    }))
    .sort((left, right) => new Date(String(right.updatedAt || 0)).getTime() - new Date(String(left.updatedAt || 0)).getTime());

  const closedJournalRows: JournalTableRow[] = asArray(bundle.journal.closed_trades).map((trade, index) => ({
      id: trade.trade_id || `${trade.market}-${trade.symbol}-${index}`,
      market: trade.market,
      symbol: trade.symbol,
      strategyId: trade.strategy_id,
      thesisId: trade.thesis_id,
      thesisState: trade.thesis_state,
      thesisReason: trade.thesis_reason,
      thesisTransitionAt: trade.thesis_transition_at,
      status: "closed" as const,
      side: titleCase(trade.side || "-"),
      quantity: String(trade.quantity ?? "-"),
      entryPrice: trade.entry_price,
      lastPrice: trade.exit_price,
      pnlValue: trade.realized_pnl,
      updatedAt: trade.closed_at || trade.opened_at || null,
      closeAvailable: false,
    }))
    .sort((left, right) => new Date(String(right.updatedAt || 0)).getTime() - new Date(String(left.updatedAt || 0)).getTime());

  const accountsPage = (
    <>
      <Panel eyebrow="Trade Journal" title="Live positions" note={<p>{formatTimestamp(bundle.journal.generated_at || null)}</p>}>
        <div className="journal-summary-strip" aria-label="Journal summary">
          <span className="journal-summary-pill"><strong>{bundle.journal.totals?.open_position_count || 0}</strong> open</span>
          <span className="journal-summary-pill"><strong>{bundle.journal.totals?.closed_trade_count || asArray(bundle.journal.closed_trades).length}</strong> closed</span>
          <span className="journal-summary-pill"><strong className={pnlToneClass(bundle.journal.totals?.total_unrealized_pnl)}>{formatMoney(bundle.journal.totals?.total_unrealized_pnl)}</strong> open PnL</span>
          <span className="journal-summary-pill"><strong className={pnlToneClass(bundle.journal.totals?.total_realized_pnl)}>{formatMoney(bundle.journal.totals?.total_realized_pnl)}</strong> closed PnL</span>
        </div>

        <div className="table-shell journal-table-shell journal-table-shell-wide">
          <div className="journal-table-heading">
            <strong>Open positions</strong>
            <small>{openJournalRows.length} rows</small>
          </div>
          <div className="journal-table-scroll">
            <table className="journal-mixed-table">
              <thead>
                <tr><th>Status</th><th>Market</th><th>Symbol</th><th>Side</th><th>Qty</th><th>Entry</th><th>Last</th><th>PnL</th><th>Updated</th><th>Action</th></tr>
              </thead>
              <tbody>
                {openJournalRows.length === 0 ? (
                  <tr><td colSpan={10} className="empty-cell">No open positions right now.</td></tr>
                ) : openJournalRows.map((row) => {
                  const rowKey = `${row.market}-${row.symbol}`;
                  const isClosing = closingRowId === rowKey && closePositionMutation.isPending;
                  return (
                    <tr key={row.id} className="journal-row-open">
                      <td><StatusBadge label={row.status} tone="success" /></td>
                      <td>{titleCase(row.market)}</td>
                      <td>
                        <div>{row.symbol}</div>
                        {row.strategyId ? <small>{titleCase(row.strategyId)}</small> : null}
                        {row.thesisState ? <small>{titleCase(row.thesisState)}</small> : null}
                        {row.thesisReason ? <small>{row.thesisReason}</small> : null}
                      </td>
                      <td>{row.side}</td>
                      <td>{row.quantity}</td>
                      <td>{formatMoney(row.entryPrice)}</td>
                      <td>{formatMoney(row.lastPrice)}</td>
                      <td className={pnlToneClass(row.pnlValue)}>{formatMoney(row.pnlValue)}</td>
                      <td>{formatTimestamp(row.updatedAt || null)}</td>
                      <td>
                        <button
                          type="button"
                          className="utility-button journal-close-button"
                          disabled={!row.closeAvailable || isClosing}
                          onClick={() => closePositionMutation.mutate({ market: row.market, symbol: row.symbol })}
                        >
                          {isClosing ? "Closing" : "Close"}
                        </button>
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        </div>
      </Panel>

      <Panel
        eyebrow="Trade Journal"
        title="Closed trades"
        note={<p>{formatTimestamp(bundle.journal.generated_at || null)}</p>}
        actions={(
          <div className="journal-actions-row">
            <button
              type="button"
              className="utility-button"
              disabled={backfillHistoryMutation.isPending || clearHistoryMutation.isPending}
              onClick={() => backfillHistoryMutation.mutate()}
            >
              <RefreshCw size={14} />
              {backfillHistoryMutation.isPending ? "Backfilling" : "Backfill history"}
            </button>
            <button
              type="button"
              className="utility-button"
              disabled={closedJournalRows.length === 0 || clearHistoryMutation.isPending || backfillHistoryMutation.isPending}
              onClick={() => clearHistoryMutation.mutate()}
            >
              {clearHistoryMutation.isPending ? "Clearing" : "Clear history"}
            </button>
          </div>
        )}
      >
        <div className="journal-summary-strip" aria-label="Closed trades summary">
          <span className="journal-summary-pill"><strong>{closedJournalRows.length}</strong> settled</span>
          <span className="journal-summary-pill"><strong className={pnlToneClass(bundle.journal.totals?.today_realized_pnl)}>{formatMoney(bundle.journal.totals?.today_realized_pnl)}</strong> today PnL</span>
          <span className="journal-summary-pill"><strong className={pnlToneClass(bundle.journal.totals?.yesterday_realized_pnl)}>{formatMoney(bundle.journal.totals?.yesterday_realized_pnl)}</strong> yesterday PnL</span>
        </div>
        <div className="table-shell journal-table-shell journal-table-shell-wide">
          <div className="journal-table-heading">
            <strong>Settled trade history</strong>
            <small>{closedJournalRows.length} rows</small>
          </div>
          <div className="journal-table-scroll">
            <table className="journal-mixed-table">
              <thead>
                <tr><th>Status</th><th>Market</th><th>Symbol</th><th>Side</th><th>Qty</th><th>Entry</th><th>Exit</th><th>PnL</th><th>Closed</th><th>Action</th></tr>
              </thead>
              <tbody>
                {closedJournalRows.length === 0 ? (
                  <tr><td colSpan={10} className="empty-cell">No closed trades recorded yet.</td></tr>
                ) : closedJournalRows.map((row) => (
                  <tr key={row.id} className="journal-row-closed">
                    <td><StatusBadge label={row.status} tone="neutral" /></td>
                    <td>{titleCase(row.market)}</td>
                      <td>
                        <div>{row.symbol}</div>
                        {row.strategyId ? <small>{titleCase(row.strategyId)}</small> : null}
                        {row.thesisState ? <small>{titleCase(row.thesisState)}</small> : null}
                        {row.thesisReason ? <small>{row.thesisReason}</small> : null}
                      </td>
                    <td>{row.side}</td>
                    <td>{row.quantity}</td>
                    <td>{formatMoney(row.entryPrice)}</td>
                    <td>{formatMoney(row.lastPrice)}</td>
                    <td className={pnlToneClass(row.pnlValue)}>{formatMoney(row.pnlValue)}</td>
                    <td>{formatTimestamp(row.updatedAt || null)}</td>
                    <td><span className="journal-action-muted">Settled</span></td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      </Panel>
    </>
  );

  const settingsPage = (
    <SettingsControlSurface
      csrfToken={csrfToken}
      build={buildInfo}
      settings={bundle.settings}
      secrets={bundle.secrets}
      onRefresh={onRefresh}
    />
  );

  if (view === "bots") {
    return botsPage;
  }
  if (view === "analytics") {
    return analyticsPage;
  }
  if (view === "accounts") {
    return accountsPage;
  }
  if (view === "settings") {
    return settingsPage;
  }

  return dashboardPage;
}

export default function App() {
  const queryClient = useQueryClient();
  const [activeView, setActiveView] = useState<ViewId>(loadView);
  const [themeMode, setThemeMode] = useState<ThemeMode>(loadThemeMode);
  const [loginError, setLoginError] = useState<string | null>(null);

  useEffect(() => {
    document.documentElement.dataset.theme = themeMode;
    window.localStorage.setItem("omnibot.themeMode", themeMode);
  }, [themeMode]);

  const sessionQuery = useQuery({
    queryKey: ["session"],
    queryFn: getSession,
    retry: false,
  });

  const dashboardQuery = useQuery({
    queryKey: ["dashboard-bundle"],
    queryFn: getDashboardBundle,
    enabled: sessionQuery.isSuccess,
    refetchInterval: 5000,
    refetchIntervalInBackground: true,
  });

  const loginMutation = useMutation({
    mutationFn: ({ username, password }: { username: string; password: string }) => login(username, password),
    onSuccess: async () => {
      setLoginError(null);
      await queryClient.invalidateQueries({ queryKey: ["session"] });
    },
    onError: (error: Error) => setLoginError(error.message),
  });

  const logoutMutation = useMutation({
    mutationFn: (csrfToken: string) => logout(csrfToken),
    onSuccess: async () => {
      await queryClient.invalidateQueries({ queryKey: ["session"] });
      queryClient.removeQueries({ queryKey: ["dashboard-bundle"] });
    },
  });

  if (sessionQuery.isLoading) {
    return <div className="boot-screen">Loading session...</div>;
  }

  if (sessionQuery.isError) {
    return (
      <LoginScreen
        busy={loginMutation.isPending}
        error={loginError}
        onSubmit={(username, password) => loginMutation.mutate({ username, password })}
      />
    );
  }

  const session = sessionQuery.data;
  const bundle = dashboardQuery.data;
  const sidebarBuild = bundle?.build || FALLBACK_BUILD_INFO;

  if (!session) {
    return <div className="boot-screen">Loading session...</div>;
  }

  return (
    <div className="app-shell">
      <Sidebar
        activeView={activeView}
        operatorName={session.actor_id}
        overallState={bundle?.ui_state.overall_state || "Loading data"}
        buildLabel={sidebarBuild.build_label}
        buildVersion={sidebarBuild.version}
        themeMode={themeMode}
        onNavigate={(view) => {
          setActiveView(view);
          window.localStorage.setItem("omnibot.reactView", view);
        }}
        onToggleTheme={() => setThemeMode((current) => current === "dark" ? "light" : "dark")}
        onLogout={() => logoutMutation.mutate(session.csrf_token)}
      />

      <main className="app-main">
        <header className="page-header">
          <div>
            <p className="panel-eyebrow">Overview</p>
            <h1>{VIEW_COPY[activeView].title}</h1>
            <p className="page-copy">{VIEW_COPY[activeView].description}</p>
          </div>
        </header>

        {dashboardQuery.isLoading || !bundle ? <div className="boot-screen boot-inline">Loading dashboard...</div> : null}
        {dashboardQuery.isSuccess && bundle ? (
          <DashboardContent
            view={activeView}
            bundle={bundle}
            csrfToken={session.csrf_token}
            onRefresh={() => void dashboardQuery.refetch()}
            themeMode={themeMode}
          />
        ) : null}
      </main>
    </div>
  );
}