import { useEffect, useRef, useState, type ChangeEvent } from "react";
import { Link } from "react-router-dom";

import { ApiError, apiFetch, apiFetchImage } from "../api/client";
import type { ItemFiltersResponse, ItemSummary, ItemsResponse } from "../api/types";
import { artKindFor } from "../artKind";
import "./library.css";

/** One screen of tiles. The endpoint caps `limit` at 200 (MAX_ITEMS_LIMIT in
 * src/autoposter/api/routes.py); 48 is a few rows on a wide monitor and keeps
 * the artwork requests behind a page bounded. */
const PAGE_SIZE = 48;

/** One tile's image, or the title in place of it.
 *
 * Two things are unusual here and both come from the same fact: the artwork
 * endpoint is authenticated by an `Authorization` header, which an <img> does
 * not send (see apiFetchImage). So the bytes are fetched by hand and shown as
 * an object URL, and because that fetch is ours rather than the browser's,
 * `loading="lazy"` cannot be what defers it -- an IntersectionObserver does
 * that instead. A page of 48 tiles is otherwise 48 requests fired at once, of
 * which a first screen needs about eight.
 *
 * Nothing here reports an error. A missing render is the ordinary state of an
 * item this project has not reached yet, and the tile simply reads as a title
 * card; the page-level error line stays reserved for the listing itself.
 */
function Artwork({
  itemId,
  artKind,
  title,
}: {
  itemId: number;
  artKind: string;
  title: string;
}) {
  const [source, setSource] = useState<string | null>(null);
  const [onScreen, setOnScreen] = useState(false);
  const placeholder = useRef<HTMLDivElement | null>(null);

  useEffect(() => {
    const node = placeholder.current;
    if (node === null) return;
    const observer = new IntersectionObserver(
      (entries) => {
        if (!entries.some((entry) => entry.isIntersecting)) return;
        setOnScreen(true);
        observer.disconnect();
      },
      // Start a row early, so a tile is usually filled by the time it arrives.
      { rootMargin: "300px" },
    );
    observer.observe(node);
    return () => observer.disconnect();
  }, []);

  useEffect(() => {
    if (!onScreen) return;
    let cancelled = false;
    let objectUrl: string | null = null;

    apiFetchImage(`/api/items/${itemId}/artwork/${artKind}`)
      .then((blob) => {
        if (blob === null || cancelled) return;
        objectUrl = URL.createObjectURL(blob);
        setSource(objectUrl);
      })
      .catch((caught: unknown) => {
        // A 401 has already sent the user to the login form; anything else is
        // one absent image, which the title card below covers -- but leave a
        // trace, because this catch is chained after the success handler and
        // would otherwise also swallow a bug thrown inside it.
        if (!(caught instanceof ApiError && caught.status === 401)) {
          console.warn(`artwork for item ${itemId} failed`, caught);
        }
      });

    return () => {
      cancelled = true;
      // A stale image must not survive into the next fetch. Unreachable while
      // the parent keys tiles by item id, but the invariant should not depend
      // on the parent's keying choice.
      setSource(null);
      // Object URLs are held by the document until revoked, so a browsed-away
      // page would otherwise keep every image it ever showed in memory.
      if (objectUrl !== null) URL.revokeObjectURL(objectUrl);
    };
  }, [onScreen, itemId, artKind]);

  if (source === null) {
    // aria-hidden because the caption below carries the same title: without it
    // every un-rendered tile announces its name twice.
    return (
      <div className="tile-art tile-art-empty" ref={placeholder} aria-hidden="true">
        {title}
      </div>
    );
  }

  return (
    <img
      className="tile-art"
      src={source}
      alt=""
      loading="lazy"
      // Corrupt or truncated bytes would otherwise paint the browser's
      // broken-image icon; fall back to the same title card a 404 gets.
      onError={() => {
        URL.revokeObjectURL(source);
        setSource(null);
      }}
    />
  );
}

