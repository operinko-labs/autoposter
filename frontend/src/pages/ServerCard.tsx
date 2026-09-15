/** One media server's card: address, credential, libraries, switches, and the
 * buttons that act on that one server.
 *
 * The card SAVES ON ITS OWN and never joins the page's shared pending change,
 * because a server is a unit: an address saved without the credential that
 * goes with it is a deployment that comes back from its next start as the
 * first-start wizard. `onChanged` is how the page re-reads the servers and
 * the configuration after a write; the card holds no part of the page's
 * pending edit and never writes into it.
 *
 * THE ORDER OF THE FIRST TWO CONTROLS IS THE ADD FLOW. A server with no block
 * yet cannot be saved until its credential is stored -- the save route refuses
 * it, in the sentence rendered here verbatim -- so the credential field is
 * first on a card for a server this deployment has not configured, and second
 * on one it has.
 *
 * THE TICK-LIST IS THE COMPLEMENT of `excluded_libraries`: ticked means
 * managed, and a save sends the unticked ones, which is what the schema's
 * field holds. A library the server no longer lists keeps its exclusion
 * rather than silently coming back, because the list is seeded from the
 * stored exclusions and only the reload adds names to it.
 *
 * THE SWITCHES ARE A DELTA, and the listing serves no values for them, so
 * each is a three-way control whose third state is "leave as it is". A
 * checkbox here would have to pick a position for a switch this card has
 * never been told the value of, and an unticked box beside a switch that is
 * on is a lie the operator has no way to see through.
 *
 * NO SECRET IS EVER READ BACK. The credential field holds a typed value for
 * exactly as long as the request that carries it, and is emptied when that
 * request ends, refused or not.
 */
import { useEffect, useState } from "react";

import { refusalMessage, RESTART_NOTE } from "../api/overrides";
import {
  cancelCatchUp,
  catchUpRun,
  checkServer,
  clearServerCredential,
  fetchCatchUp,
  fetchLibraries,
  removeServer,
  retryFailed,
  saveServer,
  setServerCredential,
  startCatchUp,
  type CatchUpProgress,
  type CheckResult,
  type LibraryRow,
  type ServerRow,
} from "../api/servers";
import { formatTime } from "../format";

const LABELS: Record<string, string> = { plex: "Plex", jellyfin: "Jellyfin" };

/** The switches this card owns, per server, matching `api/servers.py`'s
 * `SERVER_SWITCHES` exactly. A path that table does not name is a 422 naming
 * the key, which is a refusal an operator cannot act on, so the two tables say
 * the same thing. */
const SWITCHES: Record<string, { path: string; label: string }[]> = {
  plex: [
    { path: "badges.upload_to_plex", label: "Upload badged artwork to Plex" },
    { path: "operations.write_to_plex", label: "Write metadata to Plex" },
  ],
  jellyfin: [
    { path: "badges.upload_to_jellyfin", label: "Upload badged artwork to Jellyfin" },
    { path: "operations.write_to_jellyfin", label: "Write metadata to Jellyfin" },
    {
      path: "jellyfin.replace_thumb_with_backdrop",
      label: "Also upload the background into the Thumb slot",
    },
  ],
};

/** The connection pill. A STATE and nothing else, plus the one piece of the
 * server's own text this tab shows: the version it answered with. */
function pillFor(server: ServerRow, probe: CheckResult | null) {
  const ok = probe?.ok ?? server.health.ok;
  if (ok === null) return { text: "not checked", className: "config-pill" };
  if (ok) {
    const version = probe?.version ?? null;
    return {
      text: version === null || version === "" ? "connected" : `connected, version ${version}`,
      className: "config-pill on",
    };
  }
  if (probe !== null && probe.refused) return { text: "refused", className: "config-pill off" };
  return { text: "unreachable", className: "config-pill off" };
}

