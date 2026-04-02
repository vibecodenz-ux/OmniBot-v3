import type { PropsWithChildren, ReactNode } from "react";
import { Component } from "react";

interface ErrorBoundaryState {
  error: Error | null;
}

interface ErrorBoundaryProps extends PropsWithChildren {
  fallback?: (error: Error) => ReactNode;
}

export class ErrorBoundary extends Component<ErrorBoundaryProps, ErrorBoundaryState> {
  state: ErrorBoundaryState = { error: null };

  static getDerivedStateFromError(error: Error): ErrorBoundaryState {
    return { error };
  }

  override render() {
    if (this.state.error) {
      if (this.props.fallback) {
        return this.props.fallback(this.state.error);
      }

      return (
        <main className="login-shell">
          <section className="login-card">
            <p className="panel-eyebrow">Frontend Error</p>
            <h1>React migration preview failed to render</h1>
            <p className="login-copy">The page hit a runtime error while rendering the dashboard shell.</p>
            <p className="form-error">{this.state.error.message}</p>
          </section>
        </main>
      );
    }

    return this.props.children;
  }
}