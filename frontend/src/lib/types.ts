export type ViewId = "dashboard" | "bots" | "analytics" | "accounts" | "settings";

export interface SessionView {
  actor_id: string;
  csrf_token: string;
  expires_at?: string | null;
}

export interface RuntimeMarket {
  market: string;
  state: string;
  kill_switch_engaged?: boolean;
}

export interface RuntimeOverview {
  state: string;
  markets: RuntimeMarket[];
}

export interface HealthReport {
  market: string;
  state: string;
  ready: boolean;
  reason: string;
}

export interface HealthSummary {
  ready: boolean;
  market_reports: HealthReport[];
}

export interface WidgetState {
  widget_id?: string;
  title: string;
  message: string;
  state: string;
  level: string;
}

export interface UiState {
  overall_state: string;
  banner?: {
    level?: string;
    title?: string;
    message?: string;
  };
  widgets: WidgetState[];
}

export interface PortfolioMarketRow {
  market: string;
  total_portfolio_value?: string | number | null;
  equity?: string | number | null;
  market_value?: string | number | null;
  position_count?: number | null;
  open_order_count?: number | null;
  as_of?: string | null;
}

export interface PortfolioOverview {
  generated_at?: string | null;
  snapshot_count?: number;
  total_portfolio_value?: string | number | null;
  total_equity?: string | number | null;
  total_cash?: string | number | null;
  total_buying_power?: string | number | null;
  total_market_value?: string | number | null;
  total_unrealized_pnl?: string | number | null;
  total_realized_pnl?: string | number | null;
  markets?: PortfolioMarketRow[];
}

export interface AnalyticsStat {
  label: string;
  value: string | number | null;
  unit?: string | null;
}

export interface AnalyticsPoint {
  label: string;
  value: string | number;
}

export interface AnalyticsChart {
  chart_id?: string;
  title: string;
  unit?: string | null;
  provenance?: {
    aggregation?: string | null;
  };
  points: AnalyticsPoint[];
}

export interface AnalyticsOverview {
  stats?: AnalyticsStat[];
  charts?: AnalyticsChart[];
}

export interface SecretMetadata {
  secret_id: string;
  scope: string;
  status?: string;
  lifecycle_state?: string;
  backend?: string;
  masked_display?: string;
  rotation_required?: boolean;
  validation_error?: string | null;
  last_validated_at?: string | null;
  updated_at?: string | null;
}

export interface SecretsPayload {
  secret_count?: number;
  secrets?: SecretMetadata[];
}

export interface TradingModuleOption {
  id: string;
  name: string;
}

export interface TradingModule {
  market: string;
  label?: string;
  descriptor?: string;
  module_scope?: string;
  symbols_tooltip?: string;
  module_notes?: string[];
  selected_strategy_id?: string | null;
  selected_profile_id?: string | null;
  strategy_summary?: string;
  profile_summary?: string;
  strategy_note?: string;
  profile_note?: string;
  strategies?: TradingModuleOption[];
  profiles?: TradingModuleOption[];
  symbols?: string[];
  credentials_state?: string;
  connection_state?: string;
  automation_state?: string;
  execution_mode?: string;
  status_message?: string;
  status_details?: string[];
  last_decision?: string;
  last_error?: string | null;
  last_scan_at?: string | null;
  last_signal_at?: string | null;
  last_order_at?: string | null;
  last_price?: string | null;
  signals_seen?: number;
  orders_submitted?: number;
}

export interface TradingModulesPayload {
  modules: TradingModule[];
}

export interface AuditEvent {
  title?: string;
  subtitle?: string;
  meta?: string[];
  actor_id?: string;
  mechanism?: string;
  outcome?: string;
  occurred_at?: string;
  market?: string;
  event_type?: string;
  message?: string;
  [key: string]: unknown;
}

export interface AuditPayload {
  events: AuditEvent[];
}

