import { useCallback, useEffect, useRef, useState } from "react";

import { apiFetch } from "../api/client";
import type { IdMismatchRow, IdMismatchesResponse } from "../api/types";
import "./mismatches.css";

/** The three groups, in the order the endpoint fills them and the page reads
 * them: the disagreements first, because those are the rows that cannot be
 * found any other way. */
type GroupKey = "mismatched" | "arr_only" | "plex_only";

const GROUPS: { key: GroupKey; title: string; blurb: string }[] = [
  {
    key: "mismatched",
    title: "Mismatched ids",
    blurb:
      "One folder, both records, at least one id disagreeing. The differing ids are highlighted.",
  },
  {
    key: "arr_only",
    title: "In Radarr/Sonarr only",
    blurb:
      "A folder the service manages that no Plex item points at — every job under it fails to resolve.",
  },
  {
    key: "plex_only",
    title: "In Plex only",
    blurb: "A Plex item under the configured root that the service has never registered.",
  },
];

/** One side's ids, with the disagreeing ones marked.
 *
 * Both sides are always rendered in full: an operator fixing a mismatch by
 * hand needs the ids that agree as much as the one that does not, because the
 * agreeing id is usually what identifies which record is the wrong one.
 *
 * `side` is which of the two `no_ids_on_*` markers in `differing` belongs to
 * this side's empty case: a matched pair where this side has no ids at all is
 * exactly the mismatch, not the ordinary "nothing to show" of an arr_only or
 * plex_only row's absent counterpart, and reads that way rather than as a
 * plain dash.
 */
function Ids({
  ids,
  differing,
  side,
}: {
  ids: Record<string, string>;
  differing: string[];
  side: "plex" | "arr";
}) {
  const agents = Object.keys(ids);
  if (agents.length === 0) {
    if (differing.includes(`no_ids_on_${side}`)) {
      return <span className="mismatch-id differs">no ids</span>;
    }
    return <span className="muted">—</span>;
  }
  return (
    <span className="mismatch-ids">
      {agents.map((agent) => (
        <span
          key={agent}
          className={differing.includes(agent) ? "mismatch-id differs" : "mismatch-id"}
        >
          <span className="mismatch-agent">{agent}</span>{" "}
          <span className="mismatch-value">{ids[agent]}</span>
        </span>
      ))}
    </span>
  );
}

function Row({ row }: { row: IdMismatchRow }) {
  return (
    <tr>
      <td>
        <span className="mismatch-title">{row.plex_title ?? row.arr_title}</span>
        {row.year !== null && <span className="muted"> ({row.year})</span>}
        {/* Both titles, when the two records disagree about that too -- which
            is the most legible symptom of a wrong match there is. */}
        {row.plex_title !== null &&
          row.arr_title !== null &&
          row.plex_title !== row.arr_title && (
            <span className="mismatch-other-title">{row.service}: {row.arr_title}</span>
          )}
      </td>
      <td className="cell-time">
        {row.library ?? <span className="muted">—</span>}
        <span className="muted mono"> {row.service}</span>
      </td>
      <td className="mono cell-wrap">{row.path}</td>
      <td>
        <Ids ids={row.plex_ids} differing={row.differing} side="plex" />
      </td>
      <td>
        <Ids ids={row.arr_ids} differing={row.differing} side="arr" />
      </td>
      <td className="muted mono cell-time">{row.rating_key ?? "—"}</td>
    </tr>
  );
}

export function Mismatches() {
  const [result, setResult] = useState<IdMismatchesResponse | null>(null);
  const [scanning, setScanning] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // The scan runs from a click handler and takes seconds against a real
  // library, so a response can easily land after the page has gone.
  const live = useRef(true);
  useEffect(() => {
    live.current = true;
    return () => {
      live.current = false;
    };
  }, []);

  const scan = useCallback(async () => {
    setScanning(true);
    setError(null);
    try {
      const response = await apiFetch<IdMismatchesResponse>("/api/id-mismatches");
      if (!live.current) return;
      setResult(response);
    } catch (caught) {
      if (!live.current) return;
      setError((caught as Error).message);
    } finally {
      if (live.current) setScanning(false);
    }
  }, []);

  return (
    <>
      <div className="page-header">
        <h1>ID mismatches</h1>
        {result !== null && (
          <span className="muted">
            {result.total} row{result.total === 1 ? "" : "s"}
            {result.total > result.limit ? ` (showing ${result.limit})` : ""}
          </span>
        )}
      </div>

      <div className="panel">
        {/* Scanned on demand, never polled: this reads both services in full
            and walks every configured Plex section, which is seconds of work
            for a question an operator asks a few times a year. */}
        <div className="mismatch-controls">
          <button type="button" onClick={() => void scan()} disabled={scanning}>
            {scanning ? "Scanning…" : "Scan"}
          </button>
          <span className="muted">
            Reads Radarr and Sonarr in full and walks every configured Plex library. Takes a
            few seconds.
          </span>
        </div>
        {result !== null && result.skipped.length > 0 && (
          <p className="mismatch-note">
            Not scanned: {result.skipped.join(", ")} — disabled, or without a base URL or api
            key.
          </p>
        )}
        {result !== null && Object.keys(result.refused).length > 0 && (
          <p className="mismatch-note">
            Refused:{" "}
            {Object.entries(result.refused)
              .map(([service, reason]) => `${service} — ${reason}`)
              .join("; ")}
          </p>
        )}
        {result !== null && result.unmapped > 0 && (
          <p className="mismatch-note">
            {result.unmapped} Plex item{result.unmapped === 1 ? "" : "s"} sit outside the
            configured root and were not compared.
          </p>
        )}
        {result !== null && result.arr_unmapped > 0 && (
          <p className="mismatch-note">
            {result.arr_unmapped} Radarr/Sonarr entr{result.arr_unmapped === 1 ? "y has" : "ies have"}{" "}
            no path on disk and could not be compared.
          </p>
        )}
      </div>

      {error !== null && <p className="page-error">{error}</p>}

      {result === null ? (
        <div className="panel">
          <p className="muted">Nothing scanned yet.</p>
        </div>
      ) : result.total === 0 ? (
        <div className="panel">
          <p className="empty">No mismatches found.</p>
        </div>
      ) : (
        GROUPS.map((group) => {
          const rows = result[group.key];
          const count = result.counts[group.key] ?? 0;
          if (count === 0) return null;
          return (
            <div className="panel" key={group.key}>
              <div className="mismatch-heading">
                <h2>
                  <span className="mismatch-group-title">{group.title}</span>{" "}
                  <span className="muted">({count})</span>
                </h2>
                <p className="muted">{group.blurb}</p>
              </div>
              <div className="table-scroll">
                <table>
                  <thead>
                    <tr>
                      <th>Title</th>
                      <th>Library</th>
                      <th>Path</th>
                      <th>Plex ids</th>
                      <th>Radarr/Sonarr ids</th>
                      <th>Rating key</th>
                    </tr>
                  </thead>
                  <tbody>
                    {rows.map((row) => (
                      <Row key={`${row.service}-${row.path}`} row={row} />
                    ))}
                  </tbody>
                </table>
              </div>
            </div>
          );
        })
      )}
    </>
  );
}