export function Library() {
  const [filters, setFilters] = useState<ItemFiltersResponse | null>(null);
  const [library, setLibrary] = useState("");
  const [kind, setKind] = useState("");
  const [status, setStatus] = useState("");
  const [offset, setOffset] = useState(0);
  const [page, setPage] = useState<ItemsResponse | null>(null);
  const [error, setError] = useState<string | null>(null);
  // Held apart from `error` on purpose: the items effect clears `error` on
  // every success, and both effects fire concurrently on mount -- a shared
  // state would let a later listing success erase the filters failure,
  // leaving three inexplicably empty dropdowns.
  const [filtersError, setFiltersError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    apiFetch<ItemFiltersResponse>("/api/items/filters")
      .then((response) => {
        if (!cancelled) setFilters(response);
      })
      .catch((caught: Error) => {
        if (!cancelled) setFiltersError(caught.message);
      });
    return () => {
      cancelled = true;
    };
  }, []);

  useEffect(() => {
    let cancelled = false;
    // Only the requested page is fetched. The library runs to ~15,000 items,
    // so `total` drives the pager and the rows stay on the server.
    const query = new URLSearchParams({
      limit: String(PAGE_SIZE),
      offset: String(offset),
    });
    if (library !== "") query.set("library", library);
    if (kind !== "") query.set("kind", kind);
    if (status !== "") query.set("status", status);

    apiFetch<ItemsResponse>(`/api/items?${query.toString()}`)
      .then((response) => {
        if (cancelled) return;
        setPage(response);
        setError(null);
      })
      .catch((caught: Error) => {
        if (!cancelled) setError(caught.message);
      });
    return () => {
      cancelled = true;
    };
  }, [library, kind, status, offset]);

  /** Every filter resets the offset. Keeping it would land the user on page 9
   * of a filtered result that has two, which the endpoint answers with an
   * empty list rather than an error -- an apparently empty library. */
  function choose(set: (value: string) => void) {
    return (event: ChangeEvent<HTMLSelectElement>) => {
      set(event.target.value);
      setOffset(0);
    };
  }

  const items: ItemSummary[] = page?.items ?? [];
  const total = page?.total ?? 0;

  return (
    <>
      <div className="page-header">
        <h1>Library</h1>
        {page !== null && (
          <span className="muted">
            {total} item{total === 1 ? "" : "s"}
          </span>
        )}
      </div>

      {error !== null && <p className="page-error">{error}</p>}

      <div className="library-filters">
        <label>
          Library
          <select value={library} onChange={choose(setLibrary)}>
            <option value="">All libraries</option>
            {filters?.libraries.map((value) => (
              <option key={value} value={value}>
                {value}
              </option>
            ))}
          </select>
        </label>
        <label>
          Kind
          <select value={kind} onChange={choose(setKind)}>
            <option value="">All kinds</option>
            {filters?.kinds.map((value) => (
              <option key={value} value={value}>
                {value}
              </option>
            ))}
          </select>
        </label>
        <label>
          Status
          <select value={status} onChange={choose(setStatus)}>
            <option value="">Any status</option>
            {filters?.statuses.map((value) => (
              <option key={value} value={value}>
                {value}
              </option>
            ))}
          </select>
        </label>
        {filtersError !== null && (
          <span className="page-error">Filters are unavailable: {filtersError}</span>
        )}
      </div>

      {page === null ? (
        error === null ? (
          <p className="muted">Loading…</p>
        ) : null
      ) : items.length === 0 ? (
        <p className="empty">No items match these filters.</p>
      ) : (
        <div className="tile-grid">
          {items.map((item) => {
            const artKind = artKindFor(item.kind);
            return (
              <Link key={item.id} to={`/items/${item.id}`} className="tile">
                <Artwork itemId={item.id} artKind={artKind} title={item.title} />
                <div className="tile-title">{item.title}</div>
                <div className="tile-sub muted">
                  {item.library} · {item.render_status[artKind] ?? "not rendered"}
                </div>
              </Link>
            );
          })}
        </div>
      )}

      <div className="library-pager">
        <button
          type="button"
          disabled={offset === 0}
          onClick={() => setOffset(Math.max(offset - PAGE_SIZE, 0))}
        >
          Previous
        </button>
        <span className="muted">
          {items.length === 0
            ? "Nothing to show"
            : `${offset + 1}–${offset + items.length} of ${total}`}
        </span>
        <button
          type="button"
          disabled={offset + items.length >= total}
          onClick={() => setOffset(offset + PAGE_SIZE)}
        >
          Next
        </button>
      </div>
    </>
  );
}
