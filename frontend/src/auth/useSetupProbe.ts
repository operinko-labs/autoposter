import { useEffect, useState } from "react";

import { fetchSetupState } from "../api/setup";

/** How long an unanswered probe is allowed to block the login screen. Long
 * enough that a merely slow cold start still resolves before it fires, short
 * enough that a stalled connection cannot leave a configured deployment
 * looking dead instead of unreachable. */
const PROBE_TIMEOUT_MS = 3000;

/** Whether this deployment still needs its first-start wizard.
 *
 * `null` while the answer is unknown. This is the bundle's FIRST server probe
 * on load -- the session gate has always decided entirely client-side, off a
 * sessionStorage token -- and it exists because an unconfigured deployment has
 * no session to have stored and the login form it would otherwise render can
 * never succeed there.
 *
 * A failed probe resolves to `false`, not to an error state: an older server
 * has no such route, and rendering the login page is the correct, closed
 * default -- it is exactly what the bundle did before this probe existed. A
 * probe that hangs rather than rejects -- a reverse proxy holding the
 * connection, a pod mid-cold-start -- is forced to the same closed default by
 * an `AbortController` timeout, so a stall reads as "unreachable" rather than
 * blanking a configured deployment's login page indefinitely.
 */
export function useSetupProbe(): boolean | null {
  const [required, setRequired] = useState<boolean | null>(null);

  useEffect(() => {
    let cancelled = false;
    const controller = new AbortController();
    const timeout = window.setTimeout(() => controller.abort(), PROBE_TIMEOUT_MS);

    fetchSetupState({ signal: controller.signal })
      .then((state) => {
        if (!cancelled) setRequired(state.setup);
      })
      .catch(() => {
        if (!cancelled) setRequired(false);
      })
      .finally(() => window.clearTimeout(timeout));

    return () => {
      cancelled = true;
      controller.abort();
      window.clearTimeout(timeout);
    };
  }, []);

  return required;
}
