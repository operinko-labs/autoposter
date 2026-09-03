import { Fragment, useEffect, useState } from "react";
import { Link, useParams } from "react-router-dom";

import { ProviderAttribution } from "../ProviderAttribution";
import { ApiError, apiFetch, apiFetchImage } from "../api/client";
import type {
  ArtCandidate,
  CandidatesResponse,
  ClearOverrideResponse,
  ItemDetailResponse,
  ItemRender,
  ManualInstallResponse,
  PickResponse,
  ReprocessResponse,
} from "../api/types";
import { artKindFor } from "../artKind";
import { formatTime } from "../format";
import "./item.css";

/** The shape of the pane box, matched by art kind in item.css: posters are
 * 2:3, backgrounds and title cards 16:9. Without this an episode's panes
 * letterbox a 16:9 title card at a third of a tall poster box -- on a page
 * whose whole point is comparing the two images by eye. */
function ratioFor(artKind: string): "poster" | "wide" {
  return artKind === "poster" || artKind === "season_poster" ? "poster" : "wide";
}

/** "S02E26" -- mirrors the project's own zero-padded convention
 * (src/autoposter/badges/values.py's episode_text). Null unless both numbers
 * are present, which for a well-formed episode row always holds; a missing
 * one degrades to no code rather than a half-built string. */
function seasonEpisodeCode(seasonNumber: number | null, episodeNumber: number | null): string | null {
  if (seasonNumber === null || episodeNumber === null) return null;
  return `S${String(seasonNumber).padStart(2, "0")}E${String(episodeNumber).padStart(2, "0")}`;
}

/** "S02" -- the season-page analogue of seasonEpisodeCode. */
function seasonCode(seasonNumber: number | null): string | null {
  return seasonNumber === null ? null : `S${String(seasonNumber).padStart(2, "0")}`;
}

/** A fingerprint is a 64-character hex digest. Shown whole it pushes every
 * other column off a laptop screen, and no one reads one -- they compare two.
 * A prefix is enough to compare by eye, and the full value is on the title. */
const FINGERPRINT_PREFIX = 12;

type ArtworkState =
  | { status: "loading" }
  | { status: "image"; url: string }
  /** A 404. Both endpoints use it for "there is legitimately nothing here",
   * which is the ordinary state of an item this project has not reached. */
  | { status: "absent" }
  | { status: "error"; code: number; message: string };

/** Fetches image bytes and hands them to an <img> as an object URL.
 *
 * The indirection is not decoration: `<img src="/api/items/3/artwork/poster">`
 * cannot work, because require_session (src/autoposter/api/auth.py) accepts
 * the session only as an `Authorization: Bearer` header and a browser sends no
 * such header for an image it loads itself. There is no cookie to fall back
 * on. So `apiFetchImage` fetches with the header and this turns the blob into
 * a URL the <img> can use.
 */
function useArtwork(path: string): {
  state: ArtworkState;
  onImageError: () => void;
} {
  const [state, setState] = useState<ArtworkState>({ status: "loading" });

  useEffect(() => {
    let cancelled = false;
    let objectUrl: string | null = null;
    setState({ status: "loading" });

    apiFetchImage(path)
      .then((blob) => {
        // Before createObjectURL, never after: a URL created for an unmounted
        // pane has no one left to revoke it and leaks the decoded image for
        // the life of the document.
        if (cancelled) return;
        if (blob === null) {
          setState({ status: "absent" });
          return;
        }
        objectUrl = URL.createObjectURL(blob);
        setState({ status: "image", url: objectUrl });
      })
      .catch((caught: Error) => {
        if (cancelled) return;
        setState({
          status: "error",
          code: caught instanceof ApiError ? caught.status : 0,
          message: caught.message,
        });
      });

    return () => {
      cancelled = true;
      if (objectUrl !== null) URL.revokeObjectURL(objectUrl);
    };
  }, [path]);

  /** Corrupt or truncated bytes decode to nothing, and an <img> left pointing
   * at them paints the browser's broken-image icon -- the exact thing the
   * panes' blank state exists to avoid (the server 404s an *empty* Plex body,
   * but cannot vouch that non-empty bytes decode). Lives here rather than in
   * the panes because this hook owns the object URL and the state: only it
   * can revoke the one and move the other out of "image". */
  function onImageError() {
    if (state.status !== "image") return;
    URL.revokeObjectURL(state.url);
    setState({
      status: "error",
      code: 0,
      message: "The image data could not be decoded.",
    });
  }

  return { state, onImageError };
}

