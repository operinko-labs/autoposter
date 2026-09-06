import { useEffect, useState } from "react";

import { fetchSetupState } from "../api/setup";

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
 * default -- it is exactly what the bundle did before this probe existed.
 */
export function useSetupProbe(): boolean | null {
  const [required, setRequired] = useState<boolean | null>(null);

  useEffect(() => {
    let cancelled = false;
    fetchSetupState()
      .then((state) => {
        if (!cancelled) setRequired(state.setup);
      })
      .catch(() => {
        if (!cancelled) setRequired(false);
      });
    return () => {
      cancelled = true;
    };
  }, []);

  return required;
}
