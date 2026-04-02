import { useEffect, useMemo, useState } from "react";
import {
  changeDashboardPassword,
  revokeSecret,
  updateSettings,
  upsertSecret,
  validateSecret,
} from "../lib/api";
import { formatTimestamp, titleCase } from "../lib/format";
import type { AuthSettingsPayload, RuntimeSettingsPayload, SecretMetadata, SecretsPayload, SettingsPayload } from "../lib/types";
import { StatusBadge } from "./StatusBadge";

type FeedbackTone = "success" | "warning" | "danger" | "neutral";

interface FeedbackState {
  tone: FeedbackTone;
  message: string;
}

interface BrokerModule {
  id: string;
  title: string;
  subtitle: string;
  fields: Array<{ secretId: string; label: string; type: "text" | "password" }>;
}

interface SettingsControlSurfaceProps {
  csrfToken: string;
  settings: SettingsPayload;
  secrets: SecretsPayload;
  onRefresh: () => Promise<unknown> | unknown;
}

const BROKER_MODULES: BrokerModule[] = [
  {
    id: "alpaca",
    title: "Alpaca Paper",
    subtitle: "US stock broker credentials",
    fields: [
      { secretId: "alpaca-api-key", label: "API Key", type: "password" },
      { secretId: "alpaca-api-secret", label: "API Secret", type: "password" },
    ],
  },
  {
    id: "binance",
    title: "Binance Futures Demo",
    subtitle: "USD-M futures demo credentials",
    fields: [
      { secretId: "binance-api-key", label: "API Key", type: "password" },
      { secretId: "binance-api-secret", label: "API Secret", type: "password" },
    ],
  },
  {
    id: "ig-forex-au",
    title: "IG Forex AU Demo",
    subtitle: "Forex demo credentials",
    fields: [
      { secretId: "ig-forex-au-username", label: "Demo Username", type: "text" },
      { secretId: "ig-forex-au-password", label: "Demo Password", type: "password" },
      { secretId: "ig-forex-au-api-key", label: "API Key", type: "password" },
    ],
  },
];

function toneFromSecret(secret: SecretMetadata | undefined): FeedbackTone {
  const value = String(secret?.lifecycle_state || secret?.status || "").toLowerCase();
  if (value.includes("valid") || value.includes("active")) {
    return "success";
  }
  if (value.includes("revoked") || value.includes("error") || value.includes("invalid")) {
    return "danger";
  }
  if (value.includes("pending") || value.includes("warning")) {
    return "warning";
  }
  return "neutral";
}

function sanitizeRuntime(settings: SettingsPayload): RuntimeSettingsPayload {
  return {
    log_level: String(settings.runtime.log_level || "info"),
    broker_paper_trading: Boolean(settings.runtime.broker_paper_trading),
    portfolio_snapshot_interval_seconds: Number(settings.runtime.portfolio_snapshot_interval_seconds || 60),
    health_check_interval_seconds: Number(settings.runtime.health_check_interval_seconds || 30),
  };
}

function sanitizeAuth(settings: SettingsPayload): AuthSettingsPayload {
  return {
    admin_username: String(settings.auth.admin_username || "admin"),
    session_idle_timeout_seconds: Number(settings.auth.session_idle_timeout_seconds || 900),
    session_absolute_timeout_seconds: Number(settings.auth.session_absolute_timeout_seconds || 28800),
    session_cookie_secure: Boolean(settings.auth.session_cookie_secure),
    session_cookie_samesite: String(settings.auth.session_cookie_samesite || "strict"),
    allowed_origin: settings.auth.allowed_origin ? String(settings.auth.allowed_origin) : "",
  };
}

function buildBrokerDrafts(modules: BrokerModule[]): Record<string, Record<string, string>> {
  return Object.fromEntries(modules.map((module) => [module.id, Object.fromEntries(module.fields.map((field) => [field.secretId, ""]))]));
}

function latestSecretTimestamp(secrets: SecretMetadata[]): string | null {
  const timestamps = secrets
    .map((secret) => secret.updated_at || null)
    .filter((value): value is string => Boolean(value))
    .sort((left, right) => new Date(right).getTime() - new Date(left).getTime());

  return timestamps[0] || null;
}

