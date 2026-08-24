/** The six operator-triggered artwork modes, each behind a confirmation.
 *
 * A page rather than a Dashboard card. The Dashboard is a live status surface
 * -- a stream paints it and everything on it is a reading; these are six
 * one-off destructive operations, each with three filter inputs, two buttons,
 * a confirmation step, a result block and several paragraphs of consequence.
 * That is six panels' worth of controls, and none of it is a reading. Putting
 * it in a card would also put "unlock every poster in the library" one scroll
 * below the queue counters an operator watches all day.
 *
 * Two conventions shape the rest:
 *
 *  - **Dry run is the offer, apply is the exception.** Every mode's dry run is
 *    one unguarded click, because a dry run touches nothing. Apply is a
 *    two-step gate, and only one mode may be armed at a time -- two live
 *    confirm buttons on one page is precisely the misclick the gate exists to
 *    prevent.
 *  - **The server's numbers, never a restatement of them.** The result block
 *    renders whatever counts the response carried, under labels that say what
 *    each one counts. A refusal is rendered as an answer with its numbers and
 *    its reason, not as an error -- the plausibility cap firing on a first
 *    real run is the design working.
 */
import { useEffect, useState } from "react";

import { apiFetch } from "../api/client";
import type {
  ArtworkModeRequest,
  ArtworkModeResponse,
  ItemFiltersResponse,
} from "../api/types";
import "./modes.css";

/** What a mode's three controls hold: the request minus the switch that says
 * which button was pressed. */
type ModeFilters = Omit<ArtworkModeRequest, "apply">;

interface ModeSpec {
  /** The `/api/artwork-modes/` path segment, and the per-mode state key. */
  id: string;
  title: string;
  summary: string;
  /** The consequences the counts never mention. Rendered always, not on
   * demand: an operator who has to open a disclosure to learn that reset
   * cannot reclaim the replaced upload will learn it afterwards instead. */
  notes: string[];
  /** False only for backup, which writes files and never touches Plex, so
   * there is no "what would this do" to ask. */
  dryRun: boolean;
  /** False only for backup, whose endpoint takes no body. */
  filters: boolean;
  /** Restricts the type dropdown where the mode itself is restricted. */
  kinds?: string[];
  applyLabel: string;
  confirmLabel: string;
  /** The sentence beside the confirm button. */
  confirmPrompt: string;
}

/** Said on every mode whose applied run writes to Plex: `_run_plex_writing_mode`
 * in routes.py raises the worker fence for all five, not only for restore. */
const PAUSES_PIPELINE =
  "An applied run pauses the whole worker pipeline for its duration: jobs " +
  "already in flight finish, then the pool sits idle until this mode " +
  "completes.";

