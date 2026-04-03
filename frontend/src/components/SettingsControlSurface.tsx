import { useEffect, useMemo, useState } from "react";
import {
  applySystemUpdate,
  checkForUpdates,
  changeDashboardPassword,
  getUpdateStatus,
  revokeSecret,
  rollbackSystemUpdate,
  updateSettings,
  upsertSecret,
  validateSecret,
} from "../lib/api";
import { formatTimestamp, titleCase } from "../lib/format";
import type {
  AuthSettingsPayload,
  BuildInfo,
  RuntimeSettingsPayload,
  SecretMetadata,
  SecretsPayload,
  SettingsPayload,
  UpdateCheckResult,
  UpdateStatusPayload,
} from "../lib/types";
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
  build?: BuildInfo;
  settings: SettingsPayload;
  secrets: SecretsPayload;
  onRefresh: () => Promise<unknown> | unknown;
}

const FALLBACK_BUILD_INFO: BuildInfo = {
  version: "0.1.0",
  build_number: "---",
  build_label: "Build:---",
  update_source: {
    repo: "Unknown repository",
    branch: "main",
  },
};

const BROKER_MODULES: BrokerModule[] = [
  {
    id: "alpaca",
    title: "Alpaca Paper",
    subtitle: "US stock broker sign-in details",
    fields: [
      { secretId: "alpaca-api-key", label: "API Key", type: "password" },
      { secretId: "alpaca-api-secret", label: "API Secret", type: "password" },
    ],
  },
  {
    id: "binance",
    title: "Binance Futures Demo",
    subtitle: "USD-M futures demo sign-in details",
    fields: [
      { secretId: "binance-api-key", label: "API Key", type: "password" },
      { secretId: "binance-api-secret", label: "API Secret", type: "password" },
    ],
  },
  {
    id: "ig-forex-au",
    title: "IG Forex AU Demo",
    subtitle: "Forex demo sign-in details",
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

export function SettingsControlSurface({ csrfToken, build, settings, secrets, onRefresh }: SettingsControlSurfaceProps) {
  const secretList = Array.isArray(secrets.secrets) ? secrets.secrets : [];
  const secretsById = useMemo(() => new Map(secretList.map((secret) => [secret.secret_id, secret])), [secretList]);
  const resolvedBuild = build || FALLBACK_BUILD_INFO;

  const [runtimeForm, setRuntimeForm] = useState<RuntimeSettingsPayload>(() => sanitizeRuntime(settings));
  const [authForm, setAuthForm] = useState<AuthSettingsPayload>(() => sanitizeAuth(settings));
  const [passwordForm, setPasswordForm] = useState({ current: "", next: "", confirm: "" });
  const [brokerDrafts, setBrokerDrafts] = useState<Record<string, Record<string, string>>>(() => buildBrokerDrafts(BROKER_MODULES));
  const [busyKey, setBusyKey] = useState<string | null>(null);
  const [feedback, setFeedback] = useState<Record<string, FeedbackState>>({});
  const [updateState, setUpdateState] = useState<UpdateCheckResult | null>(null);
  const [updateStatus, setUpdateStatus] = useState<UpdateStatusPayload>({ backups: [] });

  useEffect(() => {
    setRuntimeForm(sanitizeRuntime(settings));
    setAuthForm(sanitizeAuth(settings));
  }, [settings]);

  useEffect(() => {
    setUpdateState((current) => current && current.local.build_number === resolvedBuild.build_number ? current : null);
  }, [resolvedBuild.build_number]);

  function setPanelFeedback(key: string, tone: FeedbackTone, message: string) {
    setFeedback((current) => ({ ...current, [key]: { tone, message } }));
  }

  async function refreshAll() {
    await Promise.resolve(onRefresh());
    try {
      setUpdateStatus(await getUpdateStatus());
    } catch {
      // Keep the current updater surface usable even if status refresh fails.
    }
  }

  useEffect(() => {
    void (async () => {
      try {
        setUpdateStatus(await getUpdateStatus());
      } catch (error) {
        setPanelFeedback("updater", "danger", error instanceof Error ? error.message : "Could not load update status.");
      }
    })();
  }, []);

  async function saveRuntimePolicy() {
    setBusyKey("runtime");
    try {
      await updateSettings(csrfToken, { runtime: runtimeForm });
      setPanelFeedback("runtime", "success", "Settings updated.");
      await refreshAll();
    } catch (error) {
      setPanelFeedback("runtime", "danger", error instanceof Error ? error.message : "Settings update failed.");
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
      setPanelFeedback("auth", "success", "Security settings updated.");
      await refreshAll();
    } catch (error) {
      setPanelFeedback("auth", "danger", error instanceof Error ? error.message : "Security settings update failed.");
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
      setPanelFeedback(module.id, "warning", "Enter at least one updated value.");
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
      setPanelFeedback(module.id, "success", `${module.title} details saved and checked.`);
      await refreshAll();
    } catch (error) {
      setPanelFeedback(module.id, "danger", error instanceof Error ? error.message : "Save failed.");
    } finally {
      setBusyKey(null);
    }
  }

  async function validateBrokerModule(module: BrokerModule) {
    const configured = module.fields.filter((field) => secretsById.has(field.secretId));
    if (configured.length === 0) {
      setPanelFeedback(module.id, "warning", "No saved details to check.");
      return;
    }

    setBusyKey(`${module.id}-validate`);
    try {
      for (const field of configured) {
        await validateSecret(csrfToken, field.secretId);
      }
      setPanelFeedback(module.id, "success", `${module.title} details checked.`);
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
      setPanelFeedback(module.id, "warning", "No saved details to remove.");
      return;
    }

    setBusyKey(`${module.id}-revoke`);
    try {
      for (const field of configured) {
        await revokeSecret(csrfToken, field.secretId);
      }
      setPanelFeedback(module.id, "success", `${module.title} details removed.`);
      await refreshAll();
    } catch (error) {
      setPanelFeedback(module.id, "danger", error instanceof Error ? error.message : "Revoke failed.");
    } finally {
      setBusyKey(null);
    }
  }

  async function runUpdaterAction() {
    if (updateState?.update_available) {
      setBusyKey("updater-apply");
      try {
        const response = await applySystemUpdate(csrfToken);
        setPanelFeedback("updater", "warning", response.message || "Updater scheduled. OmniBot will restart shortly.");
        await refreshAll();
        window.setTimeout(() => {
          window.location.reload();
        }, Math.max(3, Number(response.reload_after_seconds || 6)) * 1000);
      } catch (error) {
        setPanelFeedback("updater", "danger", error instanceof Error ? error.message : "Update launch failed.");
      } finally {
        setBusyKey(null);
      }
      return;
    }

    setBusyKey("updater-check");
    try {
      const response = await checkForUpdates(csrfToken);
      setUpdateState(response);
      setUpdateStatus((current) => ({
        ...current,
        last_check: response,
      }));
      setPanelFeedback(
        "updater",
        response.update_available ? "warning" : "success",
        response.message || (response.update_available ? "Update available." : "Already on the latest build."),
      );
      await refreshAll();
    } catch (error) {
      setPanelFeedback("updater", "danger", error instanceof Error ? error.message : "Update check failed.");
    } finally {
      setBusyKey(null);
    }
  }

  async function runRollbackAction() {
    setBusyKey("updater-rollback");
    try {
      const response = await rollbackSystemUpdate(csrfToken);
      setPanelFeedback("updater", "warning", response.message || "Rollback scheduled. OmniBot will restart shortly.");
      await refreshAll();
      window.setTimeout(() => {
        window.location.reload();
      }, Math.max(3, Number(response.reload_after_seconds || 6)) * 1000);
    } catch (error) {
      setPanelFeedback("updater", "danger", error instanceof Error ? error.message : "Rollback launch failed.");
    } finally {
      setBusyKey(null);
    }
  }

  const updaterBusy = busyKey === "updater-check" || busyKey === "updater-apply";
  const rollbackBusy = busyKey === "updater-rollback";
  const updaterButtonLabel = busyKey === "updater-check"
    ? "Checking for updates"
    : updateState?.update_available
      ? "UPDATE NOW"
      : "Check for Updates";
  const updaterTone: FeedbackTone = updateState?.update_available
    ? "warning"
    : feedback.updater?.tone || "neutral";
  const latestBackup = updateStatus.backups[0] || null;

  return (
    <div className="settings-control-stack">
      <div className="two-column-grid settings-policy-grid">
        <section className="panel-surface">
          <header className="panel-header">
            <div className="panel-copy">
              <p className="panel-eyebrow">Settings</p>
              <h2>App settings</h2>
            </div>
          </header>
          <div className="panel-body settings-form-stack">
            <div className="settings-form-grid">
              <label className="settings-field">
                <span>Log detail</span>
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
                <span>Paper trading</span>
                <input
                  type="checkbox"
                  checked={Boolean(runtimeForm.broker_paper_trading)}
                  onChange={(event) => setRuntimeForm((current) => ({ ...current, broker_paper_trading: event.target.checked }))}
                />
              </label>
              <label className="settings-field">
                <span>Portfolio refresh interval (seconds)</span>
                <input
                  type="number"
                  min={1}
                  value={String(runtimeForm.portfolio_snapshot_interval_seconds || 0)}
                  onChange={(event) => setRuntimeForm((current) => ({ ...current, portfolio_snapshot_interval_seconds: Number(event.target.value) }))}
                />
              </label>
              <label className="settings-field">
                <span>Status check interval (seconds)</span>
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
                {busyKey === "runtime" ? "Saving" : "Save settings"}
              </button>
              {feedback.runtime ? <StatusBadge label={feedback.runtime.message} tone={feedback.runtime.tone} /> : null}
            </div>
          </div>
        </section>

        <section className="panel-surface">
          <header className="panel-header">
            <div className="panel-copy">
              <p className="panel-eyebrow">Security</p>
              <h2>Sign-in and session</h2>
            </div>
          </header>
          <div className="panel-body settings-form-stack">
            <div className="settings-form-grid">
              <label className="settings-field settings-field-readonly">
                <span>Username</span>
                <strong>{authForm.admin_username || "admin"}</strong>
              </label>
              <label className="settings-field">
                <span>Idle sign-out time (seconds)</span>
                <input
                  type="number"
                  min={60}
                  value={String(authForm.session_idle_timeout_seconds || 0)}
                  onChange={(event) => setAuthForm((current) => ({ ...current, session_idle_timeout_seconds: Number(event.target.value) }))}
                />
              </label>
              <label className="settings-field">
                <span>Maximum session length (seconds)</span>
                <input
                  type="number"
                  min={60}
                  value={String(authForm.session_absolute_timeout_seconds || 0)}
                  onChange={(event) => setAuthForm((current) => ({ ...current, session_absolute_timeout_seconds: Number(event.target.value) }))}
                />
              </label>
              <label className="settings-field settings-field-checkbox">
                <span>Secure cookies</span>
                <input
                  type="checkbox"
                  checked={Boolean(authForm.session_cookie_secure)}
                  onChange={(event) => setAuthForm((current) => ({ ...current, session_cookie_secure: event.target.checked }))}
                />
              </label>
              <label className="settings-field">
                <span>Cookie access mode</span>
                <select value={String(authForm.session_cookie_samesite || "strict")} onChange={(event) => setAuthForm((current) => ({ ...current, session_cookie_samesite: event.target.value }))}>
                  {["strict", "lax", "none"].map((value) => <option key={value} value={value}>{titleCase(value)}</option>)}
                </select>
              </label>
              <label className="settings-field settings-field-wide">
                <span>Allowed website</span>
                <input
                  type="text"
                  placeholder="Optional website"
                  value={String(authForm.allowed_origin || "")}
                  onChange={(event) => setAuthForm((current) => ({ ...current, allowed_origin: event.target.value }))}
                />
              </label>
            </div>
            <div className="settings-form-actions">
              <button type="button" className="primary-button" onClick={() => void saveAuthPolicy()} disabled={busyKey === "auth"}>
                {busyKey === "auth" ? "Saving" : "Save security settings"}
              </button>
              {feedback.auth ? <StatusBadge label={feedback.auth.message} tone={feedback.auth.tone} /> : null}
            </div>
          </div>
        </section>
      </div>

      <section className="panel-surface">
        <header className="panel-header">
          <div className="panel-copy">
            <p className="panel-eyebrow">Broker accounts</p>
            <h2>Account connections</h2>
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
              <p className="panel-eyebrow">Updater</p>
              <h2>GitHub build updates</h2>
            </div>
          </header>
          <div className="panel-body settings-form-stack">
            <div className="settings-update-card-grid">
              <article className="support-card settings-update-card">
                <span>Installed build</span>
                <strong>{resolvedBuild.build_label}</strong>
                <small>Version {resolvedBuild.version}</small>
              </article>
              <article className="support-card settings-update-card">
                <span>GitHub source</span>
                <strong>{resolvedBuild.update_source?.branch || "main"}</strong>
                <small>{resolvedBuild.update_source?.repo || "Unknown repository"}</small>
              </article>
              <article className="support-card settings-update-card">
                <span>Status</span>
                <strong>{updateState?.remote?.build_label || resolvedBuild.build_label}</strong>
                <small>{updateState?.checked_at ? `Checked ${formatTimestamp(updateState.checked_at)}` : "No remote check yet."}</small>
              </article>
            </div>
            <div className="settings-update-meta">
              <div className="settings-update-status">
                <StatusBadge
                  label={updateState?.status || "local-build"}
                  tone={updaterTone}
                />
                <span>
                  {updateState?.remote
                    ? `Remote version ${updateState.remote.version} · ${updateState.remote.build_label}`
                    : "Checks GitHub main and preserves data, secrets, tools, and your Python environment during update."}
                </span>
              </div>
              <div className="settings-form-actions">
                <button type="button" className="primary-button" onClick={() => void runUpdaterAction()} disabled={updaterBusy}>
                  {updaterButtonLabel}
                </button>
                <button type="button" className="utility-button" onClick={() => void runRollbackAction()} disabled={rollbackBusy || !latestBackup}>
                  {rollbackBusy ? "Rolling back" : "Rollback last update"}
                </button>
                {feedback.updater ? <StatusBadge label={feedback.updater.message} tone={feedback.updater.tone} /> : null}
              </div>
              <div className="settings-update-history">
                <article className="support-card settings-update-card settings-update-history-card">
                  <span>Last action</span>
                  <strong>{updateStatus.last_action?.action || "No update action yet"}</strong>
                  <small>{updateStatus.last_action?.message || "No updater activity has been recorded yet."}</small>
                  <small>
                    {updateStatus.last_action?.status
                      ? `${titleCase(updateStatus.last_action.status)} ${formatTimestamp(updateStatus.last_action.completed_at || updateStatus.last_action.requested_at || null)}`
                      : ""}
                  </small>
                </article>
                <article className="support-card settings-update-card settings-update-history-card">
                  <span>Latest backup</span>
                  <strong>{latestBackup?.source_build_label || latestBackup?.archive_name || "No backup yet"}</strong>
                  <small>{latestBackup ? `Created ${formatTimestamp(latestBackup.created_at)}` : "A code backup will be created automatically before update or rollback."}</small>
                  <small>{latestBackup?.archive_name || ""}</small>
                </article>
              </div>
            </div>
          </div>
        </section>

        <section className="panel-surface">
          <header className="panel-header">
            <div className="panel-copy">
              <p className="panel-eyebrow">Environment</p>
              <h2>App mode</h2>
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
      </div>

      <section className="panel-surface">
        <header className="panel-header">
          <div className="panel-copy">
            <p className="panel-eyebrow">Password</p>
            <h2>Change password</h2>
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
  );
}