/** The image this project rendered and holds on disk. */
function BasePane({ itemId, artKind }: { itemId: number; artKind: string }) {
  const { state, onImageError } = useArtwork(`/api/items/${itemId}/artwork/${artKind}`);

  return (
    <figure className="art-pane" data-ratio={ratioFor(artKind)}>
      <figcaption>
        Base image <span className="muted">on disk, before badges</span>
      </figcaption>
      {state.status === "image" ? (
        <img className="art-image" src={state.url} alt="" onError={onImageError} />
      ) : (
        // Deliberately not an <img> with a src that cannot load: that renders
        // the browser's broken-image icon, which says nothing about why.
        <div className="art-blank">
          <p className="art-note">
            {state.status === "loading"
              ? "Loading…"
              : state.status === "absent"
                ? "Nothing has been rendered for this item yet."
                : "The base image could not be loaded."}
          </p>
          {state.status === "error" && (
            <p className="art-detail muted">{state.message}</p>
          )}
        </div>
      )}
    </figure>
  );
}

/** What Plex is actually serving right now.
 *
 * The two failures here are the reason this page exists, and they are not the
 * same failure: "Plex has no artwork" means this item's upload never happened
 * or was undone, which is a fact about the library. "Plex cannot be reached"
 * means this page knows nothing at all, which is a fact about the connection.
 * Collapsing them into one "unavailable" would have the user hunting a
 * missing upload while their Plex server is down.
 */
function LivePane({ itemId, artKind }: { itemId: number; artKind: string }) {
  const { state, onImageError } = useArtwork(`/api/items/${itemId}/artwork/${artKind}/live`);

  return (
    <figure className="art-pane" data-ratio={ratioFor(artKind)}>
      <figcaption>
        Live in Plex <span className="muted">badged, as uploaded</span>
      </figcaption>
      {state.status === "image" ? (
        <img className="art-image" src={state.url} alt="" onError={onImageError} />
      ) : (
        <div className="art-blank">
          <p className="art-note">
            {state.status === "loading"
              ? "Loading…"
              : state.status === "absent"
                ? "Plex is not serving any artwork of this kind."
                : state.code === 503
                  ? "Plex could not be reached, so what it is serving is unknown."
                  : "The live image could not be loaded."}
          </p>
          {state.status === "error" && (
            <p className="art-detail muted">{state.message}</p>
          )}
        </div>
      )}
    </figure>
  );
}

/** Truncated, with the whole digest on hover. */
function Fingerprint({ value }: { value: string | null }) {
  if (value === null || value === "") return <span className="muted">—</span>;
  return (
    <span className="mono fingerprint" title={value}>
      {value.slice(0, FINGERPRINT_PREFIX)}…
    </span>
  );
}

function Facts({ facts }: { facts: ItemDetailResponse["facts"] }) {
  if (facts === null) {
    return <p className="empty">No facts have been collected for this item yet.</p>;
  }

  return (
    <dl className="fact-list">
      <dt>Critic rating</dt>
      <dd>{facts.critic_rating ?? "—"}</dd>
      <dt>Audience rating</dt>
      <dd>{facts.audience_rating ?? "—"}</dd>
      <dt>Content rating</dt>
      <dd>{facts.content_rating ?? "—"}</dd>
      <dt>Studio</dt>
      <dd>{facts.studio ?? "—"}</dd>
      <dt>Genres</dt>
      <dd>{facts.genres.length === 0 ? "—" : facts.genres.join(", ")}</dd>
      {/* A plain date, not a timestamp: formatTime would read "2019-06-28" as
          UTC midnight and print an hour the API never said anything about. */}
      <dt>Originally available</dt>
      <dd>{facts.originally_available ?? "—"}</dd>
    </dl>
  );
}

