import { useEffect, useState } from "react";

import { apiFetch } from "../api/client";
import type { ConfigResponse } from "../api/types";
/** Imported rather than referenced as `/tmdb-logo.png` from `public/`. Vite
 * copies `public/` to the *root* of `dist/`, which `src/autoposter/api/spa.py`
 * does not serve -- it mounts `/assets` and answers everything else with
 * index.html, so the logo would come back as a 200 of HTML and render broken
 * with nothing failing loudly. Importing it puts the file under `/assets/`
 * with a content hash, where the mount already serves it. */
import tmdbLogo from "../assets/tmdb-logo.png";
import "./settings.css";

/** Verbatim, because TMDB's terms specify the wording. Do not paraphrase. */
export const TMDB_NOTICE =
  "This product uses TMDB and the TMDB APIs but is not endorsed, certified, " +
  "or otherwise approved by TMDB.";

/** TheTVDB's terms require attribution carrying a direct link to their site. */
export const TVDB_NOTICE =
  "Metadata provided by TheTVDB. Please consider adding missing information " +
  "or subscribing.";

/** The server's redaction marker (routes.py::get_config). It is a state, not
 * a value, so the renderer shows it as a badge rather than the raw string. */
const REDACTED_MARKER = "***REDACTED***";

/** snake_case -> "Snake case". Derived, never looked up: the config schema
 * grows every phase, and a label table would drift. */
function labelFor(key: string): string {
  const words = key.replace(/_/g, " ");
  return words.charAt(0).toUpperCase() + words.slice(1);
}

function isPlainObject(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function ScalarValue({ value }: { value: unknown }) {
  if (value === REDACTED_MARKER) {
    return <span className="config-pill redacted">redacted</span>;
  }
  if (typeof value === "boolean") {
    return (
      <span className={`config-pill ${value ? "on" : "off"}`}>
        {value ? "on" : "off"}
      </span>
    );
  }
  if (value === "" || value === null) {
    return <span className="muted">(not set)</span>;
  }
  return <>{String(value)}</>;
}

function ListValue({ value }: { value: unknown[] }) {
  if (value.length === 0) {
    return <span className="muted">(none)</span>;
  }
  // A list of scalars reads best as a single joined value; a list holding
  // objects gets one row per entry instead.
  if (value.every((item) => !isPlainObject(item) && !Array.isArray(item))) {
    return <>{value.map(String).join(", ")}</>;
  }
  return (
    <ol className="config-list">
      {value.map((item, index) => (
        <li key={index}>
          {isPlainObject(item) ? (
            <ConfigNode value={item} />
          ) : Array.isArray(item) ? (
            <ListValue value={item} />
          ) : (
            <ScalarValue value={item} />
          )}
        </li>
      ))}
    </ol>
  );
}

/** Recursive renderer driven entirely by the response's shape: scalars and
 * lists become label/value rows, nested objects become indented subsections.
 * Nothing here names a config field, so a new key appears without a frontend
 * change. */
function ConfigNode({ value }: { value: Record<string, unknown> }) {
  return (
    <div className="config-node">
      {Object.entries(value).map(([key, entry]) =>
        isPlainObject(entry) ? (
          <div className="config-subsection" key={key}>
            <h3>{labelFor(key)}</h3>
            <ConfigNode value={entry} />
          </div>
        ) : (
          <div className="config-row" key={key}>
            <span className="config-key">{labelFor(key)}</span>
            <span className="config-value">
              {Array.isArray(entry) ? (
                <ListValue value={entry} />
              ) : (
                <ScalarValue value={entry} />
              )}
            </span>
          </div>
        ),
      )}
    </div>
  );
}

/** One titled panel per top-level object, in the server's own key order --
 * which is the config model's declaration order, the same order the example
 * YAML documents. Two documented exceptions to pure shape-driven rendering:
 * top-level scalars (assets_root, workers, ...) have no section of their own,
 * so they are gathered into a leading "General" panel; and `secrets` is
 * pinned last -- the server already appends it last, but an all-redacted
 * panel drifting into the middle of the page on a server refactor would be a
 * regression worth defending against here. */
function ConfigSections({ config }: { config: ConfigResponse }) {
  const entries = Object.entries(config);
  const general = entries.filter(([, value]) => !isPlainObject(value));
  const sections = entries
    .filter((entry): entry is [string, Record<string, unknown>] =>
      isPlainObject(entry[1]),
    )
    // Array.prototype.sort is stable, so everything but `secrets` keeps the
    // server's order.
    .sort(([a], [b]) => Number(a === "secrets") - Number(b === "secrets"));

  return (
    <>
      {general.length > 0 && (
        <section className="panel config-section">
          <h2>General</h2>
          <ConfigNode value={Object.fromEntries(general)} />
        </section>
      )}
      {sections.map(([key, value]) => (
        <section className="panel config-section" key={key}>
          <h2>{labelFor(key)}</h2>
          <ConfigNode value={value} />
        </section>
      ))}
    </>
  );
}

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
        {/* TMDB's terms require their logo to be less prominent than this
            application's own branding. The sidebar wordmark is not a sound
            basis for that comparison from this page (see settings.css), so
            this page carries its own, directly above the logo it is being
            weighed against. */}
        <p className="brand-mark">Autoposter</p>

        <h2>Metadata providers</h2>

        {/* Required attribution, not decoration. Both providers' terms make
            this a condition of using their API from a user-facing surface,
            which is what this page became the moment it shipped. */}
        <div className="provider">
          <img
            className="provider-logo"
            src={tmdbLogo}
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
        <p className="muted config-note">
          Read-only. Every secret is redacted by the server and never sent to
          this page. Editing arrives with the config editor (phase 6c).
        </p>
        {config === null && <p className="muted">Loading…</p>}
      </section>
      {config !== null && <ConfigSections config={config} />}
    </>
  );
}
