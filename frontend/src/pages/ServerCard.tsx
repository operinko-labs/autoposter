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
 * THE SWITCHES ARE TICK-BOXES OVER VALUES THE PAGE ALREADY HOLDS. Each one is
 * a leaf of the configuration document, so the page reads it by its dotted
 * path and hands it in; the box shows that value, and a save sends back only
 * the paths whose box was moved -- `switches` is a delta over the stored
 * block, not a replacement of it, so a card that sent every box would write
 * settings nobody touched.
 *
 * WHAT THE PROPS SAY WINS, EXCEPT WHERE AN EDIT IS IN FLIGHT. The three fields
 * seeded from props -- the address, the exclusions and the switches -- are
 * re-seeded when the page re-reads, so a change made somewhere else is not
 * quietly reverted by this card's next save (`excluded_libraries` is written
 * as a REPLACEMENT, and the revision this card sends is the fresh one, so
 * nothing else would catch it). An edit in flight is left alone: a re-read
 * that emptied the field being typed into is the worse failure.
 *
 * NO SECRET IS EVER READ BACK. The credential field holds a typed value for
 * exactly as long as the request that carries it, and is emptied when that
 * request ends, refused or not.
 *
 * A WRITE IS REFUSED WHILE THE PAGE HOLDS AN UNSAVED EDIT. The card takes no
 * part in that edit, but the re-read every one of its writes owes the page
 * re-seeds the editor from the server, which would throw the operator's typing
 * away with nothing on screen to say so. The three buttons that re-seed the
 * page are disabled and the shared sentence says why; the reads beside them,
 * and the catch-up buttons -- which re-read this tab's own listing and nothing
 * else -- are left alone, because none of them touches the stored document.
 */
import { useEffect, useRef, useState } from "react";

import { refusalMessage, RESTART_NOTE } from "../api/overrides";
import {
  cancelCatchUp,
  catchUpRun,
  checkServer,
  clearServerCredential,
  fetchCatchUp,
  fetchLibraries,
  removeServer,
  restartWaiting,
  retryFailed,
  saveServer,
  setServerCredential,
  startCatchUp,
  type CatchUpProgress,
  type CheckResult,
  type LibraryRow,
  type ServerRemovalResult,
  type ServerRow,
} from "../api/servers";
import { formatTime } from "../format";
import { PENDING_EDITS_NOTE } from "./RestartBanner";

/** What each server is called on screen. Exported because the tab titles this
 * card's accordion with it: two copies would be two names for one server, one
 * of them on the header and the other inside every field label under it. */
export const LABELS: Record<string, string> = { plex: "Plex", jellyfin: "Jellyfin" };

/** The switches this card owns, per server, matching `api/servers.py`'s
 * `SERVER_SWITCHES` exactly. A path that table does not name is a 422 naming
 * the key, which is a refusal an operator cannot act on, so the two tables say
 * the same thing.
 *
 * Exported because the tab reads these paths out of the served configuration
 * and hands their values back here. Derived there rather than listed again: a
 * switch added below and forgotten in a second list would show its box off
 * while the document said on, and this card is the only place that setting
 * appears on that tab. */