/** What the last clear-override attempt did, and which row it was about.
 *
 * Keyed by art kind rather than shown page-wide: a movie has two render rows,
 * and "no manual override for this art kind" floating above the table says
 * nothing about which kind the server meant. */
interface ClearNote {
  artKind: string;
  failed: boolean;
  message: string;
}

/** The provider that supplied a row's artwork, plus the control that takes a
 * manual one out of play.
 *
 * `provider === "manual"` is the database's ONLY trace of an override file --
 * the pipeline stamps it on every pass that finds one -- so it is the whole
 * test for whether there is anything to clear. */
function Provider({
  render,
  busy,
  onClear,
}: {
  render: ItemRender;
  busy: boolean;
  onClear: (artKind: string) => void;
}) {
  if (render.provider === null || render.provider === "") {
    return <span className="muted">—</span>;
  }
  if (render.provider !== "manual") return <>{render.provider}</>;

  return (
    <>
      {render.provider}{" "}
      <button
        type="button"
        className="clear-override"
        disabled={busy}
        onClick={() => onClear(render.art_kind)}
      >
        Clear override
      </button>
    </>
  );
}

/** A provider URL reduced to its host, and anything else left exactly as it is.
 *
 * Under a manual override `source_url` is not a URL at all: the pipeline stamps
 * the absolute path of the override file on the operator's mount. `new URL`
 * throws on it (and a drive-lettered path parses as a scheme with no host),
 * so both cases fall through to the value itself. */
function hostOrPath(value: string): string {
  try {
    const { host } = new URL(value);
    if (host !== "") return host;
  } catch {
    // Not a URL. A path, then.
  }
  return value;
}

/** Where a row's base image came from.
 *
 * Never a link and never an image source. Half the values here are provider
 * URLs and half are filesystem paths on a mount this browser cannot reach, and
 * text is the only rendering that is true of both -- on a page that legitimately
 * hot-links provider thumbnails a few lines away, which is exactly how the two
 * get confused. The host alone, because the table is nowrap and a full TMDB URL
 * pushes every other column off a laptop screen; the whole value is on the
 * title. */
function Source({ render }: { render: ItemRender }) {
  if (render.source_url === null || render.source_url === "") {
    return <span className="muted">—</span>;
  }
  return (
    <>
      <span className="source-url" title={render.source_url}>
        {hostOrPath(render.source_url)}
      </span>
      {/* Only when the provider said so. Null is "no candidate was asked",
        * which is the state under an override, and showing it as either answer
        * would be inventing provenance. */}
      {render.textless === true && <span className="textless-badge"> textless</span>}
    </>
  );
}

function Renders({
  renders,
  clearing,
  note,
  onClear,
}: {
  renders: ItemRender[];
  clearing: string | null;
  note: ClearNote | null;
  onClear: (artKind: string) => void;
}) {
  if (renders.length === 0) {
    return <p className="empty">This item has never been rendered.</p>;
  }

  return (
    /* Nine columns, every cell nowrapped: this table is wider than a phone
     * viewport by construction. It gets its own horizontal scrollbar so the
     * page around it stays put. */
    <div className="table-scroll">
      <table className="render-table">
        <thead>
          <tr>
            <th>Art</th>
            <th>Status</th>
            <th>Provider</th>
            <th>Source</th>
            <th>Fingerprint</th>
            <th>Badge fingerprint</th>
            <th>Upload</th>
            <th>Rendered</th>
            <th>Uploaded</th>
          </tr>
        </thead>
        <tbody>
          {renders.map((render) => (
            <Fragment key={render.art_kind}>
              <tr>
                <td>{render.art_kind}</td>
                <td>{render.status}</td>
                <td>
                  <Provider
                    render={render}
                    // Every button, not only this one: the endpoint moves a file
                    // and is not idempotent, so two clears in flight at once is
                    // not a state worth being able to reach.
                    busy={clearing !== null}
                    onClear={onClear}
                  />
                </td>
                <td>
                  <Source render={render} />
                </td>
                <td>
                  <Fingerprint value={render.fingerprint} />
                </td>
                <td>
                  <Fingerprint value={render.badge_fingerprint} />
                </td>
                <td>{render.upload_status}</td>
                <td className="muted cell-time">{formatTime(render.rendered_at)}</td>
                <td className="muted cell-time">{formatTime(render.uploaded_at)}</td>
              </tr>
              {note !== null && note.artKind === render.art_kind && (
                <tr>
                  <td colSpan={9}>
                    <p className={note.failed ? "render-error" : "render-note"}>
                      {note.message}
                    </p>
                  </td>
                </tr>
              )}
            </Fragment>
          ))}
        </tbody>
      </table>
    </div>
  );
}

