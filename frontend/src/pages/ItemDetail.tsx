import { useEffect, useState } from "react";
import { Link, useParams } from "react-router-dom";

import { ApiError, apiFetch, apiFetchImage } from "../api/client";
import type {
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

function Renders({ renders }: { renders: ItemRender[] }) {
  if (renders.length === 0) {
    return <p className="empty">This item has never been rendered.</p>;
  }

  return (
    <table className="render-table">
      <thead>
        <tr>
          <th>Art</th>
          <th>Status</th>
          <th>Fingerprint</th>
          <th>Badge fingerprint</th>
          <th>Upload</th>
          <th>Rendered</th>
          <th>Uploaded</th>
        </tr>
      </thead>
      <tbody>
        {renders.map((render) => (
          <tr key={render.art_kind}>
            <td>{render.art_kind}</td>
            <td>{render.status}</td>
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

  useEffect(() => {
    let cancelled = false;
    setItem(null);
    setOutcome(null);
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

  const artKind = artKindFor(item.kind);

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

      <div className="art-panes">
        <BasePane itemId={item.id} artKind={artKind} />
        <LivePane itemId={item.id} artKind={artKind} />
      </div>

      <div className="panel item-panel">
        <h2>Facts</h2>
        <Facts facts={item.facts} />
      </div>

      <div className="panel item-panel">
        <h2>Renders</h2>
        <Renders renders={item.renders} />
      </div>
    </>
  );
}
