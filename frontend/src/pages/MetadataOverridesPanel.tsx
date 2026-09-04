import { useCallback, useEffect, useRef, useState } from "react";

import { apiFetch } from "../api/client";
import type {
  MetadataOverride,
  MetadataOverrideWriteResponse,
  MetadataOverridesResponse,
} from "../api/types";

/** Why this panel exists (roadmap row 99): the operator declares, for THIS
 * item, the value a metadata field must hold. It beats every provider source
 * for that field on that item and is re-applied every pass. */
const NOTE =
  "A value typed here is written to Plex for this item and re-applied every " +
  "pass, ahead of anything a provider says. It affects 1 item — this one — " +
  "and nothing else in the library.";

/** C9's disclosure, both halves. An operator told only "the override was
 * removed" would not understand why the tagline they typed is still there. */
const CLEAR_NOTE =
  "Clearing an override removes it and unlocks that field in Plex. A field a " +
  "provider supplies (ratings, content rating, studio, release date, genres) " +
  "is rewritten and re-locked on the next pass. A field no provider supplies " +
  "— title, sort title, summary, tagline — keeps the value you last set " +
  "until Plex itself refreshes it.";

/** Disclosed rather than engineered around: the title drawn on a poster comes
 * from what Plex reported when the item was resolved, so a new title reaches
 * the artwork on the pass AFTER the one that writes it. */
const TITLE_NOTE =
  "Overriding the title also changes the title drawn on this item's artwork, " +
  "one re-render, on the pass after the one that writes it to Plex.";

const GATE = "operations.item_overrides_enabled";

/** The per-item override panel: a field table, an inline edit per row, and
 * the gate's banner.
 *
 * **Nothing here ever fills an input from what Plex or the facts row
 * currently holds.** The server serves `writable` as a list of NAMES for
 * exactly that reason, and an un-overridden field renders EMPTY. Pre-filling
 * would put today's values one Save away from being frozen as overrides —
 * the freezing hazard with an extra click rather than without one.
 *
 * The server is the authority: every write re-reads the listing rather than
 * patching state from the response, which is the posture GroupsPanel and
 * FactsBackfillPanel already take.
 */
export function MetadataOverridesPanel({ itemId }: { itemId: number }) {
  const [state, setState] = useState<MetadataOverridesResponse | null>(null);
  const [drafts, setDrafts] = useState<Record<string, string>>({});
  const [note, setNote] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState<string | null>(null);

  // A ref rather than an effect-local `cancelled`: a save's continuation
  // lands outside any effect -- the neighbouring panels' idiom.
  const live = useRef(true);
  useEffect(() => {
    live.current = true;
    return () => {
      live.current = false;
    };
  }, []);

  const reload = useCallback(async () => {
    const next = await apiFetch<MetadataOverridesResponse>(
      `/api/items/${itemId}/metadata-overrides`,
    );
    if (live.current) {
      setState(next);
      // Drafts are cleared, never seeded from `next.overrides`: an input
      // shows what the operator is typing, and the stored value is shown
      // beside it as text.
      setDrafts({});
    }
  }, [itemId]);

  useEffect(() => {
    reload().catch((caught: Error) => {
      if (live.current) setError(caught.message);
    });
  }, [reload]);

  async function save(field: string) {
    setBusy(field);
    setError(null);
    setNote(null);
    try {
      const outcome = await apiFetch<MetadataOverrideWriteResponse>(
        `/api/items/${itemId}/metadata-overrides/${field}`,
        { method: "PUT", body: JSON.stringify({ value: drafts[field] ?? "" }) },
      );
      if (live.current) {
        setNote(
          `Saved ${outcome.field} as “${outcome.value}”. ` +
            (outcome.queued
              ? "A re-run was queued."
              : "A re-run was already pending, so nothing new was added."),
        );
      }
      await reload();
    } catch (caught) {
      // Shown verbatim. This page is behind require_session, and the server
      // deliberately serves a class name rather than the value that failed,
      // so there is nothing here to leak.
      if (live.current) setError((caught as Error).message);
    } finally {
      if (live.current) setBusy(null);
    }
  }

  async function clear(field: string) {
    setBusy(field);
    setError(null);
    setNote(null);
    try {
      await apiFetch<MetadataOverrideWriteResponse>(
        `/api/items/${itemId}/metadata-overrides/${field}`,
        { method: "DELETE" },
      );
      if (live.current) {
        setNote(
          `Cleared ${field} and unlocked it in Plex. If a provider supplies ` +
            "this field it is rewritten on the next pass; if none does, the " +
            "value you set stays until Plex itself refreshes it.",
        );
      }
      await reload();
    } catch (caught) {
      if (live.current) setError((caught as Error).message);
    } finally {
      if (live.current) setBusy(null);
    }
  }

  if (state === null) {
    return (
      <section className="panel item-panel">
        <h2>Metadata overrides</h2>
        {error !== null ? (
          <p className="page-error">{error}</p>
        ) : (
          <p className="muted">Loading…</p>
        )}
      </section>
    );
  }

  const stored = new Map<string, MetadataOverride>(
    state.overrides.map((entry) => [entry.field, entry]),
  );
  const readOnly = !state.enabled;

  return (
    <section className="panel item-panel">
      <h2>Metadata overrides</h2>
      {readOnly && (
        <p className="muted" role="alert">
          {GATE} is off. Any overrides below are left in place and ignored —
          turning it back on applies them again on the next pass.
        </p>
      )}
      <p className="muted">{NOTE}</p>
      <p className="muted">{CLEAR_NOTE}</p>
      {state.writable.includes("title") && <p className="muted">{TITLE_NOTE}</p>}
      <table className="override-table">
        <thead>
          <tr>
            <th>Field</th>
            <th>Override</th>
            <th>New value</th>
            <th />
          </tr>
        </thead>
        <tbody>
          {state.writable.map((field) => {
            const current = stored.get(field);
            return (
              <tr key={field} data-testid={`override-${field}`}>
                <td className="mono">{field}</td>
                <td>{current === undefined ? <span className="muted">—</span> : current.value}</td>
                <td>
                  <input
                    type="text"
                    aria-label={`New value for ${field}`}
                    value={drafts[field] ?? ""}
                    disabled={readOnly}
                    onChange={(event) =>
                      setDrafts((all) => ({ ...all, [field]: event.target.value }))
                    }
                  />
                </td>
                <td>
                  <button
                    type="button"
                    disabled={readOnly || busy !== null || (drafts[field] ?? "") === ""}
                    onClick={() => void save(field)}
                  >
                    Save
                  </button>
                  {current !== undefined && (
                    <button
                      type="button"
                      disabled={readOnly || busy !== null}
                      onClick={() => void clear(field)}
                    >
                      Clear {field}
                    </button>
                  )}
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
      {note !== null && (
        <p className="muted" role="status">
          {note}
        </p>
      )}
      {error !== null && <p className="page-error">{error}</p>}
    </section>
  );
}
