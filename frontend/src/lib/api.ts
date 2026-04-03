import type {
  AuthSettingsPayload,
  DashboardBundle,
  RuntimeSettingsPayload,
  SecretMetadata,
  SessionView,
  SettingsPayload,
  UpdateApplyResponse,
  UpdateCheckResult,
  UpdateStatusPayload,
} from "./types";

const API_BASE_URL = import.meta.env.VITE_API_BASE_URL || "";

export class ApiError extends Error {
  status: number;

  constructor(message: string, status: number) {
    super(message);
    this.status = status;
  }
}

interface RequestOptions {
  method?: string;
  body?: unknown;
  csrfToken?: string;
}

async function requestJson<T>(path: string, options: RequestOptions = {}): Promise<T> {
  const headers = new Headers({ Accept: "application/json" });
  let body: BodyInit | undefined;

  if (options.body !== undefined) {
    headers.set("Content-Type", "application/json");
    body = JSON.stringify(options.body);
  }

  if (options.csrfToken) {
    headers.set("X-CSRF-Token", options.csrfToken);
  }

  const response = await fetch(`${API_BASE_URL}${path}`, {
    method: options.method || "GET",
    headers,
    body,
    credentials: "same-origin",
  });

  const contentType = response.headers.get("content-type") || "";
  const payload = contentType.includes("application/json") ? await response.json() : await response.text();

  if (!response.ok) {
    const message = typeof payload === "object" && payload && "detail" in payload
      ? String((payload as { detail?: unknown }).detail)
      : `Request failed with status ${response.status}`;
    throw new ApiError(message, response.status);
  }

  return payload as T;
}

export async function getSession(): Promise<SessionView> {
  return requestJson<SessionView>("/v1/auth/session");
}

export async function login(username: string, password: string): Promise<SessionView> {
  return requestJson<SessionView>("/v1/auth/login", {
    method: "POST",
    body: { username, password },
  });
}

export async function logout(csrfToken: string): Promise<{ logged_out: boolean }> {
  return requestJson<{ logged_out: boolean }>("/v1/auth/logout", {
    method: "POST",
    csrfToken,
  });
}

export async function getDashboardBundle(): Promise<DashboardBundle> {
  return requestJson<DashboardBundle>("/v1/dashboard");
}

export async function checkForUpdates(csrfToken: string): Promise<UpdateCheckResult> {
  return requestJson<UpdateCheckResult>("/v1/system/update/check", {
    method: "POST",
    csrfToken,
  });
}

export async function applySystemUpdate(csrfToken: string): Promise<UpdateApplyResponse> {
  return requestJson<UpdateApplyResponse>("/v1/system/update/apply", {
    method: "POST",
    csrfToken,
  });
}

export async function getUpdateStatus(): Promise<UpdateStatusPayload> {
  return requestJson<UpdateStatusPayload>("/v1/system/update/status");
}

export async function rollbackSystemUpdate(csrfToken: string): Promise<UpdateApplyResponse> {
  return requestJson<UpdateApplyResponse>("/v1/system/update/rollback", {
    method: "POST",
    csrfToken,
  });
}

export async function sendRuntimeCommand(csrfToken: string, market: string, command: "start-market" | "stop-market") {
  const action = command === "start-market" ? "start" : "stop";
  return requestJson(`/v1/markets/${market}/${action}`, {
    method: "POST",
    csrfToken,
  });
}

export async function updateModuleSelection(
  csrfToken: string,
  market: string,
  body: { strategy_id?: string; profile_id?: string },
) {
  return requestJson(`/v1/trading/modules/${market}/selection`, {
    method: "PUT",
    csrfToken,
    body,
  });
}

export async function updateSettings(
  csrfToken: string,
  body: { runtime?: RuntimeSettingsPayload; auth?: Partial<AuthSettingsPayload> },
): Promise<SettingsPayload> {
  return requestJson<SettingsPayload>("/v1/settings", {
    method: "PUT",
    csrfToken,
    body,
  });
}

export async function changeDashboardPassword(
  csrfToken: string,
  currentPassword: string,
  newPassword: string,
): Promise<{ updated: boolean; actor_id: string; message: string }> {
  return requestJson<{ updated: boolean; actor_id: string; message: string }>("/v1/settings/dashboard-password", {
    method: "POST",
    csrfToken,
    body: {
      current_password: currentPassword,
      new_password: newPassword,
    },
  });
}

export async function upsertSecret(
  csrfToken: string,
  secretId: string,
  value: string,
): Promise<SecretMetadata> {
  return requestJson<SecretMetadata>(`/v1/secrets/${secretId}`, {
    method: "PUT",
    csrfToken,
    body: {
      scope: "broker",
      value,
      validate_after_store: true,
    },
  });
}

export async function validateSecret(csrfToken: string, secretId: string): Promise<SecretMetadata> {
  return requestJson<SecretMetadata>(`/v1/secrets/${secretId}/validate`, {
    method: "POST",
    csrfToken,
  });
}

export async function revokeSecret(csrfToken: string, secretId: string): Promise<{ secret_id?: string; revoked?: boolean }> {
  return requestJson<{ secret_id?: string; revoked?: boolean }>(`/v1/secrets/${secretId}`, {
    method: "DELETE",
    csrfToken,
  });
}

export async function closeJournalPosition(
  csrfToken: string,
  market: string,
  symbol: string,
): Promise<{ closed: boolean; market: string; symbol: string; order_id?: string; trade_id?: string; closed_at?: string; realized_pnl?: string }> {
  return requestJson<{ closed: boolean; market: string; symbol: string; order_id?: string; trade_id?: string; closed_at?: string; realized_pnl?: string }>("/v1/trades/close-position", {
    method: "POST",
    csrfToken,
    body: { market, symbol },
  });
}

export async function backfillJournalHistory(
  csrfToken: string,
  body: { market?: string; limit?: number } = {},
): Promise<{ backfilled: boolean; requested_market?: string | null; loaded_trade_count?: number; updated_markets?: Array<{ market: string; loaded_trade_count: number; journal_trade_count: number }>; skipped_markets?: Array<{ market: string; reason: string }>; message?: string }> {
  return requestJson("/v1/trades/backfill-history", {
    method: "POST",
    csrfToken,
    body,
  });
}

export async function clearJournalHistory(
  csrfToken: string,
): Promise<{ cleared: boolean; cleared_before?: string; remaining_visible_count?: number; message?: string }> {
  return requestJson("/v1/trades/clear-history", {
    method: "POST",
    csrfToken,
  });
}