import type { ItemServerOutcome } from "../api/types";
import { formatTime } from "../format";

/** Whether this server answered `absent` for everything it was asked about.
 *
 * `absent` is a statement about the LIBRARY, not about any one write, so a
 * server that answered it renders as one neutral line rather than as a row of
 * pills each repeating the same fact. Checked across both halves: a server
 * can only be absent for both, and a mixture would mean the presence pass and
 * the pipeline disagree, which is worth showing rather than flattening. */
function isAbsent(entry: ItemServerOutcome): boolean {
  const statuses = [
    ...(entry.metadata === null ? [] : [entry.metadata.status]),
    ...entry.artwork.map((row) => row.status),
  ];
  return statuses.length > 0 && statuses.every((status) => status === "absent");
}

/** The latest thing that happened on this server, for the "When" column.
 *
 * The completion stamp where there is one and the attempt stamp otherwise, so
 * a row that has only ever failed still says when it last tried rather than
 * nothing at all. ISO-8601 in UTC sorts lexically, which is why a plain string
 * sort is enough to find the newest of them. */
function timeOf(entry: ItemServerOutcome): string | null {
  const stamps = [
    entry.metadata?.written_at ?? entry.metadata?.attempted_at ?? null,
    ...entry.artwork.map((row) => row.uploaded_at ?? row.attempted_at),
  ].filter((value): value is string => value !== null);
  return stamps.length === 0 ? null : (stamps.sort().at(-1) ?? null);
}

/** The item page's per-server table: artwork status, metadata status, when,
 * and the detail on hover -- the same pill-and-title idiom the render table's
 * delivery chips already use. */
export default function ServerOutcomes({ servers }: { servers: ItemServerOutcome[] }) {
  if (servers.length === 0) {
    return <p className="muted">No per-server outcome recorded yet.</p>;
  }
  return (
    <table className="server-outcomes">
      <thead>
        <tr>
          <th>Server</th>
          <th>Artwork</th>
          <th>Metadata</th>
          <th>When</th>
        </tr>
      </thead>
      <tbody>
        {servers.map((entry) => {
          const when = timeOf(entry);
          if (isAbsent(entry)) {
            return (
              <tr key={entry.server}>
                <td>{entry.server}</td>
                <td className="muted" colSpan={2}>
                  {entry.server} does not carry this library
                </td>
                <td className="muted cell-time">{formatTime(when)}</td>
              </tr>
            );
          }
          return (
            <tr key={entry.server}>
              <td>{entry.server}</td>
              <td>
                {entry.artwork.length === 0 ? (
                  <span className="muted">—</span>
                ) : (
                  entry.artwork.map((row) => (
                    <span
                      key={row.art_kind}
                      className={`pill pill-${row.status}`}
                      title={row.detail ?? undefined}
                    >
                      {row.art_kind} {row.status}
                    </span>
                  ))
                )}
              </td>
              <td>
                {entry.metadata === null ? (
                  <span className="muted">—</span>
                ) : (
                  <span
                    className={`pill pill-${entry.metadata.status}`}
                    title={entry.metadata.detail ?? undefined}
                  >
                    metadata {entry.metadata.status}
                  </span>
                )}
              </td>
              <td className="muted cell-time">{formatTime(when)}</td>
            </tr>
          );
        })}
      </tbody>
    </table>
  );
}