/** The item kinds whose poster render composites a clearlogo, and therefore the
 * only ones a picked logo would ever be consumed by. Mirrors
 * LOGO_BROWSABLE_ITEM_KINDS in src/autoposter/api/candidates.py, which 404s a
 * logo browse for anything else. */
const LOGO_BROWSABLE_KINDS = ["movie", "show"];

/** Which section has a panel open, and what that panel is browsing.
 *
 * The two are not the same: a logo is browsed from the poster section, because
 * a logo has no section of its own -- there is no render row for one. */
interface Browsing {
  section: string;
  artKind: string;
}

type PanelState =
  | { status: "loading" }
  | { status: "error"; message: string }
  | { status: "ready"; response: CandidatesResponse };

function dimensions(candidate: ArtCandidate): string {
  if (candidate.width === null || candidate.height === null) return "size unknown";
  return `${candidate.width}×${candidate.height}`;
}

/** Every provider's artwork for one art kind, with a control that takes one.
 *
 * An inline panel rather than a modal: the SPA has no overlay anywhere, and the
 * grid is worth reading beside the panes it is going to replace.
 */
function CandidatePanel({
  itemId,
  artKind,
  onPicked,
}: {
  itemId: number;
  artKind: string;
  onPicked: () => Promise<void>;
}) {
  const [state, setState] = useState<PanelState>({ status: "loading" });
  /** True while a pick is in flight. A pick overwrites a file outright, so two
   * at once for one art kind is a race over which image the operator ends up
   * with -- every button goes down, not only the one clicked. */
  const [picking, setPicking] = useState(false);
  const [note, setNote] = useState<string | null>(null);
  const [failure, setFailure] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    setState({ status: "loading" });
    apiFetch<CandidatesResponse>(`/api/items/${itemId}/candidates/${artKind}`)
      .then((response) => {
        if (!cancelled) setState({ status: "ready", response });
      })
      .catch((caught: Error) => {
        if (!cancelled) setState({ status: "error", message: caught.message });
      });
    return () => {
      cancelled = true;
    };
  }, [itemId, artKind]);

  async function pick(candidate: ArtCandidate) {
    setPicking(true);
    setNote(null);
    setFailure(null);
    try {
      const response = await apiFetch<PickResponse>(
        `/api/items/${itemId}/candidates/${artKind}/pick`,
        {
          method: "POST",
          // The full-size `url`, never the `thumb_url` this tile is showing.
          // The endpoint treats the URL as a claim and re-runs the provider
          // fan-out to check it, so a thumbnail URL -- which no provider
          // offered -- is refused with a 422.
          body: JSON.stringify({ provider: candidate.provider, url: candidate.url }),
        },
      );
      // Re-read before reporting: the row's provider has just become "manual"
      // and its fingerprints were nulled server-side, so both the item (the
      // Renders table) and this panel's own candidates (the is-current tile
      // and the replaces-override warning) are stale the moment this returns.
      const [, refreshed] = await Promise.all([
        onPicked(),
        apiFetch<CandidatesResponse>(`/api/items/${itemId}/candidates/${artKind}`),
      ]);
      setState({ status: "ready", response: refreshed });
      setNote(
        response.queued
          ? "Picked. The image was written to the mount and a re-render was queued."
          : "Picked. The image was written to the mount; a re-render was already pending, so nothing new was added.",
      );
    } catch (caught) {
      // Verbatim: a 422 says the server was not offering that image, a 502
      // names the provider that would not serve it, and a 503 carries the OS
      // error for the mount. This page is behind require_session.
      setFailure((caught as Error).message);
    } finally {
      setPicking(false);
    }
  }

  const current = state.status === "ready" ? state.response.current : null;
  /** A pick overwrites an existing override with no backup kept -- deliberately:
   * the mount is the operator's, not this service's to version. The control has
   * to say so before it is clicked, because nothing afterwards can. */
  const pickTitle =
    current?.provider === "manual"
      ? "Picking replaces the current override — the previous file is not kept."
      : undefined;

  return (
    <div className="panel candidate-panel">
      <h3 className="candidate-heading">{artKind} candidates</h3>

      {state.status === "loading" && <p className="muted">Loading…</p>}
      {state.status === "error" && <p className="candidate-error">{state.message}</p>}

      {state.status === "ready" && (
        <>
          {/* Beside the tiles, never instead of them: a failing provider costs
            * its own rows only, and a partial list is the normal result. The
            * server sends the exception's class name rather than a sentence --
            * shown as one it would read as this page's own diagnosis. */}
          {Object.keys(state.response.errors).length > 0 && (
            <ul className="candidate-errors">
              {Object.entries(state.response.errors).map(([provider, failed]) => (
                <li key={provider}>{`${provider} unavailable (${failed})`}</li>
              ))}
            </ul>
          )}

          {state.response.candidates.length === 0 ? (
            <p className="empty">No provider offered artwork of this kind.</p>
          ) : (
            <ul className="candidate-grid">
              {state.response.candidates.map((candidate) => {
                const isCurrent =
                  current !== null && current.source_url === candidate.url;
                return (
                  <li
                    key={`${candidate.provider} ${candidate.url}`}
                    className={isCurrent ? "candidate-tile is-current" : "candidate-tile"}
                  >
                    {/* A plain <img src>, and the only place on this page where
                      * that is correct: provider image URLs are public and
                      * unauthenticated. Our own artwork endpoints accept the
                      * session as a bearer header only, which a browser does
                      * not send for an image it loads itself -- those go
                      * through apiFetchImage. */}
                    <img
                      className="candidate-thumb"
                      src={candidate.thumb_url}
                      loading="lazy"
                      alt=""
                    />
                    <p className="candidate-meta">
                      <span className="candidate-provider">{candidate.provider}</span>
                      {" · "}
                      {candidate.language ?? "no language"}
                      {" · "}
                      {dimensions(candidate)}
                      {candidate.includes_text === false && (
                        <>
                          {" · "}
                          <span className="candidate-textless">textless</span>
                        </>
                      )}
                    </p>
                    {isCurrent && <p className="candidate-current">in use</p>}
                    <button
                      type="button"
                      className="pick"
                      disabled={picking}
                      title={pickTitle}
                      onClick={() => void pick(candidate)}
                    >
                      Pick
                    </button>
                  </li>
                );
              })}
            </ul>
          )}

          {note !== null && <p className="candidate-note">{note}</p>}
          {failure !== null && <p className="candidate-error">{failure}</p>}

          {/* Required by TMDB's and TheTVDB's terms, and required HERE: this
            * panel is the surface showing their artwork as theirs, and an
            * operator can browse candidates for a whole evening without ever
            * opening Settings. */}
          <div className="attribution candidate-attribution">
            <ProviderAttribution />
          </div>
        </>
      )}
    </div>
  );
}