export function ServerCard({
  server,
  revision,
  onChanged,
}: {
  server: ServerRow;
  /** The configuration revision the page was served. Required by both of this
   * card's writes, so a page that was served none cannot save: the routes
   * refuse a body without one, and that refusal is the only thing standing
   * between a concurrent settings save and a silent revert of it. */
  revision: string | null;
  onChanged: () => Promise<void> | void;
}) {
  const label = LABELS[server.name] ?? server.name;
  const [address, setAddress] = useState(server.url ?? "");
  const [credential, setCredential] = useState("");
  const [libraries, setLibraries] = useState<LibraryRow[] | null>(null);
  const [excluded, setExcluded] = useState<string[]>(server.excluded_libraries);
  const [switches, setSwitches] = useState<Record<string, boolean>>({});
  const [probe, setProbe] = useState<CheckResult | null>(null);
  const [progress, setProgress] = useState<CatchUpProgress | null>(null);
  const [confirming, setConfirming] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [note, setNote] = useState<string | null>(null);

  // A configured server's catch-up is read once, when the card is opened: the
  // buttons below act on a run, and a card that offered "Catch up" without
  // saying whether one is already in flight would have the operator start a
  // second one to find out. A server with no block has no runs to read, and a
  // read that fails leaves the line off rather than putting a failure about
  // the backlog in front of the fields this card is for.
  useEffect(() => {
    if (!server.configured) return;
    let cancelled = false;
    fetchCatchUp(server.name)
      .then((found) => {
        if (!cancelled) setProgress(found);
      })
      .catch(() => {});
    return () => {
      cancelled = true;
    };
  }, [server.configured, server.name]);

  /** The typed-address rule, client side: an address this request names must
   * carry the credential to use against it, and a card with nothing typed
   * sends neither field and gets the booted address and the held credential.
   * Sending both or neither is the only shape this card produces. */
  function probeBody() {
    const typedAddress = address !== (server.url ?? "");
    if (typedAddress || credential !== "") {
      return { url: address, credential_value: credential };
    }
    return {};
  }

  /** Run one action, with this card's one error line and one note line.
   *
   * `clearsCredential` is every action that SENT the typed credential: the
   * value is dropped when its request ends, won or lost, because a refused
   * value is one to type again and a field left holding a secret is one
   * reload away from being the only copy of it on the screen. */
  async function run(
    action: () => Promise<string | null>,
    { clearsCredential = false }: { clearsCredential?: boolean } = {},
  ) {
    setBusy(true);
    setError(null);
    setNote(null);
    try {
      setNote(await action());
    } catch (caught) {
      // The server's own sentence, whatever shape it arrived in.
      setError(refusalMessage(caught));
    } finally {
      if (clearsCredential) setCredential("");
      setBusy(false);
    }
  }

  const pill = pillFor(server, probe);
  const catchUp = catchUpRun(progress);
  const detail = probe === null ? server.health.detail : probe.detail;
  const writable = revision !== null;

  const credentialField = (
    <div className="config-row">
      <span className="config-key">{`${label} credential`}</span>
      <span className="config-value">
        <input
          type="password"
          aria-label={`${label} credential`}
          // The same posture as every other field in this app that takes a
          // secret: a password manager must not offer to save a server token
          // as a credential for this site, nor fill one in here.
          autoComplete="off"
          value={credential}
          onChange={(event) => setCredential(event.target.value)}
        />
        <span className="config-pill">{`credential: ${server.credential_source}`}</span>
        <button
          type="button"
          disabled={busy || credential === ""}
          onClick={() =>
            void run(
              async () => {
                await setServerCredential(server.name, credential);
                await onChanged();
                return `Stored ${label}'s credential. It is not shown again.`;
              },
              { clearsCredential: true },
            )
          }
        >
          Save credential
        </button>
        {server.credential_source === "stored" && (
          <button
            type="button"
            disabled={busy}
            onClick={() =>
              void run(async () => {
                const cleared = await clearServerCredential(server.name);
                await onChanged();
                return cleared.credential_source === "unset"
                  ? `Cleared ${label}'s stored credential. No other source supplies it.`
                  : `Cleared ${label}'s stored credential. It now comes from the ${cleared.credential_source}.`;
              })
            }
          >
            Clear credential
          </button>
        )}
      </span>
    </div>
  );

  const addressField = (
    <div className="config-row">
      <span className="config-key">{`${label} address`}</span>
      <span className="config-value">
        <input
          type="text"
          aria-label={`${label} address`}
          value={address}
          onChange={(event) => setAddress(event.target.value)}
        />
      </span>
    </div>
  );

  return (
    // Not a panel of its own: this card sits inside the Servers tab's
    // accordion, which is the panel, and a second bordered box inside it
    // would be a fourth level of visual nesting where the page allows three.
    <section className="server-card">
      <h2>
        {label}
        <span className={pill.className}>{pill.text}</span>
        {server.restart_pending && (
          <span
            className="config-pill restart"
            title="This server is saved, and this deployment is still running on what it started with."
          >
            restart to apply
          </span>
        )}
      </h2>
      {error !== null && <p className="page-error">{error}</p>}
      {note !== null && <p className="config-saved">{note}</p>}
      {!server.configured && (
        <p className="muted config-note">
          Store this server&apos;s credential before saving it: a configured
          server without one sends this deployment back to first-start setup.
        </p>
      )}

      {!server.configured && credentialField}
      {addressField}
      {server.configured && credentialField}

      {detail !== null && <p className="muted">{detail}</p>}
      {probe === null && server.health.checked_at !== null && (
        <p className="muted">{`Last answered ${formatTime(server.health.checked_at)}.`}</p>
      )}

      {libraries !== null && (
        <fieldset className="server-libraries">
          <legend>Managed libraries</legend>
          {libraries.length === 0 && (
            <p className="muted">{`${label} listed no libraries.`}</p>
          )}
          {libraries.map((library) => (
            <label key={library.id}>
              <input
                type="checkbox"
                checked={!excluded.includes(library.name)}
                onChange={() =>
                  setExcluded((current) =>
                    current.includes(library.name)
                      ? current.filter((name) => name !== library.name)
                      : [...current, library.name],
                  )
                }
              />
              {library.name}
            </label>
          ))}
        </fieldset>
      )}

      {(SWITCHES[server.name] ?? []).map((entry) => (
        <div className="config-row" key={entry.path}>
          <span className="config-key">{entry.label}</span>
          <span className="config-value">
            <select
              aria-label={entry.label}
              value={
                switches[entry.path] === undefined
                  ? ""
                  : switches[entry.path]
                    ? "on"
                    : "off"
              }
              onChange={(event) =>
                setSwitches((current) => {
                  const chosen = event.target.value;
                  if (chosen === "") {
                    const kept = { ...current };
                    delete kept[entry.path];
                    return kept;
                  }
                  return { ...current, [entry.path]: chosen === "on" };
                })
              }
            >
              <option value="">leave as it is</option>
              <option value="on">on</option>
              <option value="off">off</option>
            </select>
          </span>
        </div>
      ))}
      {(SWITCHES[server.name] ?? []).length > 0 && (
        <p className="muted config-note">
          A switch left at &ldquo;leave as it is&rdquo; is not sent, so this
          card changes only what is set here.
        </p>
      )}

      {progress !== null && (
        <p className="muted">
          {catchUp === null
            ? `No catch-up has run for ${label} yet.`
            : `Catch up ${catchUp.status}: ${catchUp.due} due, ${catchUp.done} done, ${catchUp.failed} failed, of ${catchUp.total}.`}
        </p>
      )}

      <div className="config-actions">
        <button
          type="button"
          disabled={busy}
          onClick={() =>
            void run(
              async () => {
                setProbe(await checkServer(server.name, probeBody()));
                return null;
              },
              { clearsCredential: true },
            )
          }
        >
          Check connection
        </button>
        <button
          type="button"
          disabled={busy}
          onClick={() =>
            void run(
              async () => {
                setLibraries(await fetchLibraries(server.name, probeBody()));
                return null;
              },
              { clearsCredential: true },
            )
          }
        >
          Reload libraries
        </button>
        {server.configured && (
          <button
            type="button"
            disabled={busy}
            onClick={() =>
              void run(async () => {
                await startCatchUp(server.name);
                // The counts come from the read, which is the one place they
                // are computed; starting one answers the run and its cadence.
                setProgress(await fetchCatchUp(server.name));
                return null;
              })
            }
          >
            Catch up
          </button>
        )}
        {catchUp !== null && catchUp.finished_at === null && (
          <button
            type="button"
            disabled={busy}
            onClick={() =>
              void run(async () => {
                const cancelled = await cancelCatchUp(server.name);
                setProgress(await fetchCatchUp(server.name));
                return cancelled.detail;
              })
            }
          >
            Cancel catch-up
          </button>
        )}
        {server.configured && (
          <button
            type="button"
            disabled={busy}
            onClick={() =>
              void run(async () => {
                const retried = await retryFailed(server.name);
                return `Re-armed ${retried.artwork} artwork and ${retried.metadata} metadata deliveries for ${label}.`;
              })
            }
          >
            Retry failed
          </button>
        )}
        {server.configured && (
          <button
            type="button"
            disabled={busy || !writable}
            onClick={() => setConfirming(true)}
          >
            Remove server
          </button>
        )}
        <button
          type="button"
          disabled={busy || !writable}
          onClick={() =>
            void run(async () => {
              await saveServer(server.name, {
                url: address,
                excluded_libraries: excluded,
                switches,
                expected_revision: revision ?? "",
                confirm: false,
              });
              // Applied, so they are not sent again by the next save.
              setSwitches({});
              await onChanged();
              return (
                `Saved ${label}. This deployment is still running on the ` +
                "address it started with, so a connection check reaches the " +
                "saved address only once it has restarted, or while it is " +
                `typed in above. ${RESTART_NOTE}`
              );
            })
          }
        >
          Save
        </button>
      </div>
      {!writable && (
        <p className="muted config-note">
          This page was served no configuration revision, which both of this
          card&apos;s writes are checked against, so reload the page before
          saving.
        </p>
      )}

      {confirming && (
        <p className="config-confirm">
          {`Removing ${label} drops its configuration and clears its stored credential.`}
          <button
            type="button"
            disabled={busy}
            onClick={() =>
              void run(async () => {
                const removed = await removeServer(server.name, {
                  expected_revision: revision ?? "",
                  confirm: true,
                });
                setConfirming(false);
                await onChanged();
                return removed.credential_cleared
                  ? `Removed ${label}.`
                  : `Removed ${label}, but its credential could not be cleared and is still stored. Open this card again from Add a server to clear it.`;
              })
            }
          >
            {`Yes, remove ${label}`}
          </button>
          <button type="button" onClick={() => setConfirming(false)}>
            Cancel
          </button>
        </p>
      )}
    </section>
  );
}
