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
            <p className="panel-eyebrow">Something Went Wrong</p>
            <h1>Unable to load the page</h1>
            <p className="login-copy">The dashboard could not be loaded.</p>
            <p className="form-error">{this.state.error.message}</p>
          </section>
        </main>
      );
    }

    return this.props.children;
  }
}