import { useState, type FormEvent } from "react";

import { ApiError } from "../api/client";
import { useSession } from "../auth/SessionContext";
import "./login.css";

export function loginErrorMessage(caught: unknown): string {
  if (!(caught instanceof ApiError)) {
    // fetch itself rejected: no route to the server, DNS, TLS, or a body the
    // client could not read.
    return "Could not reach the server. Check that it is running and try again.";
  }
  if (caught.status === 401) return "Incorrect password.";
  if (caught.status === 429) {
    return "Too many attempts. Wait a moment and try again.";
  }
  return `Could not reach the server (error ${caught.status}). Try again.`;
}

export function Login() {
  const { login } = useSession();
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function onSubmit(event: FormEvent) {
    event.preventDefault();
    setBusy(true);
    setError(null);
    try {
      await login(password);
    } catch (caught) {
      // Only a 401 means the credentials were wrong. Reporting a 500 or a
      // dropped connection as "Incorrect password." tells someone whose
      // server is down to go hunting for a typo. The don't-say-which-half
      // rule applies to the 401 itself -- every rejected password gets the
      // same message, whatever was wrong with it -- not to transport errors.
      setError(loginErrorMessage(caught));
      setPassword("");
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="login-screen">
      <form className="login-box" onSubmit={onSubmit}>
        <h1 className="login-title">Autoposter</h1>

        <label className="login-label" htmlFor="password">
          Password
        </label>
        <input
          id="password"
          type="password"
          autoComplete="current-password"
          autoFocus
          value={password}
          onChange={(event) => setPassword(event.target.value)}
        />

        {error !== null && (
          <p className="login-error" role="alert">
            {error}
          </p>
        )}

        <button className="primary" type="submit" disabled={busy || password === ""}>
          {busy ? "Signing in…" : "Sign in"}
        </button>
      </form>
    </div>
  );
}