export const SWITCHES: Record<string, { path: string; label: string }[]> = {
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

/** What a run's stored status word means, said the way an operator would say
 * it.
 *
 * The four values are the run table's own (`scheduler/run_history.py` opens a
 * row as `running` and closes an abandoned one as `interrupted`;
 * `catchup.py` closes its own as `ok` or `cancelled`), and they are written
 * for the table rather than for a card: "Catch up ok" reads as a state nobody
 * would call a finished backlog, and "interrupted" says nothing about what
 * interrupted it. A word this table does not carry is printed as it arrived
 * rather than guessed at. */
const RUN_WORDS: Record<string, string> = {
  running: "still running",
  ok: "finished",
  cancelled: "cancelled",
  interrupted: "stopped when this deployment restarted",
};

/** How often an open run's counts are read again, in milliseconds. Slow on
 * purpose: this is a backlog measured in hours, and the card is one of several
 * on a page an operator leaves open. */
const PROGRESS_INTERVAL = 10_000;

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

/** Two lists or two switch tables holding the same thing.
 *
 * Compared by value rather than by identity because both arrive as fresh
 * objects on every re-read: identity would report every re-read as a change
 * and every card as edited. */
function sameList(one: string[], other: string[]): boolean {
  return one.length === other.length && one.every((name, at) => name === other[at]);
}

function sameSwitches(
  one: Record<string, boolean>,
  other: Record<string, boolean>,
): boolean {
  const keys = Object.keys(one);
  return (
    keys.length === Object.keys(other).length &&
    keys.every((path) => one[path] === other[path])
  );
}

export function ServerCard({
  server,
  switches,
  revision,
  pendingEdits = false,
  onChanged,
  onRemoved,
}: {
  server: ServerRow;
  /** What each of this server's switches is set to now, keyed by the dotted
   * path -- the page reads them out of the served configuration, because they
   * are ordinary settings that happen to be about one server. The card shows
   * these and sends back only what was moved. */
  switches: Record<string, boolean>;
  /** The configuration revision the page was served. Required by both of this
   * card's writes, so a page that was served none cannot save: the routes
   * refuse a body without one, and that refusal is the only thing standing
   * between a concurrent settings save and a silent revert of it. */
  revision: string | null;
  /** Whether the page is holding an edit nobody has stored. Every control
   * below whose success re-seeds the page is refused while it is true. */
  pendingEdits?: boolean;
  /** Re-read what this card is rendered from. `page: false` asks for the
   * tab's listing alone, which is what the catch-up buttons need: they change
   * no setting, and the page's own re-read would discard an unsaved edit. */
  onChanged: (options?: { page?: boolean }) => Promise<void> | void;
  /** What a removal answered, handed to the tab. The removal takes this card
   * off the screen, so a sentence about it belongs to something that outlives
   * it -- and one of the two sentences is the only record an operator gets
   * that a credential for a server this deployment no longer has is still in
   * the store. */
  onRemoved: (result: ServerRemovalResult) => void;
}) {
  const label = LABELS[server.name] ?? server.name;
  const [address, setAddress] = useState(server.url ?? "");
  const [credential, setCredential] = useState("");
  const [libraries, setLibraries] = useState<LibraryRow[] | null>(null);
  const [excluded, setExcluded] = useState<string[]>(server.excluded_libraries);
  const [chosen, setChosen] = useState<Record<string, boolean>>(switches);
  const [probe, setProbe] = useState<CheckResult | null>(null);
  const [progress, setProgress] = useState<CatchUpProgress | null>(null);
  const [confirming, setConfirming] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [note, setNote] = useState<string | null>(null);

  /** What the three seeded fields were last seeded FROM.
   *
   * The baseline for two questions at once: whether the props have moved, and
   * whether this card is holding an edit. A ref rather than state because
   * changing it is never a reason to render -- what renders is the seeding
   * itself. */
  const seeded = useRef({
    address: server.url ?? "",
    excluded: server.excluded_libraries,
    switches,
  });

  // Re-seed the three fields the props own when the page re-reads.
  //
  // The card is a long-lived child of the tab and its props are replaced on
  // every re-read, including re-reads it did not cause -- another card's
  // write, a settings save, a manual refresh. Seeding once at mount left it
  // holding the list it was mounted with, and `excluded_libraries` is written
  // as a REPLACEMENT, so the next save here would put that stale list back
  // over somebody else's change with a 200 and no mention of it. The revision
  // does not catch it: this card sends the fresh one it was just handed.
  //
  // An edit in flight is the one thing that wins over the re-read, because
  // taking away what the operator is in the middle of typing is worse than
  // showing a value one re-read old -- and a save makes what it sent the new
  // baseline, so the re-read that follows it reconciles rather than counting
  // as a conflict.
  useEffect(() => {
    const seed = seeded.current;
    const fresh = {
      address: server.url ?? "",
      excluded: server.excluded_libraries,
      switches,
    };
    if (
      fresh.address === seed.address &&
      sameList(fresh.excluded, seed.excluded) &&
      sameSwitches(fresh.switches, seed.switches)
    ) {
      return;
    }
    if (
      address !== seed.address ||
      !sameList(excluded, seed.excluded) ||
      !sameSwitches(chosen, seed.switches)
    ) {
      return;
    }
    seeded.current = fresh;
    setAddress(fresh.address);
    setExcluded(fresh.excluded);
    setChosen(fresh.switches);
    // The three fields are dependencies as well as props: a re-read that
    // arrived while something was half-typed is reconciled as soon as the
    // card stops holding that edit, rather than waiting for the next re-read.
    // The first guard is what makes the extra passes free.
  }, [server, switches, address, excluded, chosen]);

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

  const catchUp = catchUpRun(progress);
  /** A run nothing has closed yet. The one state in which the counts on
   * screen go stale by themselves, and the one in which starting another is
   * refused. */
  const runOpen = catchUp !== null && catchUp.finished_at === null;

  // While a run is open, read its counts again on a slow interval: they are
  // the only thing on this card that moves without anybody pressing
  // anything, and a line frozen at the counts the card was opened with reads
  // as a run that has stopped making progress. It stops when the run closes
  // and when the card goes -- an accordion collapsed over an open run must
  // not leave a timer reading a server behind it.
  useEffect(() => {
    if (!runOpen) return;
    let cancelled = false;
    const timer = setInterval(() => {
      fetchCatchUp(server.name)
        .then((found) => {
          if (!cancelled) setProgress(found);
        })
        .catch(() => {});
    }, PROGRESS_INTERVAL);
    return () => {
      cancelled = true;
      clearInterval(timer);
    };
  }, [runOpen, server.name]);

  /** The counts again, after a press that may have changed them.
   *
   * Never this card's own failure: a progress read that failed after an
   * action that landed would be rendered as that action's refusal and take
   * its note with it -- the rule the tab keeps for the re-read it owes the
   * page. The line keeps the counts it had, which is one read old rather than
   * wrong. */
  async function refreshProgress() {
    try {
      setProgress(await fetchCatchUp(server.name));
    } catch {
      // Deliberately silent; see above.
    }
  }

  /** Whether the address in the field is one the request would have to name.
   *
   * The stored address is what an empty body asks for, and it is the only
   * address this deployment will send a credential it holds to. Anything else
   * has to travel in the request, and then the credential to use against it
   * has to travel with it. */
  const typedAddress = address !== (server.url ?? "");

  /** The typed-address rule, client side: an address this request names must
   * carry the credential to use against it, and a card with nothing typed
   * sends neither field and gets the booted address and the held credential.
   * Sending both or neither is the only shape this card produces, which is
   * why the two buttons below are refused while the field pair says neither
   * -- an address with an empty credential is a 400 every time. */
  function probeBody() {
    if (typedAddress || credential !== "") {
      return { url: address, credential_value: credential };
    }
    return {};
  }

  /** Why neither probe button may be pressed, or null when both may.
   *
   * The three states `_resolve_target` refuses outright (`api/servers.py`),
   * each with the sentence that names the half that is missing -- an operator
   * sent to the credential field for a missing address fixes the wrong thing.
   * Offering a press that is a 400 by construction is the rule this card set
   * for itself with the first of the three; the other two are as reachable,
   * and the second of them is the state the tab OPENS a card in.
   */
  const cannotProbe: string | null =
    typedAddress && credential === ""
      ? "An address typed here must come with the credential to use against " +
        "it: this deployment does not send a credential it holds to an " +
        "address a request names."
      : address === ""
        ? "There is no address to check. Type this server's address above " +
          "first; a check with none is refused."
        : !typedAddress && credential === "" && server.credential_source === "unset"
          ? `This deployment holds no credential for ${label}, and a check ` +
            "needs one. Store it above, or type it in beside the address to " +
            "check that pair without storing it."
          : null;

  /** What this save STATES about the switches: the boxes that were moved, and
   * nothing else. The route merges this over the stored block, so a path sent
   * for a switch nobody touched is a write nobody asked for -- and these are
   * ordinary settings, editable on their own tabs too, so the one this card
   * did not mean to state is the one somebody else had just changed.
   *
   * Keyed from this server's own table rather than from the prop, so a path
   * the route does not accept for this server cannot be sent even if the page
   * hands one in. */
  function switchDelta(): Record<string, boolean> {
    const delta: Record<string, boolean> = {};
    for (const entry of SWITCHES[server.name] ?? []) {
      const now = chosen[entry.path] ?? false;
      if (now !== (switches[entry.path] ?? false)) delta[entry.path] = now;
    }
    return delta;
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
  const detail = probe === null ? server.health.detail : probe.detail;
  const writable = revision !== null;
  /** Whether a write is allowed at all: the revision this card sends, and the
   * page not holding an edit the re-read after a write would discard. */
  const canWrite = writable && !pendingEdits;

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
          aria-label={`Save ${label} credential`}
          disabled={busy || credential === "" || pendingEdits}
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
            aria-label={`Clear ${label} credential`}
            disabled={busy || pendingEdits}
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
          onChange={(event) => {
            setAddress(event.target.value);
            // The pill is a state, and the state it held was about the
            // address that was there a moment ago. Dropping the probe hands
            // the pill back to the listing's own health, which is the only
            // thing that still knows something about an address.
            setProbe(null);
          }}
        />
      </span>
    </div>
  );

  return (
    // Not a panel of its own, and no class of its own: this card sits inside
    // the Servers tab's accordion, which is the panel, and a second bordered
    // box inside it would be a fourth level of visual nesting where the page
    // allows three. The one thing that needed laying out is the pill row.
    // Named with the server it is about, so the buttons inside it are told
    // apart by the region that carries them: two cards are open together in
    // exactly the states this tab is built for, and "Save" beside "Save" with
    // nothing between them is two controls with one name.
    <section aria-label={label}>
      {/* The pills, and no heading of its own: the accordion this card sits
          inside is titled with the server's name, so a heading here would name
          the same server a second time one line below the first. */}
      <p className="server-card-pills">
        <span className={pill.className}>{pill.text}</span>
        {server.restart_pending && (
          <span
            className="config-pill restart"
            title="This server is saved, and this deployment is still running on what it started with."
          >
            restart to apply
          </span>
        )}
      </p>
      {error !== null && <p className="page-error">{error}</p>}
      {note !== null && <p className="config-saved">{note}</p>}
      {/* One line for every control it refuses, in the wording the two other
          re-reading panels use: three sentences for one rule is how an
          operator learns it as three rules. */}
      {pendingEdits && <p className="muted config-note">{PENDING_EDITS_NOTE}</p>}
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
      {cannotProbe !== null && (
        <p className="muted config-note">{cannotProbe}</p>
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

      {(SWITCHES[server.name] ?? []).length > 0 && (
        <fieldset className="server-switches">
          <legend>{`${label} settings`}</legend>
          {(SWITCHES[server.name] ?? []).map((entry) => (
            <label key={entry.path}>
              <input
                type="checkbox"
                aria-label={entry.label}
                checked={chosen[entry.path] ?? false}
                onChange={(event) =>
                  setChosen((current) => ({
                    ...current,
                    [entry.path]: event.target.checked,
                  }))
                }
              />
              {entry.label}
            </label>
          ))}
        </fieldset>
      )}

      {progress !== null && (
        <p className="muted">
          {catchUp === null
            ? `No catch-up has run for ${label} yet.`
            : `Catch up ${RUN_WORDS[catchUp.status] ?? catchUp.status}: ` +
              `${catchUp.due} due, ${catchUp.done} done, ${catchUp.failed} failed, ` +
              `of ${catchUp.total}.` +
              // The run's own summary, which the server writes when it closes
              // one: it is what the cancel button shows for a cancel made on
              // this screen, and without this it was lost the moment the card
              // was re-opened.
              (catchUp.detail === null ? "" : ` ${catchUp.detail}`)}
        </p>
      )}

      <div className="config-actions">
        <button
          type="button"
          aria-label={`Check ${label} connection`}
          disabled={busy || cannotProbe !== null}
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
          aria-label={`Reload ${label} libraries`}
          disabled={busy || cannotProbe !== null}
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
        {/* Not offered over a run nothing has closed: the service takes one
            lock per server and refuses a second start in so many words, so
            this press beside the Cancel button below is a guaranteed refusal
            -- and the operator who pressed it was asking for the counts to
            move, which they now do on their own. */}
        {server.configured && !runOpen && (
          <button
            type="button"
            aria-label={`Catch ${label} up`}
            disabled={busy}
            onClick={() =>
              void run(async () => {
                await startCatchUp(server.name);
                // The counts come from the read, which is the one place they
                // are computed; starting one answers the run and its cadence.
                await refreshProgress();
                // The listing carries this server's health and its restart
                // pill and nothing else moved, so the tab's own read is all
                // that is asked for: the page's would re-seed the editor from
                // the server and take an unsaved edit with it.
                await onChanged({ page: false });
                return null;
              })
            }
          >
            Catch up
          </button>
        )}
        {runOpen && (
          <button
            type="button"
            aria-label={`Cancel ${label} catch-up`}
            disabled={busy}
            onClick={() =>
              void run(async () => {
                const cancelled = await cancelCatchUp(server.name);
                await refreshProgress();
                await onChanged({ page: false });
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
            aria-label={`Retry ${label}'s failed deliveries`}
            disabled={busy}
            onClick={() =>
              void run(async () => {
                const retried = await retryFailed(server.name);
                // Re-armed rows are rows a run is owed, so the counts on the
                // line above are the ones this press just changed.
                await refreshProgress();
                await onChanged({ page: false });
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
            aria-label={`Remove ${label}`}
            disabled={busy || !canWrite}
            onClick={() => setConfirming(true)}
          >
            Remove server
          </button>
        )}
        <button
          type="button"
          aria-label={`Save ${label}`}
          disabled={busy || !canWrite}
          onClick={() =>
            void run(async () => {
              // Narrowed rather than defaulted: `""` is a string the route
              // accepts and then refuses as a stale revision, which is a
              // worse answer than the disabled button above.
              if (revision === null) return null;
              const exclusionsMoved = !sameList(
                excluded,
                server.excluded_libraries,
              );
              const saved = await saveServer(server.name, {
                url: address,
                excluded_libraries: excluded,
                switches: switchDelta(),
                expected_revision: revision,
                confirm: false,
              });
              // What was sent is the new baseline, so the re-read below
              // reconciles this card rather than reading as a conflict with
              // an edit in flight -- and the boxes keep the state they were
              // saved in instead of blanking until the props catch up.
              seeded.current = { address, excluded, switches: chosen };
              // The address that was just saved is not the one the probe
              // reached, so the pill goes back to what the listing knows.
              setProbe(null);
              await onChanged();
              const said = [
                `Saved ${label}. This deployment is still running on the ` +
                  "address it started with, so a connection check is refused " +
                  "until it has restarted, unless the address and the " +
                  "credential to use against it are typed in above.",
                // Which libraries a server is expected to carry decides which
                // items it is owed, so the service starts the backlog itself
                // -- and the operator who is not told goes looking for the
                // button that started it.
                ...(exclusionsMoved
                  ? [`Changing the managed libraries also starts a catch-up for ${label}.`]
                  : []),
                // The response's own list, not a fixed sentence: it is what
                // this write left waiting, and a save that left nothing
                // waiting must not claim otherwise.
                ...(restartWaiting(saved.restart_required) ? [RESTART_NOTE] : []),
              ];
              return said.join(" ");
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
            disabled={busy || !canWrite}
            onClick={() =>
              void run(async () => {
                // Narrowed for the reason the save is: an empty revision is a
                // request the route takes and then refuses.
                if (revision === null) return null;
                const removed = await removeServer(server.name, {
                  expected_revision: revision,
                  confirm: true,
                });
                setConfirming(false);
                // Handed up BEFORE the re-read, and rendered by the tab: the
                // listing that re-read brings back has this server
                // unconfigured, so the accordion and this card are gone
                // before a sentence written here could be read.
                onRemoved(removed);
                await onChanged();
                return null;
              })
            }
          >
            {`Yes, remove ${label}`}
          </button>
          <button
            type="button"
            aria-label={`Keep ${label}`}
            onClick={() => setConfirming(false)}
          >
            Cancel
          </button>
        </p>
      )}
    </section>
  );
}