export interface JournalTotals {
  open_position_count?: number;
  closed_trade_count?: number;
  total_unrealized_pnl?: string | number | null;
  total_realized_pnl?: string | number | null;
  today_realized_pnl?: string | number | null;
  yesterday_realized_pnl?: string | number | null;
}

export interface JournalPosition {
  market: string;
  symbol: string;
  status?: string;
  quantity?: string | number | null;
  side?: string | null;
  entry_price?: string | number | null;
  market_price?: string | number | null;
  opened_at?: string | null;
  updated_at?: string | null;
  current_price?: string | number | null;
  unrealized_pnl?: string | number | null;
  close_available?: boolean;
}

export interface JournalTrade {
  trade_id?: string;
  market: string;
  symbol: string;
  status?: string;
  side?: string | null;
  quantity?: string | number | null;
  entry_price?: string | number | null;
  exit_price?: string | number | null;
  opened_at?: string | null;
  fees?: string | number | null;
  realized_pnl?: string | number | null;
  closed_at?: string | null;
}

export interface JournalPayload {
  generated_at?: string | null;
  totals?: JournalTotals;
  open_positions?: JournalPosition[];
  closed_trades?: JournalTrade[];
}

export interface MarketHoursItem {
  market: string;
  state?: string;
  status?: string;
  label?: string;
  is_open?: boolean;
  detail: string;
}

export interface MarketHoursPayload {
  markets: MarketHoursItem[];
}

export interface StrategyActivityItem {
  title: string;
  subtitle: string;
  meta: string[];
}

export interface StrategyActivityRankedSymbol {
  symbol: string;
  score?: string | number | null;
  latest_price?: string | number | null;
  momentum_ratio?: string | number | null;
  volatility_ratio?: string | number | null;
  volume_ratio?: string | number | null;
  bar_count?: number | null;
}

export interface StrategyActivitySeriesPoint {
  label: string;
  value: string | number;
}

export interface StrategyActivitySeries {
  market: string;
  symbol: string;
  timeframe?: string | null;
  points: StrategyActivitySeriesPoint[];
}

export interface StrategyActivityCandle {
  label: string;
  opened_at?: string;
  open: string | number;
  high: string | number;
  low: string | number;
  close: string | number;
  volume?: string | number | null;
}

export interface StrategyActivityMarketSummary {
  market: string;
  warmup_status?: string | null;
  last_scan_at?: string | null;
  last_decision?: string | null;
  top_symbol?: string | null;
  available_symbols?: string[];
  ranked_symbols?: StrategyActivityRankedSymbol[];
  series?: StrategyActivitySeries[];
  candles_by_symbol?: Record<string, StrategyActivityCandle[]>;
  candle_timeframe?: string | null;
}

export interface StrategyActivityPayload {
  events: StrategyActivityItem[];
  market_summaries?: StrategyActivityMarketSummary[];
}

export interface RuntimeSettingsPayload {
  log_level?: string;
  broker_paper_trading?: boolean;
  portfolio_snapshot_interval_seconds?: number;
  health_check_interval_seconds?: number;
}

export interface AuthSettingsPayload {
  admin_username?: string;
  session_idle_timeout_seconds?: number;
  session_absolute_timeout_seconds?: number;
  session_cookie_secure?: boolean;
  session_cookie_samesite?: string;
  allowed_origin?: string | null;
}

export interface SettingsPayload {
  environment?: string | Record<string, unknown>;
  updated_at?: string | null;
  runtime: RuntimeSettingsPayload;
  auth: AuthSettingsPayload;
}

export interface DashboardBundle {
  runtime: RuntimeOverview;
  health: HealthSummary;
  ui_state: UiState;
  portfolio: PortfolioOverview;
  analytics: AnalyticsOverview;
  settings: SettingsPayload;
  runtime_audit: AuditPayload;
  login_audit: AuditPayload;
  secrets: SecretsPayload;
  modules: TradingModulesPayload;
  journal: JournalPayload;
  market_hours: MarketHoursPayload;
  strategy_activity: StrategyActivityPayload;
}