import { useState } from "react";

interface LoginScreenProps {
  busy: boolean;
  error?: string | null;
  onSubmit: (username: string, password: string) => void;
}

export function LoginScreen({ busy, error, onSubmit }: LoginScreenProps) {
  const [username, setUsername] = useState("admin");
  const [password, setPassword] = useState("admin");

  return (
    <main className="login-shell">
      <section className="login-card">
        <p className="panel-eyebrow">Sign In</p>
        <h1>OmniBot Dashboard</h1>
        <p className="login-copy">
          Sign in to access your dashboard, bots, analytics, journal, and settings.
        </p>
        <form
          className="login-form"
          onSubmit={(event) => {
            event.preventDefault();
            onSubmit(username, password);
          }}
        >
          <label>
            <span>Username</span>
            <input value={username} onChange={(event) => setUsername(event.target.value)} autoComplete="username" />
          </label>
          <label>
            <span>Password</span>
            <input type="password" value={password} onChange={(event) => setPassword(event.target.value)} autoComplete="current-password" />
          </label>
          <button type="submit" className="primary-button" disabled={busy}>
            {busy ? "Signing in..." : "Sign In"}
          </button>
        </form>
        {error ? <p className="form-error">{error}</p> : null}
      </section>
    </main>
  );
}