export function SettingsControlSurface({ csrfToken, settings, secrets, onRefresh }: SettingsControlSurfaceProps) {
  const secretList = Array.isArray(secrets.secrets) ? secrets.secrets : [];
  const secretsById = useMemo(() => new Map(secretList.map((secret) => [secret.secret_id, secret])), [secretList]);

  const [runtimeForm, setRuntimeForm] = useState<RuntimeSettingsPayload>(() => sanitizeRuntime(settings));
  const [authForm, setAuthForm] = useState<AuthSettingsPayload>(() => sanitizeAuth(settings));
  const [passwordForm, setPasswordForm] = useState({ current: "", next: "", confirm: "" });
  const [brokerDrafts, setBrokerDrafts] = useState<Record<string, Record<string, string>>>(() => buildBrokerDrafts(BROKER_MODULES));
  const [busyKey, setBusyKey] = useState<string | null>(null);
  const [feedback, setFeedback] = useState<Record<string, FeedbackState>>({});

  useEffect(() => {
    setRuntimeForm(sanitizeRuntime(settings));
    setAuthForm(sanitizeAuth(settings));
  }, [settings]);

  function setPanelFeedback(key: string, tone: FeedbackTone, message: string) {
    setFeedback((current) => ({ ...current, [key]: { tone, message } }));
  }

  async function refreshAll() {
    await Promise.resolve(onRefresh());
  }

  async function saveRuntimePolicy() {
    setBusyKey("runtime");
    try {
      await updateSettings(csrfToken, { runtime: runtimeForm });
      setPanelFeedback("runtime", "success", "Runtime policy updated.");
      await refreshAll();
    } catch (error) {
      setPanelFeedback("runtime", "danger", error instanceof Error ? error.message : "Runtime update failed.");
    } finally {
      setBusyKey(null);
    }
  }

  async function saveAuthPolicy() {
    setBusyKey("auth");
    try {
      await updateSettings(csrfToken, {
        auth: {
          session_idle_timeout_seconds: Number(authForm.session_idle_timeout_seconds || 0),
          session_absolute_timeout_seconds: Number(authForm.session_absolute_timeout_seconds || 0),
          session_cookie_secure: Boolean(authForm.session_cookie_secure),
          session_cookie_samesite: String(authForm.session_cookie_samesite || "strict"),
          allowed_origin: String(authForm.allowed_origin || "").trim() || null,
        },
      });
      setPanelFeedback("auth", "success", "Session policy updated.");
      await refreshAll();
    } catch (error) {
      setPanelFeedback("auth", "danger", error instanceof Error ? error.message : "Policy update failed.");
    } finally {
      setBusyKey(null);
    }
  }

  async function savePassword() {
    if (passwordForm.next !== passwordForm.confirm) {
      setPanelFeedback("password", "danger", "New password confirmation does not match.");
      return;
    }
    setBusyKey("password");
    try {
      const response = await changeDashboardPassword(csrfToken, passwordForm.current, passwordForm.next);
      setPasswordForm({ current: "", next: "", confirm: "" });
      setPanelFeedback("password", "success", response.message || "Password updated.");
    } catch (error) {
      setPanelFeedback("password", "danger", error instanceof Error ? error.message : "Password update failed.");
    } finally {
      setBusyKey(null);
    }
  }

  async function saveBrokerModule(module: BrokerModule) {
    const draft = brokerDrafts[module.id] || {};
    const entries = module.fields
      .map((field) => ({ field, value: String(draft[field.secretId] || "") }))
      .filter(({ value }) => value.trim() !== "");

    if (entries.length === 0) {
      setPanelFeedback(module.id, "warning", "Enter at least one updated credential value.");
      return;
    }

    setBusyKey(module.id);
    try {
      for (const { field, value } of entries) {
        await upsertSecret(csrfToken, field.secretId, field.type === "text" ? value.trim() : value);
      }
      setBrokerDrafts((current) => ({
        ...current,
        [module.id]: Object.fromEntries(module.fields.map((field) => [field.secretId, ""])),
      }));
      setPanelFeedback(module.id, "success", `${module.title} credentials saved and validated.`);
      await refreshAll();
    } catch (error) {
      setPanelFeedback(module.id, "danger", error instanceof Error ? error.message : "Credential save failed.");
    } finally {
      setBusyKey(null);
    }
  }

  async function validateBrokerModule(module: BrokerModule) {
    const configured = module.fields.filter((field) => secretsById.has(field.secretId));
    if (configured.length === 0) {
      setPanelFeedback(module.id, "warning", "No stored credentials to validate.");
      return;
    }

    setBusyKey(`${module.id}-validate`);
    try {
      for (const field of configured) {
        await validateSecret(csrfToken, field.secretId);
      }
      setPanelFeedback(module.id, "success", `${module.title} credentials validated.`);
      await refreshAll();
    } catch (error) {
      setPanelFeedback(module.id, "danger", error instanceof Error ? error.message : "Validation failed.");
    } finally {
      setBusyKey(null);
    }
  }

  async function revokeBrokerModule(module: BrokerModule) {
    const configured = module.fields.filter((field) => secretsById.has(field.secretId));
    if (configured.length === 0) {
      setPanelFeedback(module.id, "warning", "No stored credentials to revoke.");
      return;
    }

    setBusyKey(`${module.id}-revoke`);
    try {
      for (const field of configured) {
        await revokeSecret(csrfToken, field.secretId);
      }
      setPanelFeedback(module.id, "success", `${module.title} credentials revoked.`);
      await refreshAll();
    } catch (error) {
      setPanelFeedback(module.id, "danger", error instanceof Error ? error.message : "Revoke failed.");
    } finally {
      setBusyKey(null);
    }
  }

  return (
    <div className="settings-control-stack">
      <div className="two-column-grid settings-policy-grid">
        <section className="panel-surface">
          <header className="panel-header">
            <div className="panel-copy">
              <p className="panel-eyebrow">Settings</p>
              <h2>Runtime controls</h2>
            </div>
          </header>
          <div className="panel-body settings-form-stack">
            <div className="settings-form-grid">
              <label className="settings-field">
                <span>Log level</span>
                <select value={runtimeForm.log_level || "info"} onChange={(event) => setRuntimeForm((current) => ({ ...current, log_level: event.target.value }))}>
                  {[
                    "debug",
                    "info",
                    "warning",
                    "error",
                    "critical",
                  ].map((value) => <option key={value} value={value}>{titleCase(value)}</option>)}
                </select>
              </label>
              <label className="settings-field settings-field-checkbox">
                <span>Broker paper trading</span>
                <input
                  type="checkbox"
                  checked={Boolean(runtimeForm.broker_paper_trading)}
                  onChange={(event) => setRuntimeForm((current) => ({ ...current, broker_paper_trading: event.target.checked }))}
                />
              </label>
              <label className="settings-field">
                <span>Portfolio snapshot interval seconds</span>
                <input
                  type="number"
                  min={1}
                  value={String(runtimeForm.portfolio_snapshot_interval_seconds || 0)}
                  onChange={(event) => setRuntimeForm((current) => ({ ...current, portfolio_snapshot_interval_seconds: Number(event.target.value) }))}
                />
              </label>
              <label className="settings-field">
                <span>Health check interval seconds</span>
                <input
                  type="number"
                  min={1}
                  value={String(runtimeForm.health_check_interval_seconds || 0)}
                  onChange={(event) => setRuntimeForm((current) => ({ ...current, health_check_interval_seconds: Number(event.target.value) }))}
                />
              </label>
            </div>
            <div className="settings-form-actions">
              <button type="button" className="primary-button" onClick={() => void saveRuntimePolicy()} disabled={busyKey === "runtime"}>
                {busyKey === "runtime" ? "Saving" : "Save runtime"}
              </button>
              {feedback.runtime ? <StatusBadge label={feedback.runtime.message} tone={feedback.runtime.tone} /> : null}
            </div>
          </div>
        </section>

        <section className="panel-surface">
          <header className="panel-header">
            <div className="panel-copy">
              <p className="panel-eyebrow">Auth</p>
              <h2>Session policy</h2>
            </div>
          </header>
          <div className="panel-body settings-form-stack">
            <div className="settings-form-grid">
              <label className="settings-field settings-field-readonly">
                <span>Admin username</span>
                <strong>{authForm.admin_username || "admin"}</strong>
              </label>
              <label className="settings-field">
                <span>Session idle timeout seconds</span>
                <input
                  type="number"
                  min={60}
                  value={String(authForm.session_idle_timeout_seconds || 0)}
                  onChange={(event) => setAuthForm((current) => ({ ...current, session_idle_timeout_seconds: Number(event.target.value) }))}
                />
              </label>
              <label className="settings-field">
                <span>Session absolute timeout seconds</span>
                <input
                  type="number"
                  min={60}
                  value={String(authForm.session_absolute_timeout_seconds || 0)}
                  onChange={(event) => setAuthForm((current) => ({ ...current, session_absolute_timeout_seconds: Number(event.target.value) }))}
                />
              </label>
              <label className="settings-field settings-field-checkbox">
                <span>Session cookie secure</span>
                <input
                  type="checkbox"
                  checked={Boolean(authForm.session_cookie_secure)}
                  onChange={(event) => setAuthForm((current) => ({ ...current, session_cookie_secure: event.target.checked }))}
                />
              </label>
              <label className="settings-field">
                <span>Session cookie samesite</span>
                <select value={String(authForm.session_cookie_samesite || "strict")} onChange={(event) => setAuthForm((current) => ({ ...current, session_cookie_samesite: event.target.value }))}>
                  {["strict", "lax", "none"].map((value) => <option key={value} value={value}>{titleCase(value)}</option>)}
                </select>
              </label>
              <label className="settings-field settings-field-wide">
                <span>Allowed origin</span>
                <input
                  type="text"
                  placeholder="Optional origin"
                  value={String(authForm.allowed_origin || "")}
                  onChange={(event) => setAuthForm((current) => ({ ...current, allowed_origin: event.target.value }))}
                />
              </label>
            </div>
            <div className="settings-form-actions">
              <button type="button" className="primary-button" onClick={() => void saveAuthPolicy()} disabled={busyKey === "auth"}>
                {busyKey === "auth" ? "Saving" : "Save policy"}
              </button>
              {feedback.auth ? <StatusBadge label={feedback.auth.message} tone={feedback.auth.tone} /> : null}
            </div>
          </div>
        </section>
      </div>

      <section className="panel-surface">
        <header className="panel-header">
          <div className="panel-copy">
            <p className="panel-eyebrow">Broker setup</p>
            <h2>Trading platform credentials</h2>
          </div>
        </header>
        <div className="panel-body">
          <div className="settings-broker-grid">
            {BROKER_MODULES.map((module) => {
              const configuredSecrets = module.fields.map((field) => secretsById.get(field.secretId)).filter(Boolean) as SecretMetadata[];
              const allConfigured = configuredSecrets.length === module.fields.length;
              const lastUpdatedAt = latestSecretTimestamp(configuredSecrets);
              const providerTone: FeedbackTone = configuredSecrets.some((item) => toneFromSecret(item) === "danger")
                ? "danger"
                : allConfigured
                  ? "success"
                  : "warning";

              return (
                <article key={module.id} className="settings-broker-card">
                  <div className="settings-broker-header">
                    <div>
                      <h3>{module.title}</h3>
                      <p>{module.subtitle}</p>
                    </div>
                    <StatusBadge label={allConfigured ? "configured" : `${configuredSecrets.length}/${module.fields.length} ready`} tone={providerTone} />
                  </div>

                  <div className="settings-broker-status-list">
                    {module.fields.map((field) => {
                      const metadata = secretsById.get(field.secretId);
                      return (
                        <div key={field.secretId} className="settings-broker-status-item">
                          <div>
                            <strong>{field.label}</strong>
                            <small>{metadata?.masked_display || "Not stored"}</small>
                          </div>
                          <StatusBadge label={metadata?.lifecycle_state || metadata?.status || "missing"} tone={metadata ? toneFromSecret(metadata) : "warning"} />
                        </div>
                      );
                    })}
                  </div>

                  <div className="settings-broker-fields">
                    {module.fields.map((field) => (
                      <label key={field.secretId} className="settings-field">
                        <span>{field.label}</span>
                        <input
                          type={field.type}
                          placeholder={`Update ${field.label.toLowerCase()}`}
                          value={brokerDrafts[module.id]?.[field.secretId] || ""}
                          onChange={(event) => setBrokerDrafts((current) => ({
                            ...current,
                            [module.id]: {
                              ...(current[module.id] || {}),
                              [field.secretId]: event.target.value,
                            },
                          }))}
                        />
                      </label>
                    ))}
                  </div>

                  <div className="settings-broker-actions">
                    <button type="button" className="primary-button" onClick={() => void saveBrokerModule(module)} disabled={busyKey === module.id}>
                      {busyKey === module.id ? "Saving" : "Save"}
                    </button>
                    <button type="button" className="utility-button" onClick={() => void validateBrokerModule(module)} disabled={busyKey === `${module.id}-validate`}>
                      {busyKey === `${module.id}-validate` ? "Checking" : "Test connection"}
                    </button>
                    <button type="button" className="utility-button utility-button-danger" onClick={() => void revokeBrokerModule(module)} disabled={busyKey === `${module.id}-revoke`}>
                      {busyKey === `${module.id}-revoke` ? "Deleting" : "Delete"}
                    </button>
                  </div>

                  <div className="settings-broker-meta">
                    <span>{lastUpdatedAt ? `Updated ${formatTimestamp(lastUpdatedAt)}` : "Not updated yet."}</span>
                  </div>

                  {feedback[module.id] ? <p className={`settings-feedback settings-feedback-${feedback[module.id].tone}`}>{feedback[module.id].message}</p> : null}
                </article>
              );
            })}
          </div>
        </div>
      </section>

      <div className="two-column-grid settings-policy-grid">
        <section className="panel-surface">
          <header className="panel-header">
            <div className="panel-copy">
              <p className="panel-eyebrow">Environment</p>
              <h2>Deployment</h2>
            </div>
          </header>
          <div className="panel-body">
            <article className="support-card">
              <span>Mode</span>
              <strong>{typeof settings.environment === "string" ? titleCase(settings.environment) : "Custom"}</strong>
              <small>Updated {formatTimestamp(settings.updated_at || null)}</small>
            </article>
          </div>
        </section>

        <section className="panel-surface">
          <header className="panel-header">
            <div className="panel-copy">
              <p className="panel-eyebrow">Dashboard password</p>
              <h2>Change operator login</h2>
            </div>
          </header>
          <div className="panel-body settings-form-stack">
            <div className="settings-form-grid settings-form-grid-single">
              <label className="settings-field">
                <span>Current password</span>
                <input type="password" value={passwordForm.current} onChange={(event) => setPasswordForm((current) => ({ ...current, current: event.target.value }))} />
              </label>
              <label className="settings-field">
                <span>New password</span>
                <input type="password" minLength={8} value={passwordForm.next} onChange={(event) => setPasswordForm((current) => ({ ...current, next: event.target.value }))} />
              </label>
              <label className="settings-field">
                <span>Confirm password</span>
                <input type="password" minLength={8} value={passwordForm.confirm} onChange={(event) => setPasswordForm((current) => ({ ...current, confirm: event.target.value }))} />
              </label>
            </div>
            <div className="settings-form-actions">
              <button type="button" className="primary-button" onClick={() => void savePassword()} disabled={busyKey === "password"}>
                {busyKey === "password" ? "Updating" : "Update password"}
              </button>
            </div>
            {feedback.password ? <p className={`settings-feedback settings-feedback-${feedback.password.tone}`}>{feedback.password.message}</p> : null}
          </div>
        </section>
      </div>
    </div>
  );
}