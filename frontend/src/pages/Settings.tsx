import { useEffect, useState } from "react";

import { apiFetch } from "../api/client";
import type { ConfigResponse } from "../api/types";
import "./settings.css";

/** Verbatim, because TMDB's terms specify the wording. Do not paraphrase. */
export const TMDB_NOTICE =
  "This product uses TMDB and the TMDB APIs but is not endorsed, certified, " +
  "or otherwise approved by TMDB.";

/** TheTVDB's terms require attribution carrying a direct link to their site. */
export const TVDB_NOTICE =
  "Metadata provided by TheTVDB. Please consider adding missing information " +
  "or subscribing.";

export function Settings() {
  const [config, setConfig] = useState<ConfigResponse | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    apiFetch<ConfigResponse>("/api/config")
      .then((response) => {
        if (!cancelled) setConfig(response);
      })
      .catch((caught: Error) => {
        if (!cancelled) setError(caught.message);
      });
    return () => {
      cancelled = true;
    };
  }, []);

  return (
    <>
      <div className="page-header">
        <h1>Settings</h1>
      </div>

      {error !== null && <p className="page-error">{error}</p>}

      <section className="panel attribution">
        <h2>Metadata providers</h2>

        {/* Required attribution, not decoration. Both providers' terms make
            this a condition of using their API from a user-facing surface,
            which is what this page became the moment it shipped. */}
        <div className="provider">
          <img
            className="provider-logo"
            src="/tmdb-logo.png"
            alt="TMDB"
            width={72}
            height={38}
          />
          <p className="provider-notice">{TMDB_NOTICE}</p>
        </div>

        <div className="provider">
          <p className="provider-notice">
            {TVDB_NOTICE}{" "}
            <a href="https://thetvdb.com" target="_blank" rel="noreferrer noopener">
              TheTVDB.com
            </a>
          </p>
        </div>

        <p className="provider-notice muted">
          Artwork is also sourced from Fanart.tv, which sets no attribution
          requirement for personal API keys.
        </p>
      </section>

      <section className="panel">
        <h2>Running configuration</h2>
        <p className="muted">
          Read-only. Every secret is redacted by the server and never sent to
          this page. Editing arrives with the config endpoint in a later phase.
        </p>
        {config === null ? (
          <p className="muted">Loading…</p>
        ) : (
          <pre className="config-dump mono">{JSON.stringify(config, null, 2)}</pre>
        )}
      </section>
    </>
  );
}
