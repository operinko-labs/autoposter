import { Fragment, useEffect, useState } from "react";
import { Link, useParams } from "react-router-dom";

import { ApiError, apiFetch, apiFetchImage } from "../api/client";
import type {
  ClearOverrideResponse,
  ItemDetailResponse,
  ItemRender,
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
    <table className="render-table">
      <thead>
        <tr>
          <th>Art</th>
          <th>Status</th>
          <th>Provider</th>
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
                <Fingerprint value={render.fingerprint} />
              </td>
              <td>
                <Fingerprint value={render.badge_fingerprint} />
              </td>
              <td>{render.upload_status}</td>
              <td className="muted">{formatTime(render.rendered_at)}</td>
              <td className="muted">{formatTime(render.uploaded_at)}</td>
            </tr>
            {note !== null && note.artKind === render.art_kind && (
              <tr>
                <td colSpan={8}>
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

  useEffect(() => {
    let cancelled = false;
    setItem(null);
    setOutcome(null);
    setClearNote(null);
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
      : [...item.renders].map((render) => render.art_kind).sort();

  return (
    <>
      <div className="page-header">
        <h1>{item.title}</h1>
        <button type="button" className="primary" disabled={busy} onClick={() => void reprocess()}>
          Re-run
        </button>
      </div>

      <p className="item-meta muted">
        <Link to="/library">Library</Link> · {item.library} · {item.kind} ·{" "}
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