const MODES: ModeSpec[] = [
  {
    id: "backup",
    title: "Backup",
    summary:
      "Copy the artwork Plex is serving right now into the backup tree, in the " +
      "Kometa layout Restore reads back.",
    notes: [
      "Nothing on Plex is touched. This only reads what Plex serves and writes " +
        "files under the configured backup root, so there is no dry run and no " +
        "pipeline pause.",
      "It overwrites whatever the backup tree already holds. Run it on a library " +
        "this service has already badged and the badged artwork becomes the " +
        "backup — take the backup before the first pass, not after.",
      "written, skipped and failed count one artwork field each, so a movie " +
        "contributes two and they do not add up to items.",
    ],
    dryRun: false,
    filters: false,
    applyLabel: "Run backup",
    confirmLabel: "Confirm backup",
    confirmPrompt: "This overwrites the existing backup tree.",
  },
  {
    id: "restore",
    title: "Restore",
    summary: "Push the backup tree back onto Plex, filtered.",
    notes: [
      PAUSES_PIPELINE,
      "Only items that actually have a file in the backup tree are pushed. " +
        "files counts backup files, not items — an item can carry both a poster " +
        "and a background.",
    ],
    dryRun: true,
    filters: true,
    applyLabel: "Apply…",
    confirmLabel: "Confirm apply",
    confirmPrompt: "This writes to Plex and cannot be undone by this page.",
  },
  {
    id: "revert",
    title: "Remove overlays",
    summary:
      "Re-upload the un-badged base image from the assets tree, which is the " +
      "same picture without the badges.",
    notes: [
      PAUSES_PIPELINE,
      "This uploads over the badged image rather than deleting it, so the badged " +
        "upload stays on the Plex server.",
      "Items whose base image is not on disk are counted as candidates and never " +
        "pushed — a render that failed or produced nothing leaves a row naming a " +
        "file that was never written.",
    ],
    dryRun: true,
    filters: true,
    applyLabel: "Apply…",
    confirmLabel: "Confirm apply",
    confirmPrompt: "This writes to Plex and cannot be undone by this page.",
  },
  {
    id: "reset",
    title: "Reset to Plex artwork",
    summary:
      "Unlock the poster and the background and hand both fields back to Plex's " +
      "own agent artwork.",
    notes: [
      "It cannot delete the artwork it replaces. Those images stay on the Plex " +
        "server as an orphaned upload:// image each, because Plex offers no API " +
        "to remove one — the reset frees the field, not the disk space.",
      PAUSES_PIPELINE,
      "Only fields still showing artwork this service uploaded are touched, told " +
        "apart by their EXIF provenance, so a poster set by hand is left alone.",
      "fields, reset and failed count (item, field) pairs, not items — one item " +
        "can contribute two. items showing our art is the per-item number.",
      "A field Plex holds no agent artwork for counts as failed: it was unlocked, " +
        "but what is displayed did not change.",
    ],
    dryRun: true,
    filters: true,
    applyLabel: "Apply…",
    confirmLabel: "Confirm apply",
    confirmPrompt: "This writes to Plex and cannot be undone by this page.",
  },
  {
    id: "logo",
    title: "Logo updater",
    summary:
      "Fetch and upload a clearlogo for every movie or show Plex shows none for. " +
      "Items that already have one are left alone.",
    notes: [
      "On a large library the first real run will normally come back refused by " +
        "the plausibility cap. That is the design and not a failure: it is the " +
        "cap asking whether thousands of changes are really what you meant. " +
        "Raise the cap or narrow the filters below, then run it again.",
      PAUSES_PIPELINE,
      "uploaded counts logos that reached Plex, including any whose marker write " +
        "afterwards failed. Logo revert will not claim those back — nothing in " +
        "this response can tell them apart, only the server log can.",
      "failed means the item still has no logo, whatever the cause: no provider " +
        "had one, the only candidate was an SVG, or the upload itself did not go " +
        "through. The three are folded into one number.",
      "A dry run spends no provider call at all. It counts the items missing a " +
        "logo; it does not look up which logo each would get.",
    ],
    dryRun: true,
    filters: true,
    kinds: ["movie", "show"],
    applyLabel: "Apply…",
    confirmLabel: "Confirm apply",
    confirmPrompt: "This writes to Plex and cannot be undone by this page.",
  },
  {
    id: "logo-revert",
    title: "Logo revert",
    summary: "Remove the clearlogos this service set.",
    notes: [
      "It acts on only the logos this service set and that Plex still has " +
        "selected. A logo set by hand, or one that replaced ours since, is never " +
        "touched.",
      PAUSES_PIPELINE,
      "Unlike the poster reset this leaves nothing orphaned: Plex exposes a " +
        "delete for the clearlogo field, so the image really does go.",
    ],
    dryRun: true,
    filters: true,
    kinds: ["movie", "show"],
    applyLabel: "Apply…",
    confirmLabel: "Confirm apply",
    confirmPrompt: "This writes to Plex and cannot be undone by this page.",
  },
];

/** What each count actually counts, in the words the mode's own docstring
 * uses. The keys that are NOT here fall through to the raw name rather than
 * being dropped: a count this table has not caught up with should read oddly,
 * not vanish. */
const COUNT_LABELS: Record<string, string> = {
  items: "items matched",
  items_with_backup: "items with a file in the backup tree",
  items_with_base: "items with a base image on disk",
  items_with_our_art: "items showing our art",
  items_with_our_logo: "items showing our logo",
  items_missing_logo: "items missing a logo",
  files: "files to push",
  fields: "(item, field) pairs",
  pushed: "pushed",
  reset: "fields reset",
  cleared: "logos cleared",
  uploaded: "logos uploaded",
  written: "artwork files written",
  skipped: "skipped",
  failed: "failed",
};

/** The envelope keys that are not counts. */
const NOT_COUNTS = new Set(["mode", "status", "dry_run", "reason", "note"]);

