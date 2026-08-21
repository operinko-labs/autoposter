import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useState,
  type ReactNode,
} from "react";

import {
  apiFetch,
  getToken,
  setToken,
  setUnauthorizedHandler,
} from "../api/client";
import type { LoginResponse } from "../api/types";

interface SessionValue {
  authenticated: boolean;
  login: (password: string) => Promise<void>;
  logout: () => Promise<void>;
}

const SessionContext = createContext<SessionValue | null>(null);

export function SessionProvider({ children }: { children: ReactNode }) {
  const [authenticated, setAuthenticated] = useState(() => getToken() !== null);

  useEffect(() => {
    // apiFetch clears the token and calls this whenever any request comes
    // back 401 -- an expired session, or one revoked from another tab. The
    // provider is the only subscriber, so every page drops to the login form
    // without having to handle 401 itself.
    setUnauthorizedHandler(() => setAuthenticated(false));
    return () => setUnauthorizedHandler(null);
  }, []);

  const login = useCallback(async (password: string) => {
    const response = await apiFetch<LoginResponse>("/api/login", {
      method: "POST",
      body: JSON.stringify({ password }),
    });
    setToken(response.token);
    setAuthenticated(true);
  }, []);

  const logout = useCallback(async () => {
    try {
      await apiFetch("/api/logout", { method: "POST" });
    } catch {
      // The server may already consider the session gone. Either way the
      // local half is dropped below -- failing to revoke remotely must not
      // leave the user stuck in a session they asked to end.
    }
    setToken(null);
    setAuthenticated(false);
  }, []);

  const value = useMemo(
    () => ({ authenticated, login, logout }),
    [authenticated, login, logout],
  );

  return <SessionContext.Provider value={value}>{children}</SessionContext.Provider>;
}

export function useSession(): SessionValue {
  const value = useContext(SessionContext);
  if (value === null) {
    throw new Error("useSession must be used inside a SessionProvider");
  }
  return value;
}