/** Installs an operator-supplied image as this art kind's base artwork.
 *
 * The manual-mode counterpart to CandidatePanel: rather than picking one of a
 * provider's offered images, the operator names a source of their own -- a URL,
 * or a path on the manual-assets mount. The endpoint (src/autoposter/api/manual.py)
 * guards a URL against SSRF and contains a path to the mount, then runs the same
 * install-and-enqueue tail a pick does. An inline panel, not a modal, matching
 * the idiom the browse panels already use here.
 */
function ManualSourcePanel({
  itemId,
  artKind,
  onInstalled,
}: {
  itemId: number;
  artKind: string;
  onInstalled: () => Promise<void>;
}) {
  const [source, setSource] = useState("");
  const [installing, setInstalling] = useState(false);
  const [note, setNote] = useState<string | null>(null);
  const [failure, setFailure] = useState<string | null>(null);

  async function install() {
    setInstalling(true);
    setNote(null);
    setFailure(null);
    try {
      const response = await apiFetch<ManualInstallResponse>(
        `/api/items/${itemId}/renders/${artKind}/manual`,
        {
          method: "POST",
          // The one field the endpoint reads, `source`. A URL and a mount path
          // are told apart by their own shape server-side, so there is no
          // second field to set -- and sending any other field name is a 422
          // there.
          body: JSON.stringify({ source }),
        },
      );
      // Re-read before reporting: the row's provider has just become "manual"
      // and its fingerprints were nulled server-side, so the Renders table and
      // the Clear override control are stale until the item is read again.
      await onInstalled();
      setNote(
        response.queued
          ? "Installed. The image was written to the mount and a re-render was queued."
          : "Installed. The image was written to the mount; a re-render was already pending, so nothing new was added.",
      );
    } catch (caught) {
      // Verbatim: the guard's refusals carry a reason with no URL in them, a
      // 422 names a bad mount path, and a 502 says the URL would not serve an
      // image. This page is behind require_session.
      setFailure((caught as Error).message);
    } finally {
      setInstalling(false);
    }
  }

  return (
    <div className="panel manual-panel">
      <h3 className="candidate-heading">{artKind} — use a file or URL</h3>
      <p className="manual-help muted">
        Paste an <span className="mono">https://…</span> URL, or a path under{" "}
        <span className="mono">/manualassets</span>.
      </p>
      <div className="manual-controls">
        <input
          type="text"
          className="manual-input"
          value={source}
          placeholder="https://… or /manualassets/…"
          onChange={(event) => setSource(event.target.value)}
        />
        <button
          type="button"
          disabled={installing || source.trim() === ""}
          onClick={() => void install()}
        >
          Install
        </button>
      </div>
      {note !== null && <p className="candidate-note">{note}</p>}
      {failure !== null && <p className="candidate-error">{failure}</p>}
    </div>
  );
}

