/** The Action Center: every artwork asset this service chose that is imperfect.
 *
 * Roadmap rows 11a/11b. One row is one chosen asset -- one `renders` row --
 * and every reason it is here is computed by the server from the config live
 * at that moment. Nothing on this page decides what counts as imperfect, and
 * nothing here restates a fact: the flags, their labels, and the factual
 * sentence behind each one all arrive from the server's registry, so a flag
 * added there appears here without an edit.
 *
 * Three conventions shape the rest, borrowed from the pages that established
 * them:
 *
 *  - **The server's numbers, never a restatement.** The chips, the pager range
 *    and the bulk result all render counts the server sent. A refusal or a
 *    de-duplicated queue is an answer with its numbers, not an error (Modes).
 *  - **Dry run is the offer, apply is the exception.** Counting what a bulk
 *    re-search would queue is one unguarded click, because it queues nothing.
 *    Applying is a two-step gate, and a filter change withdraws the grant --
 *    the grant was for the request those filters described (Modes).
 *  - **Re-read after every action.** An optimistic removal would show a row
 *    gone when the server had not moved it, and a re-search can legitimately
 *    change nothing at all (Failures).
 */
import { useCallback, useEffect, useRef, useState, type ChangeEvent } from "react";
import { Link } from "react-router-dom";

import { ApiError, apiFetch } from "../api/client";
import { fieldErrors } from "../api/overrides";
import type {
  ActionRow,
  ActionsResponse,
  ActionsSummaryResponse,
  BulkRerenderResponse,
  ItemFiltersResponse,
  QualityBackfillStatus,
  QualityBackfillTrigger,
  RebuildResponse,
} from "../api/types";
import { formatTime } from "../format";
// The pill and the row-error paragraph are dashboard.css's, exactly as
// Modes.tsx and Library.tsx borrow them. Imported explicitly so this file
// names the stylesheets it depends on, rather than leaning on another
// page's import (App.tsx bundles every page statically today).
import "./dashboard.css";
import "./action-center.css";

/** One screen of rows. The endpoint caps `limit` at 200
 * (MAX_ACTIONS_LIMIT in src/autoposter/api/action_center.py). */
const PAGE_SIZE = 50;

/** Mirrors `flags.ART_KINDS` in src/autoposter/actions/flags.py. Hardcoded
 * rather than fetched: /api/items/filters reports render *statuses*, not art
 * kinds, and the four kinds are a fixed part of the schema, not library data. */
const ART_KINDS = ["poster", "season_poster", "background", "title_card"];

/** A stable identity for one row: the queue's unit is (item, art kind), which
 * is exactly what the renders table keys uniquely. */
function rowKey(row: ActionRow): string {
  return `${row.item_id}:${row.art_kind}`;
}

/** A refusal in whichever of the two shapes it arrives.
 *
 * `POST /api/actions/dismiss` validates `flag` against the server's own
 * registry, so a chip read from a summary an older build served refuses as a
 * FastAPI validation list -- and `api/client.ts` flattens a structured
 * `detail` to the useless "request failed with 422". The sentence the server
 * wrote is inside the detail; this digs it out, the same way
 * CustomCollectionsPanel's own `refusalMessage` does.
 */
function refusalMessage(caught: unknown): string {
  if (caught instanceof ApiError && Array.isArray(caught.detail)) {
    const messages = Object.values(fieldErrors(caught.detail));
    if (messages.length > 0) return messages.join("; ");
  }
  return (caught as Error).message;
}