/** Which pill colour a status wears. The statuses are free text from five
 * dataclasses ("dry run", "backed up", "backup failed", "refused", "reset"),
 * so this maps the ones that are not plain successes rather than enumerating
 * all of them.
 *
 * "refused" wears the warn colour, not the error one. A refusal is the
 * plausibility cap answering the question it exists to ask, and on a large
 * library it is the *expected* result of the logo updater's first real run --
 * painting it red would say "something went wrong" about the safeguard
 * working. Only a genuinely failed run gets the error colour. */
function statusPill(status: string): string {
  if (status.endsWith("failed")) return "pill-error";
  if (status === "refused" || status === "dry run") return "pill-skipped";
  return "pill-ok";
}

function countsOf(response: ArtworkModeResponse): [string, number][] {
  return Object.entries(response).filter(
    (entry): entry is [string, number] =>
      !NOT_COUNTS.has(entry[0]) && typeof entry[1] === "number",
  );
}

function ModeResult({ response }: { response: ArtworkModeResponse }) {
  return (
    /* `status` rather than `alert`: this is the outcome of something the
     * operator asked for, including a refusal, and it must not be announced
     * as an error. The real errors below use `alert`. */
    <div className="mode-result" role="status">
      <div className="mode-result-head">
        <span className={`pill ${statusPill(response.status)}`}>{response.status}</span>
        {response.dry_run === true && (
          <span className="muted">Nothing was changed.</span>
        )}
      </div>

      <div className="mode-counts">
        {countsOf(response).map(([key, value]) => (
          <div className="mode-count" key={key}>
            <span className="mode-count-value">{value}</span>
            <span className="mode-count-label">{COUNT_LABELS[key] ?? key}</span>
          </div>
        ))}
      </div>

      {response.reason !== undefined && <p className="mode-reason">{response.reason}</p>}
      {response.note !== undefined && <p className="mode-note">Note: {response.note}</p>}
    </div>
  );
}