export function ItemDetail() {
  const { itemId } = useParams();
  const [item, setItem] = useState<ItemDetailResponse | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [outcome, setOutcome] = useState<string | null>(null);
  /** The art kind whose clear-override call is in flight, or null. */
  const [clearing, setClearing] = useState<string | null>(null);
  const [clearNote, setClearNote] = useState<ClearNote | null>(null);
  /** The one open candidate panel, or null. One at a time: each open panel
   * costs a fan-out across every provider for this item. */
  const [browsing, setBrowsing] = useState<Browsing | null>(null);
  /** The one open manual-source panel, or null. Keyed the same way as
   * `browsing` -- a logo is installed from the poster section, as it is
   * browsed from there. */
  const [manualing, setManualing] = useState<Browsing | null>(null);

  useEffect(() => {
    let cancelled = false;
    setItem(null);
    setOutcome(null);
    setClearNote(null);
    // A panel opened on the previous item browses the previous item's
    // candidates; left open it would offer a pick against this one.
    setBrowsing(null);
    setManualing(null);
    // Cleared here, not only on success: navigating from a failed item to a
    // good one must not show the previous item's error while loading.
    setError(null);
    apiFetch<ItemDetailResponse>(`/api/items/${itemId}`)
      .then((response) => {
        if (cancelled) return;
        setItem(response);
        setError(null);
      })
      .catch((caught: Error) => {
        if (!cancelled) setError(caught.message);
      });
    return () => {
      cancelled = true;
    };
  }, [itemId]);

  async function reprocess() {
    setBusy(true);
    setOutcome(null);
    setError(null);
    try {
      const response = await apiFetch<ReprocessResponse>(
        `/api/items/${itemId}/reprocess`,
        { method: "POST" },
      );
      // What the queue says happened, not what the click hoped for. The
      // endpoint de-duplicates on the intent's dedupe_key, so asking twice
      // while the first job is still pending queues nothing the second time
      // -- and telling the user it did would have them waiting for a job that
      // does not exist.
      setOutcome(
        response.queued
          ? `Queued as job #${response.job_id}.`
          : "Already queued — nothing new was added.",
      );
    } catch (caught) {
      setError((caught as Error).message);
    } finally {
      setBusy(false);
    }
  }

  /** Takes one hand-placed override out of play.
   *
   * The endpoint renames the file to `<name>.disabled` and clears the row's
   * fingerprints, so the table on screen is stale the moment it returns --
   * hence the re-read rather than a local edit. It is deliberately NOT
   * idempotent: a second call 409s on a file that is no longer where it was,
   * which is why the button is disabled for the duration.
   */
  async function clearOverride(artKind: string) {
    setClearing(artKind);
    setClearNote(null);
    try {
      const response = await apiFetch<ClearOverrideResponse>(
        `/api/items/${itemId}/renders/${artKind}/clear-override`,
        { method: "POST" },
      );
      // Re-read before reporting: the fingerprints the table is showing were
      // just nulled server-side.
      setItem(await apiFetch<ItemDetailResponse>(`/api/items/${itemId}`));
      setClearNote({
        artKind,
        failed: false,
        // "disabled", never "deleted" -- the file is renamed, and an operator
        // told their own artwork was deleted would not think to rename it back.
        message: response.queued
          ? "The override file was disabled (renamed to .disabled on the mount) and a re-render was queued."
          : "The override file was disabled (renamed to .disabled on the mount). A re-render was already pending, so nothing new was added.",
      });
    } catch (caught) {
      // Shown verbatim: a 409 says there was no override to clear, and a 503
      // carries the OS error, whose absolute path on the mount is the only
      // thing that says which file to go and look at. This page is behind
      // require_session.
      setClearNote({ artKind, failed: true, message: (caught as Error).message });
    } finally {
      setClearing(null);
    }
  }

  /** Opens a section's panel, or closes it if it is already showing that art
   * kind -- the same button is the way back out. */
  function toggleBrowse(section: string, artKind: string) {
    setBrowsing((open) =>
      open !== null && open.section === section && open.artKind === artKind
        ? null
        : { section, artKind },
    );
  }

  /** The manual-source counterpart of toggleBrowse. Its own toggle, so the
   * browse panel and the manual panel can be open at once for one section --
   * neither costs the other's provider fan-out. */
  function toggleManual(section: string, artKind: string) {
    setManualing((open) =>
      open !== null && open.section === section && open.artKind === artKind
        ? null
        : { section, artKind },
    );
  }

  /** Re-reads the item after a pick: the row's provider becomes "manual" and
   * its fingerprints are nulled server-side, so nothing on screen is true
   * until it is read again. */
  async function reloadItem() {
    setItem(await apiFetch<ItemDetailResponse>(`/api/items/${itemId}`));
  }

  if (item === null) {
    return (
      <>
        <div className="page-header">
          <h1>Item</h1>
        </div>
        {error !== null ? (
          <p className="page-error">{error}</p>
        ) : (
          <p className="muted">Loading…</p>
        )}
      </>
    );
  }

  /** Every art kind this item has a render row for, ordered by name so the
   * pairs do not move around between visits (the API's row order is not
   * promised). An item nothing has rendered yet has no rows to read, so it
   * falls back to the kind's primary art -- the panes are still worth showing,
   * because the live one says what Plex is serving regardless. */
  const artKinds =
    item.renders.length === 0
      ? [artKindFor(item.kind)]
      : item.renders.map((render) => render.art_kind).sort();

  // Named after the show it belongs to when one is known -- an episode or
  // season titled on its own ("Episode 26", "Season 2") is indistinguishable
  // from every other item Plex gives that same title. `item.parent` is null
  // both for a movie/show (which has none) and for a season/episode whose
  // parent hasn't been resolved yet (routes.py's item_detail degrades
  // honestly rather than inventing a name), so both fall back to the plain
  // title unchanged.
  const parentCode =
    item.parent === null
      ? null
      : item.kind === "episode"
        ? seasonEpisodeCode(item.season_number, item.episode_number)
        : item.kind === "season"
          ? seasonCode(item.season_number)
          : null;

  return (
    <>
      <div className="page-header">
        <h1>
          {item.parent === null ? (
            item.title
          ) : (
            <>
              <Link to={`/items/${item.parent.id}`}>{item.parent.title}</Link>
              {" — "}
              {parentCode !== null && `${parentCode} · `}
              {item.title}
            </>
          )}
        </h1>
        <button type="button" className="primary" disabled={busy} onClick={() => void reprocess()}>
          Re-run
        </button>
      </div>

      <p className="item-meta muted">
        <Link to="/library">Library</Link> · {item.library} ·{" "}
        {item.parent !== null && (
          <>
            <Link to={`/items/${item.parent.id}`}>{item.parent.title}</Link> ·{" "}
          </>
        )}
        {item.kind} ·{" "}
        <span className="mono">rating key {item.rating_key ?? "—"}</span>
      </p>

      {error !== null && <p className="page-error">{error}</p>}
      {outcome !== null && <p className="item-outcome">{outcome}</p>}

      {/* One pair per art kind, each labelled: a movie has a poster AND a
        * background, and the page used to show only the poster -- so half of
        * what it exists to compare was unreachable. The label is required
        * because the two pairs' captions are otherwise identical. */}
      {artKinds.map((kind) => (
        <section className="art-kind-panes" key={kind}>
          <h2 className="art-kind-label">{kind}</h2>
          <div className="art-panes">
            <BasePane itemId={item.id} artKind={kind} />
            <LivePane itemId={item.id} artKind={kind} />
          </div>
          <div className="browse-controls">
            <button type="button" className="browse" onClick={() => toggleBrowse(kind, kind)}>
              Browse candidates
            </button>
            <button type="button" className="browse" onClick={() => toggleManual(kind, kind)}>
              Use file or URL
            </button>
            {/* A logo has no section of its own -- no render row, no pane -- but
              * it is composited into the poster, so the poster section is where
              * an operator would look for it. Offered only for the item kinds
              * whose poster render uses one; anywhere else the pick would be
              * consumed by nothing. */}
            {kind === "poster" && LOGO_BROWSABLE_KINDS.includes(item.kind) && (
              <>
                <button
                  type="button"
                  className="browse"
                  onClick={() => toggleBrowse(kind, "logo")}
                >
                  Browse logos
                </button>
                <button
                  type="button"
                  className="browse"
                  onClick={() => toggleManual(kind, "logo")}
                >
                  Use logo file or URL
                </button>
              </>
            )}
          </div>
          {browsing !== null && browsing.section === kind && (
            // Keyed on the art kind being browsed, not left to the implicit
            // single child: without it, switching from posters to logos (or
            // back) reuses the same instance, and a prior pick's note or
            // failure message -- state private to that instance -- reappears
            // under the new tiles before anything has been clicked here.
            <CandidatePanel
              key={browsing.artKind}
              itemId={item.id}
              artKind={browsing.artKind}
              onPicked={reloadItem}
            />
          )}
          {manualing !== null && manualing.section === kind && (
            // Keyed the same way as the browse panel above, and for the same
            // reason: switching a manual panel between poster and logo must
            // start it clean rather than carry the prior kind's note.
            <ManualSourcePanel
              key={manualing.artKind}
              itemId={item.id}
              artKind={manualing.artKind}
              onInstalled={reloadItem}
            />
          )}
        </section>
      ))}

      <div className="panel item-panel">
        <h2>Facts</h2>
        <Facts facts={item.facts} />
      </div>

      <div className="panel item-panel">
        <h2>Renders</h2>
        <Renders
          renders={item.renders}
          clearing={clearing}
          note={clearNote}
          onClear={(artKind) => void clearOverride(artKind)}
        />
      </div>
    </>
  );
}
