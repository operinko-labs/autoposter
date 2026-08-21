import { useState, type FormEvent } from "react";

import { ApiError } from "../api/client";
import { useSession } from "../auth/SessionContext";
import "./login.css";

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
      // The server answers a wrong password and an unknown one identically,
      // and so does this: anything more specific tells an attacker which half
      // they got right.
      setError(
        caught instanceof ApiError && caught.status === 429
          ? "Too many attempts. Wait a moment and try again."
          : "Incorrect password.",
      );
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
