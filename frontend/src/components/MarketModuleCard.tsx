import { useState } from "react";
import { CircleAlert, Info } from "lucide-react";
import { formatNzTime, titleCase } from "../lib/format";
import type { RuntimeMarket, TradingModule } from "../lib/types";
import { StatusBadge } from "./StatusBadge";

interface MarketModuleCardProps {
  market: RuntimeMarket;
  module: TradingModule | undefined;
  pendingCommand?: "start-market" | "stop-market";
  onCommand: (market: string, command: "start-market" | "stop-market") => void;
  onSelectionChange: (market: string, field: "strategy_id" | "profile_id", value: string) => void;
}

function asArray<T>(value: T[] | undefined | null): T[] {
  return Array.isArray(value) ? value : [];
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
    return "Scanner active";
  }

  return (module?.status_message || module?.last_decision || titleCase(marketState)).split(".")[0];
}

export function MarketModuleCard({ market, module, pendingCommand, onCommand, onSelectionChange }: MarketModuleCardProps) {
  const [collapsed, setCollapsed] = useState(false);
  const canStart = market.state !== "RUNNING";
  const isPending = pendingCommand === "start-market" || pendingCommand === "stop-market";
  const nextCommand = pendingCommand || (canStart ? "start-market" : "stop-market");
  const buttonLabel = pendingCommand === "start-market" ? "Starting" : pendingCommand === "stop-market" ? "Stopping" : canStart ? "Start" : "Stop";
  const buttonClassName = nextCommand === "stop-market" ? "primary-button module-action-button module-action-button-stop" : "primary-button module-action-button";
  const symbols = asArray(module?.symbols);
  const footerMessage = summarizeFooterMessage(module, market.state);
  const hasAttention = toneFromValue(module?.connection_state) === "danger" || toneFromValue(module?.automation_state) === "danger";
  const scannerDetail = module?.last_decision || module?.status_message || "No scanner decision recorded yet.";
  const scannerMeta = [
    module?.last_scan_at ? `Scanned ${formatNzTime(module.last_scan_at)}` : null,
    module?.last_order_at ? `Last order ${formatNzTime(module.last_order_at)}` : null,
    module?.last_price ? `Price ${module.last_price}` : null,
  ].filter((value): value is string => Boolean(value));

  return (
    <article className="module-card">
      <header className="module-card-header">
        <div>
          <h3>{module?.label || titleCase(market.market)}</h3>
          <p>{module?.module_scope || module?.descriptor || "Market engine"}</p>
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
            <div className="module-inline-stats" aria-label="Module summary stats">
              <span>Signals {module?.signals_seen ?? 0}</span>
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

          <div className="control-grid">
            <label>
              <span>Strategy</span>
              <select
                value={module?.selected_strategy_id || ""}
                onChange={(event) => onSelectionChange(market.market, "strategy_id", event.target.value)}
              >
                {asArray(module?.strategies).map((option) => (
                  <option key={option.id} value={option.id}>{option.name}</option>
                ))}
              </select>
            </label>

            <label>
              <span>Profile</span>
              <select
                value={module?.selected_profile_id || ""}
                onChange={(event) => onSelectionChange(market.market, "profile_id", event.target.value)}
              >
                {asArray(module?.profiles).map((option) => (
                  <option key={option.id} value={option.id}>{option.name}</option>
                ))}
              </select>
            </label>
          </div>

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