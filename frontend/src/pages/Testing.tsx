import { useEffect, useState } from "react";

import { apiPostForImage } from "../api/client";
import type { SampleTruncatedResponse } from "../api/types";
import "./testing.css";

/** The kinds a sample can be rendered for -- exactly SampleKind in
 * src/autoposter/api/testing.py. `logo` is deliberately absent: a logo is
 * composited over a poster and has no canvas of its own, so the endpoint 422s
 * it and there is nothing to render a sample of. */
const KINDS = ["poster", "season_poster", "background", "title_card"] as const;

/** The three fixed sample lengths the endpoint knows (SAMPLE_TITLES there):
 * a short title that fits large, a realistic one that wraps, and one too long
 * to fit at the minimum point size so the truncation outcome is reachable. */
const LENGTHS = ["short", "medium", "long"] as const;

type Length = (typeof LENGTHS)[number];

/** Posters and season posters are 2:3, backgrounds and title cards 16:9 -- the
 * same shapes ItemDetail's panes reserve, so a wide sample is not squeezed into
 * a poster's tall box. */
function ratioFor(artKind: string): "poster" | "wide" {
  return artKind === "poster" || artKind === "season_poster" ? "poster" : "wide";
}

function isTruncated(value: unknown): value is SampleTruncatedResponse {
  return (
    typeof value === "object" &&
    value !== null &&
    (value as SampleTruncatedResponse).truncated === true
  );
}

type CellState =
  | { status: "idle" }
  | { status: "loading" }
  | { status: "image" }
  /** The title did not fit, so the pipeline produced no artifact. This is an
   * OUTCOME the operator asked to see, not an error -- rendered as its own
   * labelled state. */
  | { status: "truncated" }
  | { status: "error"; message: string };

/** One cell of the grid: a Render button, and after a render the styled JPEG,
 * the truncation outcome, or an error.
 *
 * The image is its own endpoint's bytes fetched with the bearer header (a
 * `<img src>` cannot carry one), so it arrives as a Blob and is shown through
 * an object URL -- the same lifecycle ItemDetail's panes use. The URL is
 * created and revoked in an effect keyed on the blob, so a re-render (a new
 * blob) revokes the old one and unmounting revokes the last. */
function SampleCell({ artKind, length }: { artKind: string; length: Length }) {
  const [state, setState] = useState<CellState>({ status: "idle" });
  const [blob, setBlob] = useState<Blob | null>(null);
  const [url, setUrl] = useState<string | null>(null);

  useEffect(() => {
    if (blob === null) {
      setUrl(null);
      return;
    }
    const objectUrl = URL.createObjectURL(blob);
    setUrl(objectUrl);
    return () => URL.revokeObjectURL(objectUrl);
  }, [blob]);

  async function renderSample() {
    setState({ status: "loading" });
    // Drop the previous image before the next request: its object URL is
    // revoked by the effect above when the blob clears, so a re-render does not
    // leak the last decode.
    setBlob(null);
    try {
      const result = await apiPostForImage("/api/testing/sample", {
        art_kind: artKind,
        length,
      });
      if ("blob" in result) {
        setBlob(result.blob);
        setState({ status: "image" });
        return;
      }
      // The only non-image outcome the endpoint returns is the truncation
      // report. Shown as its own state -- NOT coerced into the error branch,
      // which would tell the operator something failed when the running system
      // would simply produce no artifact here.
      if (isTruncated(result.json)) {
        setState({ status: "truncated" });
        return;
      }
      setState({ status: "error", message: "the sample could not be rendered" });
    } catch (caught) {
      setState({ status: "error", message: (caught as Error).message });
    }
  }

  return (
    <div className="sample-cell">
      <div className="sample-view" data-ratio={ratioFor(artKind)}>
        {state.status === "image" && url !== null ? (
          <img className="sample-image" src={url} alt={`${artKind} ${length} sample`} />
        ) : (
          <div className="sample-blank">
            {state.status === "loading" ? (
              <p className="sample-note muted">Rendering…</p>
            ) : state.status === "truncated" ? (
              <p className="sample-note sample-truncated">
                The title did not fit — no artifact is produced.
              </p>
            ) : state.status === "error" ? (
              <p className="sample-note sample-error">{state.message}</p>
            ) : (
              <p className="sample-note muted">Not rendered yet.</p>
            )}
          </div>
        )}
      </div>
      <button
        type="button"
        disabled={state.status === "loading"}
        onClick={() => void renderSample()}
      >
        {state.status === "idle" ? "Render" : "Re-render"}
      </button>
    </div>
  );
}

export function Testing() {
  return (
    <>
      <div className="page-header">
        <h1>Testing</h1>
      </div>

      {/* The one thing this page has to say about itself: a sample is rendered
        * against the config the process is running right now, so an edit saved
        * in Settings shows up here on the next Render -- there is nothing to
        * reload. */}
      <p className="testing-note muted">
        Samples are rendered against the configuration this instance is running.
        Edit it in <span className="testing-strong">Settings</span>, then re-render
        here to see the change.
      </p>

      <div className="panel">
        {/* The kind × length grid scrolls inside its own container: at a phone
          * width four length columns of poster-shaped cells do not fit, and a
          * grid that widened .app-main would push the page sideways. */}
        <div className="table-scroll">
          <table className="testing-grid">
            <thead>
              <tr>
                <th>Art kind</th>
                {LENGTHS.map((length) => (
                  <th key={length}>{length}</th>
                ))}
              </tr>
            </thead>
            <tbody>
              {KINDS.map((artKind) => (
                <tr key={artKind}>
                  <th scope="row">{artKind}</th>
                  {LENGTHS.map((length) => (
                    <td key={length}>
                      <SampleCell artKind={artKind} length={length} />
                    </td>
                  ))}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>
    </>
  );
}
