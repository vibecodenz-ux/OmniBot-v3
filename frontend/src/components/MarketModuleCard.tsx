import { useState } from "react";
import { CircleAlert, Info } from "lucide-react";
import { formatNzTime, titleCase } from "../lib/format";
import type { RuntimeMarket, StrategyActivityItem, TradingModule } from "../lib/types";
import { StatusBadge } from "./StatusBadge";

interface MarketModuleCardProps {
  market: RuntimeMarket;
  module: TradingModule | undefined;
  recentEvents?: StrategyActivityItem[];
  pendingCommand?: "start-market" | "stop-market";
  onCommand: (market: string, command: "start-market" | "stop-market") => void;
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

function toneFromValue(value?: string): "success" | "warning" | "danger" | "neutral" {
  const normalized = String(value || "").toLowerCase();
  if (normalized.includes("connected") || normalized.includes("configured") || normalized.includes("active") || normalized.includes("scan-and-trade")) {
    return "success";
  }
  if (normalized.includes("error") || normalized.includes("failure") || normalized.includes("missing")) {
    return "danger";
  }
  if (normalized.includes("waiting") || normalized.includes("scan only") || normalized.includes("connected only")) {
    return "warning";
  }
  return "neutral";
}

function formatModuleBadgeLabel(value: string): string {
  const normalized = value.trim().toLowerCase();

  if (normalized === "actively scanning") {
    return "Scanning";
  }
  if (normalized === "scan and trade" || normalized === "scan-and-trade") {
    return "Auto trade";
  }
  if (normalized === "connected only") {
    return "Connected only";
  }
  if (normalized === "scan only") {
    return "Scan only";
  }

  return value;
}

function summarizeFooterMessage(module: TradingModule | undefined, marketState: string): string {
  const raw = String(module?.status_message || module?.last_decision || "").toLowerCase();

  if (!raw) {
    return titleCase(marketState);
  }
  if (raw.includes("403") || raw.includes("failure") || raw.includes("error")) {
    return "Broker API failure";
  }
  if (raw.includes("connected")) {
    return "Connected";
  }
  if (raw.includes("scan")) {
    return "Monitoring";
  }

  return (module?.status_message || module?.last_decision || titleCase(marketState)).split(".")[0];
}

export function MarketModuleCard({ market, module, recentEvents, pendingCommand, onCommand }: MarketModuleCardProps) {
  const [collapsed, setCollapsed] = useState(false);
  const canStart = market.state !== "RUNNING";
  const isPending = pendingCommand === "start-market" || pendingCommand === "stop-market";
  const nextCommand = pendingCommand || (canStart ? "start-market" : "stop-market");
  const buttonLabel = pendingCommand === "start-market" ? "Starting" : pendingCommand === "stop-market" ? "Stopping" : canStart ? "Start" : "Stop";
  const buttonClassName = nextCommand === "stop-market" ? "primary-button module-action-button module-action-button-stop" : "primary-button module-action-button";
  const symbols = asArray(module?.symbols);
  const footerMessage = summarizeFooterMessage(module, market.state);
  const hasAttention = toneFromValue(module?.connection_state) === "danger" || toneFromValue(module?.automation_state) === "danger";
  const scannerDetail = module?.last_decision || module?.status_message || "No recent activity yet.";
  const decisionHighlights = [
    ...asArray(module?.status_details),
    ...asArray(module?.module_notes),
  ].filter(Boolean).slice(0, 4);
  const guardrailItems = [
    ...asArray(module?.active_guardrails),
    module?.execution_mode === "scan-only" ? "Execution mode is scan-only, so new orders stay blocked until automation is re-enabled." : null,
    module?.automation_state === "connected-only" ? "The worker is connected but automation has not been started for this market yet." : null,
    module?.automation_state === "passive-scanning" ? "The scanner is observing and recording decisions without sending live orders." : null,
  ].filter((value): value is string => Boolean(value)).slice(0, 6);
  const scannerMeta = [
    module?.last_scan_at ? `Checked ${formatNzTime(module.last_scan_at)}` : null,
    module?.last_order_at ? `Last order ${formatNzTime(module.last_order_at)}` : null,
    module?.last_price ? `Price ${module.last_price}` : null,
    module?.candidate_count ? `${module.candidate_count} candidate${module.candidate_count === 1 ? "" : "s"}` : null,
    module?.candidate_score ? `Score ${module.candidate_score}` : null,
  ].filter((value): value is string => Boolean(value));
  const decisionItems = asArray(recentEvents).slice(0, 3);
  const selectedCandidate = module?.last_selected_candidate;
  const selectedThesis = module?.last_selected_thesis;
  const candidateEvidence = asArray(selectedCandidate?.evidence).slice(0, 3);
  const consideredCandidates = asArray(module?.considered_candidates).slice(0, 3);

  return (
    <article className="module-card">
      <header className="module-card-header">
        <div>
          <h3>{module?.label || titleCase(market.market)}</h3>
          <p>{module?.descriptor || module?.module_scope || "Automated market bot"}</p>
        </div>
        <div className="module-card-actions">
          <button type="button" className="utility-button" onClick={() => setCollapsed((value) => !value)}>
            {collapsed ? "Expand" : "Collapse"}
          </button>
        </div>
      </header>

      {collapsed ? null : (
        <div className="module-card-body">
          <div className="badge-row">
            {[module?.credentials_state, module?.connection_state, module?.automation_state, module?.execution_mode]
              .filter((value): value is string => Boolean(value))
              .map((value) => (
                <StatusBadge key={value} label={formatModuleBadgeLabel(value)} tone={toneFromValue(value)} />
              ))}
          </div>

          <div className="module-inline-meta">
            <div className="module-inline-stats" aria-label="Market summary">
              <span>Alerts {module?.signals_seen ?? 0}</span>
              <span>Orders {module?.orders_submitted ?? 0}</span>
              <span>{titleCase(market.state)}</span>
            </div>
            <div className="module-inline-tools">
              <span className="module-symbols-count">{symbols.length} symbols</span>
              <span
                className="module-info-trigger"
                title={symbols.length ? symbols.join(", ") : "No symbols configured."}
                aria-label={symbols.length ? `Tradable symbols: ${symbols.join(", ")}` : "No symbols configured"}
              >
                <Info size={14} />
              </span>
              {hasAttention ? (
                <span className="module-info-trigger module-info-trigger-warning" title={footerMessage} aria-label={footerMessage}>
                  <CircleAlert size={14} />
                </span>
              ) : null}
            </div>
          </div>

          {guardrailItems.length ? (
            <div className="module-guardrails">
              <strong>Active guardrails</strong>
              <div className="module-guardrails-list">
                {guardrailItems.map((item) => <span key={item}>{item}</span>)}
              </div>
            </div>
          ) : null}

          {decisionHighlights.length ? (
            <div className="module-status-list">
              {decisionHighlights.map((item) => <span key={item}>{item}</span>)}
            </div>
          ) : null}

          {decisionItems.length ? (
            <div className="module-decision-list">
              <strong>Recent decisions</strong>
              {decisionItems.map((event, index) => {
                const eventType = typeof event.event_type === "string" ? event.event_type : `decision-${index + 1}`;
                const message = typeof event.message === "string" ? event.message : "No decision message available.";
                const occurredAt = typeof event.occurred_at === "string" ? event.occurred_at : null;
                const details = asArray(event.details).slice(0, 2);
                const tone = toneFromValue(event.level || eventType);
                const eventCandidate = event.selected_candidate;
                return (
                  <article key={`${eventType}-${occurredAt || index}`} className={`module-decision-card module-decision-card-${tone}`}>
                    <div className="module-decision-header">
                      <span>{titleCase(eventType.replace(/-/g, " "))}</span>
                      {occurredAt ? <small>{formatNzTime(occurredAt)}</small> : null}
                    </div>
                    <p>{message}</p>
                    {eventCandidate ? (
                      <div className="module-thesis-chip-row">
                        <span>{titleCase(eventCandidate.strategy_id)}</span>
                        {eventCandidate.setup_family ? <span>{titleCase(eventCandidate.setup_family)}</span> : null}
                        {eventCandidate.side ? <span>{titleCase(eventCandidate.side)}</span> : null}
                        {event.candidate_score ? <span>Score {event.candidate_score}</span> : null}
                      </div>
                    ) : null}
                    {details.length ? (
                      <div className="module-decision-details">
                        {details.map((detail) => <small key={detail}>{detail}</small>)}
                      </div>
                    ) : null}
                  </article>
                );
              })}
            </div>
          ) : null}

          <div className="module-footer">
            <p>{footerMessage}</p>
            <button
              type="button"
              className={buttonClassName}
              disabled={isPending}
              onClick={() => onCommand(market.market, nextCommand)}
            >
              {buttonLabel}
            </button>
          </div>

          {selectedCandidate ? (
            <div className="module-selected-candidate">
              <strong>Current thesis</strong>
              {selectedThesis?.thesis_id ? <small>{selectedThesis.thesis_id}</small> : null}
              <div className="module-thesis-chip-row">
                <span>{titleCase(selectedCandidate.strategy_id)}</span>
                {selectedCandidate.setup_family ? <span>{titleCase(selectedCandidate.setup_family)}</span> : null}
                {selectedCandidate.regime ? <span>{titleCase(selectedCandidate.regime)}</span> : null}
                {selectedCandidate.side ? <span>{titleCase(selectedCandidate.side)}</span> : null}
              </div>
              {selectedCandidate.summary ? <p>{selectedCandidate.summary}</p> : null}
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

          <div className="module-scan-detail">
            <p>{scannerDetail}</p>
            {scannerMeta.length ? <small>{scannerMeta.join(" • ")}</small> : null}
            {module?.last_error ? <small className="module-scan-detail-warning">{module.last_error}</small> : null}
          </div>
        </div>
      )}
    </article>
  );
}