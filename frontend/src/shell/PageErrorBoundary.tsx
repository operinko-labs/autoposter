import { Component, type ReactNode } from "react";

interface Props {
  children: ReactNode;
  /** The failure is forgotten when this changes -- the current path, so
   * following a sidebar link away from a broken page tries the next one. */
  resetKey: string;
}

interface State {
  failed: boolean;
  resetKey: string;
}

/** Catches a page that throws while rendering -- above all a lazy chunk that
 * no longer exists on the server after a deploy (perf spec A3) -- and says so
 * in the page area. Without it React 19 unmounts the whole root and the tab
 * goes blank, sidebar included. main.tsx's `vite:preloadError` handler
 * normally reloads first; this is what shows when it does not. */
export class PageErrorBoundary extends Component<Props, State> {
  state: State = { failed: false, resetKey: this.props.resetKey };

  static getDerivedStateFromError(): Partial<State> {
    return { failed: true };
  }

  static getDerivedStateFromProps(props: Props, state: State): Partial<State> | null {
    return props.resetKey === state.resetKey
      ? null
      : { failed: false, resetKey: props.resetKey };
  }

  render() {
    if (!this.state.failed) return this.props.children;
    return (
      <div className="page-error" role="alert">
        <p>This page failed to load.</p>
        <button type="button" onClick={() => window.location.reload()}>
          Reload
        </button>
      </div>
    );
  }
}