export function Modes() {
  const [filters, setFilters] = useState<ItemFiltersResponse | null>(null);
  /** Held apart from the per-mode errors for the Library page's reason: the
   * dropdowns being unavailable is not any one mode's failure, and neither
   * should erase the other. */
  const [filtersError, setFiltersError] = useState<string | null>(null);

  /** The filters each mode's controls hold. `apply` is deliberately not part
   * of this: it is decided by which button was pressed, never stored. */
  const [chosen, setChosen] = useState<Record<string, ModeFilters>>({});
  /** The one mode currently armed for apply, if any. */
  const [armed, setArmed] = useState<string | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [results, setResults] = useState<Record<string, ArtworkModeResponse>>({});
  const [errors, setErrors] = useState<Record<string, string>>({});

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

  /** A filter change is a change to the request the gate's grant was for, so
   * the grant does not survive it: arming and re-arming is cheap, an applied
   * run against the wrong filters is not. The mode's stored result and error
   * go with it, both for the same reason -- either would go on describing a
   * request this mode no longer holds. */
  function setFilter(id: string, patch: ModeFilters) {
    setChosen((previous) => ({ ...previous, [id]: { ...previous[id], ...patch } }));
    setArmed((previous) => (previous === id ? null : previous));
    setResults((previous) => {
      if (!(id in previous)) return previous;
      const next = { ...previous };
      delete next[id];
      return next;
    });
    setErrors((previous) => {
      if (!(id in previous)) return previous;
      const next = { ...previous };
      delete next[id];
      return next;
    });
  }

  /** The request body, built from what the operator actually chose.
   *
   * Empty controls are omitted rather than sent as "": the endpoint treats a
   * present filter as a filter, so `library: ""` would ask for the items in a
   * library called "" -- none of them. `apply` is always sent, so the run is
   * this page's decision and never the config default's. */
  function bodyFor(spec: ModeSpec, apply: boolean): ArtworkModeRequest {
    const current = chosen[spec.id];
    const body: ArtworkModeRequest = { apply };
    if (current?.type) body.type = current.type;
    if (current?.library) body.library = current.library;
    if (current?.item_id !== undefined) body.item_id = current.item_id;
    return body;
  }

  async function run(spec: ModeSpec, apply: boolean) {
    setArmed(null);
    setBusy(spec.id);
    setErrors((previous) => {
      const next = { ...previous };
      delete next[spec.id];
      return next;
    });
    try {
      const response = await apiFetch<ArtworkModeResponse>(
        `/api/artwork-modes/${spec.id}`,
        spec.filters
          ? { method: "POST", body: JSON.stringify(bodyFor(spec, apply)) }
          : { method: "POST" },
      );
      setResults((previous) => ({ ...previous, [spec.id]: response }));
    } catch (caught) {
      setErrors((previous) => ({ ...previous, [spec.id]: (caught as Error).message }));
    } finally {
      setBusy(null);
    }
  }

  /** The kinds one mode's type control offers. A logo belongs to a movie or a
   * show and to nothing below one, so offering "season" there would return
   * zero items with no explanation of why. */
  function kindsFor(spec: ModeSpec): string[] {
    const available = filters?.kinds ?? [];
    if (spec.kinds === undefined) return available;
    return available.filter((kind) => spec.kinds?.includes(kind));
  }

  return (
    <>
      <div className="page-header">
        <h1>Run modes</h1>
        <span className="muted">Dry run first. Every apply is confirmed.</span>
      </div>

      {filtersError !== null && (
        <p className="page-error">Filters are unavailable: {filtersError}</p>
      )}

      <div className="mode-grid">
        {MODES.map((spec) => {
          const current = chosen[spec.id];
          const running = busy === spec.id;
          return (
            /* A named region, so every control is addressable inside the card
             * it belongs to -- six cards carry a "Library" select. */
            <section className="panel mode-card" aria-label={spec.title} key={spec.id}>
              <h2>{spec.title}</h2>
              <p className="mode-summary">{spec.summary}</p>

              <ul className="mode-notes">
                {spec.notes.map((note) => (
                  <li key={note}>{note}</li>
                ))}
              </ul>

              {spec.filters && (
                <div className="mode-filters">
                  <label>
                    Type
                    <select
                      value={current?.type ?? ""}
                      onChange={(event) =>
                        setFilter(spec.id, { type: event.target.value || undefined })
                      }
                    >
                      <option value="">All types</option>
                      {kindsFor(spec).map((kind) => (
                        <option key={kind} value={kind}>
                          {kind}
                        </option>
                      ))}
                    </select>
                  </label>
                  <label>
                    Library
                    <select
                      value={current?.library ?? ""}
                      onChange={(event) =>
                        setFilter(spec.id, { library: event.target.value || undefined })
                      }
                    >
                      <option value="">All libraries</option>
                      {(filters?.libraries ?? []).map((library) => (
                        <option key={library} value={library}>
                          {library}
                        </option>
                      ))}
                    </select>
                  </label>
                  <label>
                    Item ID
                    <input
                      type="number"
                      min={1}
                      value={current?.item_id ?? ""}
                      placeholder="Any"
                      onChange={(event) => {
                        const raw = event.target.value.trim();
                        setFilter(spec.id, {
                          // An empty or unparseable box is "no filter", never
                          // `item_id: NaN` -- which serialises to `null` and
                          // would read as an explicit "no item".
                          item_id: raw === "" ? undefined : Number(raw) || undefined,
                        });
                      }}
                    />
                  </label>
                </div>
              )}

              <div className="row-actions mode-actions">
                {spec.dryRun && (
                  <button
                    type="button"
                    disabled={running}
                    onClick={() => void run(spec, false)}
                  >
                    {running ? "Running…" : "Dry run"}
                  </button>
                )}

                {armed === spec.id ? (
                  <>
                    <span className="mode-confirm">{spec.confirmPrompt}</span>
                    <button
                      type="button"
                      className="primary"
                      disabled={running}
                      onClick={() => void run(spec, true)}
                    >
                      {spec.confirmLabel}
                    </button>
                    <button type="button" onClick={() => setArmed(null)}>
                      Cancel
                    </button>
                  </>
                ) : (
                  /* Arms the gate. It must never post: see the mutation proof
                   * in Modes.test.tsx. */
                  <button
                    type="button"
                    disabled={running}
                    onClick={() => setArmed(spec.id)}
                  >
                    {spec.applyLabel}
                  </button>
                )}
              </div>

              {errors[spec.id] !== undefined && (
                <p className="row-error" role="alert">
                  {errors[spec.id]}
                </p>
              )}

              {results[spec.id] !== undefined && <ModeResult response={results[spec.id]} />}
            </section>
          );
        })}
      </div>
    </>
  );
}
