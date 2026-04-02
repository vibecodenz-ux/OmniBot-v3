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
        <p className="panel-eyebrow">Secure Entry</p>
        <h1>OmniBot Control Deck</h1>
        <p className="login-copy">
          Sign in to the OmniBot v3 operator dashboard for live markets, analytics, journal activity, and runtime controls.
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