export function ActionCenter() {
  const [filters, setFilters] = useState<ItemFiltersResponse | null>(null);
  /** Held apart from `error` for the Library page's reason: the dropdowns
   * being unavailable is not the queue's failure, and neither should erase
   * the other. */
  const [filtersError, setFiltersError] = useState<string | null>(null);

  const [flag, setFlag] = useState("");
  const [library, setLibrary] = useState("");
  const [artKind, setArtKind] = useState("");
  const [includeDismissed, setIncludeDismissed] = useState(false);
  const [offset, setOffset] = useState(0);

  const [summary, setSummary] = useState<ActionsSummaryResponse | null>(null);
  const [page, setPage] = useState<ActionsResponse | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  // A set, not a single value: two rows can have an `act()` in flight at
  // once (row 7's dismiss, then row 8's, inside one round trip), and a
  // single key would let the first to finish clear the other row's busy
  // state mid-flight.
  const [busyKeys, setBusyKeys] = useState<ReadonlySet<string>>(() => new Set());

  const [armed, setArmed] = useState(false);
  const [bulk, setBulk] = useState<BulkRerenderResponse | null>(null);
  const [bulkBusy, setBulkBusy] = useState(false);

  // Its own arm, its own result and its own busy flag rather than a shared
  // "which bulk action" enum: the two panels are two questions with two
  // response shapes, and one shared flag would disable the other panel's
  // button for a request it has nothing to do with.
  const [armedRebuild, setArmedRebuild] = useState(false);
  const [rebuildResult, setRebuildResult] = useState<RebuildResponse | null>(null);
  const [rebuildBusy, setRebuildBusy] = useState(false);

  const [coverage, setCoverage] = useState<QualityBackfillStatus | null>(null);
  const [coverageDetail, setCoverageDetail] = useState<string | null>(null);
  const [coverageBusy, setCoverageBusy] = useState(false);

  // `load` is awaited from an effect and again from every click handler, so a
  // response can land after the page has gone. A ref rather than a per-effect
  // local, because the same guard has to cover both callers -- the pattern
  // Failures.tsx established.
  const live = useRef(true);
  useEffect(() => {
    live.current = true;
    return () => {
      live.current = false;
    };
  }, []);

  // A per-effect `cancelled` local is not enough here, unlike Library.tsx's
  // one fetch: `act()` and `runBulk()` also call `load()` outside the effect,
  // so two runs can be in flight from two different filter clicks. A
  // monotonic generation counter says which run is still the latest one --
  // if an earlier run's response lands after a later run has already
  // started, it is discarded rather than overwriting the newer filter's data.
  const generation = useRef(0);

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

  /** The scope both requests share: everything except the chip and the page. */
  const scopeQuery = useCallback(() => {
    const query = new URLSearchParams();
    // Only when non-empty: `library=` with nothing after it is still a filter
    // the endpoint would evaluate, and it matches a library called "".
    if (library !== "") query.set("library", library);
    if (artKind !== "") query.set("art_kind", artKind);
    if (includeDismissed) query.set("include_dismissed", "true");
    return query;
  }, [library, artKind, includeDismissed]);

  const load = useCallback(async () => {
    const mine = ++generation.current;
    try {
      const summaryResponse = await apiFetch<ActionsSummaryResponse>(
        `/api/actions/summary?${scopeQuery().toString()}`,
      );
      if (!live.current || mine !== generation.current) return;
      setSummary(summaryResponse);

      const query = scopeQuery();
      query.set("limit", String(PAGE_SIZE));
      query.set("offset", String(offset));
      // Scalar, not a set: one chip at a time is the ratified reading of
      // `?flag=`, and the endpoint takes a single code.
      if (flag !== "") query.set("flag", flag);
      const rows = await apiFetch<ActionsResponse>(`/api/actions?${query.toString()}`);
      if (!live.current || mine !== generation.current) return;
      setPage(rows);
      setError(null);
    } catch (caught) {
      if (!live.current || mine !== generation.current) return;
      setError(refusalMessage(caught));
    }
  }, [scopeQuery, offset, flag]);

  useEffect(() => {
    void load();
  }, [load]);

  /** The coverage panel reads its own endpoint, on its own effect.
   *
   * Not folded into `load()`: coverage does not depend on the filters, so
   * re-reading it on every chip click would be a query per click for a number
   * that cannot have changed. */
  const loadCoverage = useCallback(async () => {
    try {
      const response = await apiFetch<QualityBackfillStatus>("/api/actions/backfill");
      if (live.current) setCoverage(response);
    } catch (caught) {
      if (live.current) setError((caught as Error).message);
    }
  }, []);

  useEffect(() => {
    void loadCoverage();
  }, [loadCoverage]);

  async function runBackfill() {
    setCoverageBusy(true);
    setError(null);
    try {
      const response = await apiFetch<QualityBackfillTrigger>("/api/actions/backfill", {
        method: "POST",
      });
      if (live.current) setCoverageDetail(response.detail);
      // Re-read rather than trusting the trigger's own numbers: the batch it
      // queued has not run yet, so the coverage it reported is the coverage
      // BEFORE the work, and the page must not present it as after.
      await loadCoverage();
    } catch (caught) {
      if (live.current) setError((caught as Error).message);
    } finally {
      if (live.current) setCoverageBusy(false);
    }
  }

  /** Every filter change resets the page and withdraws the bulk grant.
   *
   * The offset, because landing on page 9 of a filtered result that has one
   * is answered with an empty list rather than an error -- which reads as
   * "nothing is flagged" when the truth is the opposite. The grant, because
   * it was given for the request those filters described. */
  function refocus(apply: () => void) {
    apply();
    setOffset(0);
    setArmed(false);
    setBulk(null);
    setArmedRebuild(false);
    setRebuildResult(null);
    setNotice(null);
  }

  function chooseFilter(set: (value: string) => void) {
    return (event: ChangeEvent<HTMLSelectElement>) => refocus(() => set(event.target.value));
  }

  async function act(row: ActionRow, run: () => Promise<string | null>) {
    const key = rowKey(row);
    setBusyKeys((prev) => new Set(prev).add(key));
    setError(null);
    setNotice(null);
    try {
      const message = await run();
      if (live.current && message !== null) setNotice(message);
      // Re-read rather than removing the row locally. A re-search may
      // legitimately find the same art and leave the row exactly where it
      // was, and the server is the authority on what is still flagged.
      await load();
    } catch (caught) {
      if (live.current) setError(refusalMessage(caught));
    } finally {
      if (live.current) {
        setBusyKeys((prev) => {
          const next = new Set(prev);
          next.delete(key);
          return next;
        });
      }
    }
  }

  function reSearch(row: ActionRow) {
    return act(row, async () => {
      const response = await apiFetch<{ queued: boolean; job_id: number | null }>(
        "/api/actions/rerender",
        { method: "POST", body: JSON.stringify({ item_id: row.item_id }) },
      );
      return response.queued
        ? `${row.title}: queued.`
        : `${row.title}: a pass for this item is already queued; nothing was added.`;
    });
  }

  function dismiss(row: ActionRow) {
    return act(row, async () => {
      await apiFetch("/api/actions/dismiss", {
        method: "POST",
        body: JSON.stringify({
          item_id: row.item_id,
          art_kind: row.art_kind,
          // Which chip the operator was looking at, for the audit. The
          // dismissal covers the row either way -- the evidence hash is what
          // scopes it -- so an unflagged row (only reachable with dismissed
          // rows shown) records no chip rather than inventing one.
          flag: row.flags[0] ?? null,
        }),
      });
      return null;
    });
  }

  function restore(row: ActionRow) {
    return act(row, async () => {
      await apiFetch("/api/actions/undismiss", {
        method: "POST",
        body: JSON.stringify({ item_id: row.item_id, art_kind: row.art_kind }),
      });
      return null;
    });
  }

  /** Row 233. Clear this row's fingerprints and queue its item.
   *
   * No arm, exactly like Re-search beside it: this is one row the operator
   * clicked, it removes nothing, and the next render keeps the outgoing
   * asset as its backup generation -- so the two-step gate the bulk bar needs
   * would be ceremony here. `apply: true` is sent explicitly because the
   * endpoint's default is the dry run.
   */
  function rebuild(row: ActionRow) {
    return act(row, async () => {
      const response = await apiFetch<RebuildResponse>("/api/actions/rebuild", {
        method: "POST",
        body: JSON.stringify({
          row: { item_id: row.item_id, art_kind: row.art_kind },
          apply: true,
        }),
      });
      if (response.matched === 0) {
        return `${row.title}: this row is no longer in the queue; nothing was rebuilt.`;
      }
      if (response.enqueued === 0) {
        return `${row.title}: fingerprint cleared; a pass for this item is already queued.`;
      }
      return `${row.title}: fingerprint cleared and the item queued for a rebuild.`;
    });
  }

  async function runRebuild(apply: boolean) {
    setArmedRebuild(false);
    setRebuildBusy(true);
    setError(null);
    try {
      const body: Record<string, unknown> = { apply };
      if (flag !== "") body.flag = flag;
      if (library !== "") body.library = library;
      if (artKind !== "") body.art_kind = artKind;
      if (includeDismissed) body.include_dismissed = true;
      const response = await apiFetch<RebuildResponse>("/api/actions/rebuild", {
        method: "POST",
        body: JSON.stringify(body),
      });
      if (live.current) setRebuildResult(response);
      if (apply) await load();
    } catch (caught) {
      if (live.current) {
        setError(refusalMessage(caught));
        // A prior run's result panel would otherwise sit under this error,
        // directly beneath the button that just failed.
        setRebuildResult(null);
      }
    } finally {
      if (live.current) setRebuildBusy(false);
    }
  }

  async function runBulk(apply: boolean) {
    setArmed(false);
    setBulkBusy(true);
    setError(null);
    try {
      const body: Record<string, unknown> = { apply };
      if (flag !== "") body.flag = flag;
      if (library !== "") body.library = library;
      if (artKind !== "") body.art_kind = artKind;
      if (includeDismissed) body.include_dismissed = true;
      const response = await apiFetch<BulkRerenderResponse>("/api/actions/bulk/rerender", {
        method: "POST",
        body: JSON.stringify(body),
      });
      if (live.current) setBulk(response);
      if (apply) await load();
    } catch (caught) {
      if (live.current) {
        setError(refusalMessage(caught));
        // A prior run's result panel would otherwise sit under this error,
        // directly beneath the button that just failed.
        setBulk(null);
      }
    } finally {
      if (live.current) setBulkBusy(false);
    }
  }

  const rows: ActionRow[] = page?.items ?? [];
  const total = page?.total ?? 0;
  const labels = new Map((summary?.flags ?? []).map((entry) => [entry.code, entry.label]));

  return (
    <>
      <div className="page-header">
        <h1>Action Center</h1>
        {summary !== null && (
          <span className="muted">
            {summary.total} asset{summary.total === 1 ? "" : "s"} need attention
          </span>
        )}
      </div>

      {error !== null && <p className="page-error">{error}</p>}

      <div className="action-chips">
        {(summary?.flags ?? []).map((entry) => (
          <button
            type="button"
            key={entry.code}
            className={
              flag === entry.code
                ? "action-chip active"
                : entry.count === 0
                  ? "action-chip action-chip-zero"
                  : "action-chip"
            }
            aria-pressed={flag === entry.code}
            title={
              entry.instant
                ? entry.description
                : `${entry.description} This flag cannot fire until the row re-renders.`
            }
            onClick={() => refocus(() => setFlag(flag === entry.code ? "" : entry.code))}
          >
            <span className="action-chip-label">{entry.label}</span>
            <span className="action-chip-count">{entry.count}</span>
          </button>
        ))}
      </div>

      <div className="action-filters">
        <label>
          Library
          <select value={library} onChange={chooseFilter(setLibrary)}>
            <option value="">All libraries</option>
            {filters?.libraries.map((value) => (
              <option key={value} value={value}>
                {value}
              </option>
            ))}
          </select>
        </label>
        <label>
          Art kind
          <select value={artKind} onChange={chooseFilter(setArtKind)}>
            <option value="">All art kinds</option>
            {ART_KINDS.map((value) => (
              <option key={value} value={value}>
                {value}
              </option>
            ))}
          </select>
        </label>
        <label className="action-toggle">
          <input
            type="checkbox"
            checked={includeDismissed}
            onChange={(event) => refocus(() => setIncludeDismissed(event.target.checked))}
          />
          Show dismissed
        </label>
        {filtersError !== null && (
          <span className="page-error">Filters are unavailable: {filtersError}</span>
        )}
      </div>

      {coverage !== null && (
        <div className="panel action-coverage">
          <div className="row-actions">
            <span>
              {coverage.done} of {coverage.total} assets scored
            </span>
            <span>
              {coverage.queued_for_scoring} of {coverage.unscored_total} unscored asset(s)
              queued for scoring
            </span>
            {coverage.blocked > 0 && (
              <span>
                {coverage.blocked} blocked — see <Link to="/failures">Failures</Link>
              </span>
            )}
            <button
              type="button"
              disabled={coverageBusy || coverage.status === "complete"}
              onClick={() => void runBackfill()}
            >
              Score the next batch
            </button>
          </div>
          <p className="muted action-caveat">
            Four of the flags rest on facts written when an asset renders, so an
            asset rendered before this page existed cannot show them yet. Scoring
            a batch queues a genuine re-render for those assets — the only way to
            recover which language the ladder achieved, which no column holds for
            an older row. One batch per press.
          </p>
          {coverageDetail !== null && (
            <p className="muted action-notice" role="status">
              {coverageDetail}
            </p>
          )}
        </div>
      )}

      <div className="panel action-bulk">
        <div className="row-actions">
          <button type="button" disabled={bulkBusy} onClick={() => void runBulk(false)}>
            Count what this would queue
          </button>
          {armed ? (
            <>
              {/* `alert`, so a screen reader is told the question appeared:
                  the button label alone does not carry what is about to
                  happen. */}
              <span className="action-confirm" role="alert">
                Queue another pass for every item matching these filters?
              </span>
              <button
                type="button"
                className="primary"
                disabled={bulkBusy}
                autoFocus
                onClick={() => void runBulk(true)}
              >
                Yes, queue them
              </button>
              <button type="button" onClick={() => setArmed(false)}>
                Cancel
              </button>
            </>
          ) : (
            /* Arms the gate. It must never post -- see the mutation proof in
               ActionCenter.test.tsx. */
            <button
              type="button"
              disabled={bulkBusy}
              onClick={() => {
                setArmedRebuild(false);
                setArmed(true);
              }}
            >
              Re-search everything matching
            </button>
          )}
        </div>
        <p className="muted action-caveat">
          A re-search runs provider selection again for the whole item, against the 24-hour
          provider cache. It may legitimately find the same artwork and leave the row flagged.
        </p>
        {bulk !== null && (
          /* `status` rather than `alert`: this is the outcome of something the
             operator asked for, including "nothing matched", and it must not
             be announced as an error. */
          <div className="action-bulk-result" role="status">
            {/* The pill follows what was CREATED, not what was asked for: a
                batch the pending dedupe swallowed answers "enqueued" with
                zero jobs, and a green pill beside "queued 0" would be the
                page contradicting the sentence next to it. */}
            <span className={`pill ${bulk.enqueued > 0 ? "pill-ok" : "pill-skipped"}`}>
              {bulk.status}
            </span>
            <span>{bulk.detail}</span>
          </div>
        )}
      </div>

      <div className="panel action-bulk">
        <div className="row-actions">
          <button type="button" disabled={rebuildBusy} onClick={() => void runRebuild(false)}>
            Count what a rebuild would clear
          </button>
          {armedRebuild ? (
            <>
              {/* `alert`, so a screen reader is told the question appeared:
                  the button label alone does not carry what is about to
                  happen. */}
              <span className="action-confirm" role="alert">
                Clear the fingerprint and re-render every asset matching these filters?
                Nothing is removed.
              </span>
              <button
                type="button"
                className="primary"
                disabled={rebuildBusy}
                autoFocus
                onClick={() => void runRebuild(true)}
              >
                Yes, rebuild them
              </button>
              <button type="button" onClick={() => setArmedRebuild(false)}>
                Cancel
              </button>
            </>
          ) : (
            /* Arms the gate. It must never post -- see the mutation proof in
               ActionCenter.test.tsx. Arming this one disarms the re-search
               arm above, so there is never a second Cancel on screen. */
            <button
              type="button"
              disabled={rebuildBusy}
              onClick={() => {
                setArmed(false);
                setArmedRebuild(true);
              }}
            >
              Rebuild everything matching
            </button>
          )}
        </div>
        <p className="muted action-caveat">
          A rebuild clears the asset&rsquo;s fingerprint and queues its item, so the next pass
          re-renders it even when the ladder picks the same artwork &mdash; which a re-search
          alone cannot do. Nothing is unlinked from the asset tree and nothing is removed from
          Plex: the published file is replaced only when the new render lands, and the
          outgoing generation is kept as the backup copy.
        </p>
        {rebuildResult !== null && (
          /* `status` rather than `alert`: this is the outcome of something the
             operator asked for, including "nothing matched". */
          <div className="action-bulk-result" role="status">
            {/* The pill follows what this press actually MOVED. A batch whose
                rows were already cleared answers "enqueued" with cleared 0,
                and a green pill beside "cleared 0" would be the page
                contradicting the sentence next to it. */}
            <span className={`pill ${rebuildResult.cleared > 0 ? "pill-ok" : "pill-skipped"}`}>
              {rebuildResult.status}
            </span>
            {/* One template literal rather than interleaved text and
                expressions: JSX's own whitespace folding across a wrapped
                line decides whether "covers 2, cleared 0" keeps its comma
                against the number, and a sentence a test matches by regex
                must not depend on where the source happens to wrap. */}
            <span>
              {`${rebuildResult.matched} row(s) matched; this batch covers ` +
                `${rebuildResult.selected}, cleared ${rebuildResult.cleared}, ` +
                `queued ${rebuildResult.enqueued} job(s) for ${rebuildResult.items} item(s).`}
            </span>
          </div>
        )}
      </div>

      {notice !== null && <p className="muted action-notice">{notice}</p>}

      <div className="panel">
        {page === null ? (
          error === null ? (
            <p className="muted">Loading…</p>
          ) : null
        ) : rows.length === 0 ? (
          <p className="empty">Nothing needs attention under these filters.</p>
        ) : (
          <div className="table-scroll">
            <table>
              <thead>
                <tr>
                  <th>Item</th>
                  <th>Library</th>
                  <th>Art kind</th>
                  <th>Why</th>
                  <th>Scored</th>
                  <th />
                </tr>
              </thead>
              <tbody>
                {rows.map((row) => (
                  <tr key={rowKey(row)}>
                    <td>
                      <Link to={`/items/${row.item_id}`}>{row.title}</Link>
                    </td>
                    <td className="muted">{row.library}</td>
                    <td className="mono">{row.art_kind}</td>
                    <td className="cell-wrap">
                      {row.flags.map((code, index) => (
                        <div className="action-reason" key={code}>
                          <span className="action-flag">{labels.get(code) ?? code}</span>
                          <span className="action-detail">{row.details[index]}</span>
                        </div>
                      ))}
                    </td>
                    <td className="muted cell-time">
                      {row.quality_scored_at === null
                        ? "not yet"
                        : formatTime(row.quality_scored_at)}
                    </td>
                    <td>
                      <div className="row-actions">
                        <button
                          type="button"
                          disabled={busyKeys.has(rowKey(row))}
                          onClick={() => void reSearch(row)}
                        >
                          Re-search
                        </button>
                        <button
                          type="button"
                          disabled={busyKeys.has(rowKey(row))}
                          onClick={() => void rebuild(row)}
                        >
                          Rebuild
                        </button>
                        {/* The picker is row 73's and lives on the item page.
                            A second copy of it inside the queue would be a
                            second thing to keep in step. */}
                        <Link className="action-replace" to={`/items/${row.item_id}`}>
                          Replace
                        </Link>
                        {row.dismissed ? (
                          <button
                            type="button"
                            disabled={busyKeys.has(rowKey(row))}
                            onClick={() => void restore(row)}
                          >
                            Restore
                          </button>
                        ) : (
                          <button
                            type="button"
                            disabled={busyKeys.has(rowKey(row))}
                            onClick={() => void dismiss(row)}
                          >
                            Dismiss
                          </button>
                        )}
                      </div>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>

      <div className="action-pager">
        <button
          type="button"
          disabled={offset === 0}
          onClick={() => setOffset(Math.max(offset - PAGE_SIZE, 0))}
        >
          Previous
        </button>
        <span className="muted">
          {rows.length === 0
            ? "Nothing to show"
            : `${offset + 1}–${offset + rows.length} of ${total}`}
        </span>
        <button
          type="button"
          disabled={offset + rows.length >= total}
          onClick={() => setOffset(offset + PAGE_SIZE)}
        >
          Next
        </button>
      </div>
    </>
  );
}
