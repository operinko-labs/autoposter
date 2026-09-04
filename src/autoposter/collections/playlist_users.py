"""Copy a managed playlist into other people's Plex accounts, by diff.

98a answered "is this playlist ours" with a ``managed_playlists`` row naming a
rating key. This module asks the same question one level down, and everything
new here follows from one fact: **a user's copy is a separate playlist object
in a separate account with a separate rating key.** ``Playlist.create`` POSTs
``/playlists`` on whichever server session it is handed and returns a brand-new
object; ``server.playlists()`` lists only what that session's token can see. So
the owner's listing contains none of the copies, each user's listing contains
only their own, and ownership is a ``managed_playlist_users`` row per
(definition, user) naming THAT user's rating key, looked up in THAT user's
listing.

What is NOT new: the diff. A member's rating key is the server's metadata id --
identical for every account, because ``Playlist._create`` builds its uri from
``server._uriRoot()`` and that names the same machine either way. So
``lists.member_diff`` compares an admin-fetched desired list against a
user-fetched membership correctly, unchanged, and this module imports it rather
than growing a second subtraction. Kometa's per-user delete-and-recreate is not
ported here for the reason it was not ported one level up.

## The credential, and the one rule about it

This is the only module in this service where a *foreign* account's token is
ever a value. Three things follow, and they are why this is its own file:

- **We drive the chain ourselves.** ``Playlist.copyToUser`` and
  ``PlexServer.switchUser`` are never called. ``MyPlexUser.get_token`` swallows
  every exception and returns ``None`` (pinned in
  ``tests/test_plexapi_user_switch_contract.py``), and ``PlexServer.__init__``
  then reads ``auth.server_token`` out of *plexapi's own config file* -- so a
  write would be issued under the wrong identity, or anonymously. An empty
  token is therefore an explicit REFUSAL with a named report line, never a
  value passed on.
- **A token never travels.** What crosses a function boundary is
  ``UserTarget.connect``, a zero-argument callable closed over the token --
  ``SourceClients.plex_account``'s shape one level down, and for its stated
  reason: a dataclass field holding a string is one f-string away from every
  log line and every exception context. The token is a local of ``_mint`` and
  of the closure that captured it; it is never persisted, never returned and
  never logged.
- **A served string is class-name-only, and redacted anyway.**
  ``collections/playlists.py``'s own handler explains the first half: a plexapi
  failure's message commonly carries the base URL, and these strings reach
  ``scheduled_runs.last_detail``, ``/api/snapshots`` and notifications. A
  user's display TITLE is safe -- it is a name, not a credential -- and it is
  what makes the report usable. The second half is roadmap row 207's closed
  ruling: **the pod log is the trusted sink and every SERVED surface carries
  redacted text.** So every reported line and every ``reason`` this module
  produces goes through ``redact.redact_urls`` at the seam that builds it
  (``UserSyncPlan.report``/``.preview``, ``PlaylistUserResult.__post_init__``,
  ``_action`` in the applier) rather than at each of a dozen call sites. That
  helper is the one ``api/playlists.py`` already applies to this exact
  response; it lives in one module rather than three copies. Nothing here is
  expected to *need* it -- every value interpolated is a title, a count or a
  class name -- which is the point: a rule with an exemption is a rule
  somebody widens.
- **The traceback is allowed to be whole.** The four ``logger.exception``
  calls below set ``exc_info`` deliberately. Row 207 accepts the unscrubbed
  stdout copy by name, ``/api/logs`` already scrubs what it serves at the
  ``LogBuffer`` seam, and a contained per-user failure with no traceback is an
  incident nobody can diagnose. This module installs no logging filter of its
  own.

## Why the whole fan-out is planned before any of it is applied

The caps (``playlists.max_users``, ``playlists.max_user_writes``) refuse
ENTIRELY rather than spend a budget: writing "the first fifty" of four hundred
would be the same accident spread over eight passes -- ``cleanup.max_orphans``'
rule, and the delete sweep's own one level up. Refusing entirely is only
possible if the plan exists before the first write, which is also what makes
the dry-run counts exact rather than an estimate of what a real pass might do.

And "before the first write" is not enough on its own, which is why
``plan_user_sync`` runs in TWO passes. Resolving who a definition reaches is
pure over the cached ``account.users()`` list; opening a session and reading a
listing is a token mint plus a PMS GET *per user*. A single loop that did both
and checked the caps afterwards would refuse a two-hundred-account fan-out
having already spent two hundred mints and two hundred listings -- no write,
and most of the cost the caps were written against. So the first pass resolves
every definition's targets and evaluates both caps -- ``max_users`` exactly,
``max_user_writes`` from the stored rows -- and only a fan-out that survives
that opens anything. The second pass then takes the exact write count from the
listings and evaluates the write cap again, because a stale row's removals
cannot be counted before its owner's membership is read.

## What this module does NOT do, named rather than left to be discovered

- **A user's copy is never reordered.** The admin path calls
  ``lists._enforce_order`` under ``sync_mode: sync``; this one does not.
  ``_enforce_order``'s cost is knowable only by reloading and walking each
  user's copy -- a read per user per pass that the hash gate exists to avoid --
  and its write count is unbounded, up to one PUT per member: on seventeen
  accounts and a hundred members that is the ~1,700-request pass the caps exist
  to prevent, and a cost that cannot be planned cannot be refused before it is
  spent. A copy is CREATED in the definition's order and ``addItems`` appends,
  so it is correct at creation and drifts only by later additions. Roadmap row
  98's ledger records this.
- **Per-library access is not pre-checked.** ``MyPlexServerShare.allLibraries``
  and ``Section.shared`` are pinned in the contract file, but reading them
  costs one plex.tv GET per restricted user per pass and nothing here depends
  on the answer: a create naming rating keys a token cannot see fails, and the
  failure is contained per user and reported by name. The pins exist so the
  slice that adds the pre-check does not have to rediscover the shape.
"""
import logging
from collections.abc import Callable
from dataclasses import dataclass, field

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from autoposter.collections.lists import member_diff
from autoposter.db.models import ManagedPlaylistUser
from autoposter.redact import redact_urls

logger = logging.getLogger(__name__)


def connect_as_user(baseurl: str, token: str):
    """One ``PlexServer`` bound to one user's token.

    plexapi is imported inside the function so importing this module costs no
    plexapi import -- ``service._plex_account_factory`` defers its own for the
    same reason.

    The emptiness check is not defensive noise about an impossible state: it is
    the second lock on the documented footgun. ``PlexServer.__init__`` reads
    ``auth.server_token`` out of plexapi's config file when handed a falsy
    token, so without this "no token" could become "somebody else's token"
    silently. Callers refuse before reaching here; this refuses if one ever
    stops.
    """
    if not token:
        raise ValueError("refusing to open a Plex session without a user token")
    from plexapi.server import PlexServer

    return PlexServer(baseurl, token=token)


@dataclass(frozen=True)
class UserTarget:
    """One user this pass may write to, and how to reach their server.

    ``connect`` is a zero-argument callable closed over that user's token.
    There is deliberately **no token field**: this is a dataclass, so
    ``repr()`` of one reaches every log line and every exception context that
    formats it, and ``SourceClients`` holds a factory rather than an account
    for exactly this reason one level up.
    """

    user_id: int
    title: str
    connect: Callable[[], object]


@dataclass(frozen=True)
class UserSkip:
    """A named target this pass will not write to, and why. Report only."""

    title: str
    reason: str


@dataclass
class PlaylistUserResult:
    """What one pass did, or would do, to one user's copy of one playlist.

    ``creating``/``adding``/``removing`` are PREVIEW counts, ``deleting`` is
    the sweep's (one user copy is one deletion, so it is 0 or 1), and
    ``added``/``removed`` are what was applied -- ``PlaylistResult``'s split,
    for its reason. ``title`` is the user's display title, which is a name and
    not a credential, and is what makes the report readable.

    ``deleting`` is written in exactly one place, ``_sweep_playlists``' user
    branch, which attaches one of these to the ``_swept`` result for the copy
    it would delete or did delete. It is served and it is pinned; it is not a
    field left at zero "ready for" a later slice, which Global Constraint 16
    forbids.
    """

    title: str
    user_id: int | None = None
    creating: int = 0
    adding: int = 0
    removing: int = 0
    deleting: int = 0
    added: int = 0
    removed: int = 0
    skipped: bool = False
    failed: bool = False
    reason: str | None = None

    def __post_init__(self):
        # THE seam for every reason this module serves. One place, so the rule
        # is checkable by reading one method rather than a dozen call sites --
        # and so a reason added later inherits it instead of being remembered
        # about. Row 207: served surfaces carry redacted text.
        if self.reason is not None:
            self.reason = redact_urls(self.reason)


class UserSync:
    """Everything the per-user stage knows for ONE pass.

    Built once per pass and only when a definition actually asks for a
    fan-out, because constructing it costs plex.tv round trips a deployment
    that syncs to nobody must not pay. Three caches live here and nowhere
    else, all keyed for the pass's lifetime:

    - ``account.users()``, because plexapi caches nothing there and every
      definition asks the same question;
    - one connected ``PlexServer`` per user, because minting a token and
      opening a session is two to three round trips;
    - one playlist listing per user, because that listing IS the per-user
      ownership map and every definition consults it.

    ``connect`` is injected rather than imported at the call site so a test
    can drive the real pass without plexapi and, more usefully, assert that a
    connection was **not** made. It defaults to ``connect_as_user``.
    """

    def __init__(self, account, server, config, connect=connect_as_user):
        self._account = account
        self._server = server
        self._config = config
        self._connect = connect
        self._users: list | None = None
        # user_id -> (server | None, reason | None)
        self._servers: dict[int, tuple[object | None, str | None]] = {}
        # user_id -> {rating key: playlist} | None
        self._listings: dict[int, dict | None] = {}

    def users(self) -> list:
        """``account.users()``, once. The owner is never in it."""
        if self._users is None:
            self._users = list(self._account.users())
        return self._users

    def owner_refusal(self) -> str | None:
        """``None`` to proceed, or the sentence that stops the whole stage.

        C13 A9, and it runs before the first write rather than as a 401
        discovered partway through a fan-out that has already written to
        somebody. One plex.tv GET: ``account.resources()`` carries
        ``clientIdentifier`` and ``owned``, and only the owner may mint another
        account's token at all.
        """
        machine = str(getattr(self._server, "machineIdentifier", "") or "")
        for resource in self._account.resources():
            if str(getattr(resource, "clientIdentifier", "") or "") == machine:
                if getattr(resource, "owned", False):
                    return None
                return (
                    "refusing the user fan-out: the configured plex.tv account "
                    "token belongs to somebody who is not this server's owner, "
                    "and only the owner may mint another account's token. "
                    "Nothing was written to any user and the admin playlists "
                    "were reconciled"
                )
        return (
            "refusing the user fan-out: the configured plex.tv account token "
            "sees no server matching this one, so it is not this server's "
            "owner's. Nothing was written to any user and the admin playlists "
            "were reconciled"
        )

    def targets_for(self, definition) -> tuple[list[UserTarget], list[UserSkip]]:
        """Who this definition is copied to, and who it is not, and why not.

        Pure over the cached user list and the section's ``exclude_users``: no
        plex.tv call happens here at all, which is what makes calling it again
        from the sweep free. The two refusals knowable without a request -- a
        name nobody answers to, and ``protected`` -- are made here so they cost
        nothing; the two that are not (no token, a connection that failed) are
        made by ``server_for`` when the plan first needs that user's listing.
        """
        excluded = {
            name.casefold() for name in self._config.playlists.exclude_users
        }
        owner = {
            (getattr(self._account, "title", "") or "").casefold(),
            (getattr(self._account, "username", "") or "").casefold(),
        } - {""}
        targets: list[UserTarget] = []
        skips: list[UserSkip] = []

        if definition.sync_to_users == "all":
            chosen = [
                user for user in self.users()
                if (user.title or "").casefold() not in excluded
            ]
        else:
            by_title = {(u.title or "").casefold(): u for u in self.users()}
            chosen = []
            # A ``seen`` set of casefolded keys, not ``dict.fromkeys`` on the
            # raw names: two names differing only in case (``"alice"`` and
            # ``"Alice"``) resolve to the same user and must collapse to one
            # target, while the ORDER an operator wrote is what the report
            # reads in.
            seen: set[str] = set()
            for name in definition.sync_to_users or []:
                key = name.casefold()
                if key in seen:
                    continue
                seen.add(key)
                if key in excluded:
                    skips.append(
                        UserSkip(name, "excluded by playlists.exclude_users")
                    )
                elif key in owner:
                    # account.users() never contains the owner, so without this
                    # branch naming them reads as "no such user" -- true and
                    # unhelpful. Kometa reaches switchUser(admin) here and is
                    # non-destructive only because the NotFound it raises is
                    # swallowed, which is precisely the shape 98a refused.
                    skips.append(UserSkip(
                        name,
                        "the server owner is not a sync target; the admin "
                        "playlist IS this definition",
                    ))
                elif key not in by_title:
                    skips.append(UserSkip(name, "no such user on this account"))
                else:
                    chosen.append(by_title[key])

        for user in chosen:
            refusal = self._refusal_for(user)
            if refusal is not None:
                skips.append(refusal)
                continue
            targets.append(UserTarget(user.id, user.title, self._factory_for(user)))
        return targets, skips

    def target_for_id(self, user_id: int) -> UserTarget | None:
        """The target a stored row names, or None if it is unreachable.

        Unreachable means the account no longer lists that user at all, or
        lists them as ``protected``. Either way there is nothing this pass may
        do about their copy, and the sweep reports the row rather than treating
        it as a candidate.
        """
        for user in self.users():
            if user.id == user_id:
                if self._refusal_for(user) is not None:
                    return None
                return UserTarget(user.id, user.title, self._factory_for(user))
        return None

    def server_for(self, target: UserTarget) -> tuple[object | None, str | None]:
        """``(server, reason)`` for one user, cached for the pass.

        The reason is non-None exactly when the server is None, and it is the
        second half of that user's report line. Both failure shapes are named
        rather than merged, because "plex.tv would not mint a token" and "the
        server would not accept it" send an operator to different places.
        """
        if target.user_id not in self._servers:
            server = None
            reason = None
            try:
                server = target.connect()
            except Exception as error:
                # The class name only: a plexapi failure's message carries the
                # base URL and in some shapes the token, and this string is
                # served. The traceback goes to the logger.
                logger.exception(
                    "could not open a Plex session as the user %r", target.title
                )
                reason = "connecting to Plex as this user failed (%s)" % (
                    type(error).__name__
                )
            if server is None and reason is None:
                reason = "plex.tv returned no access token for this user"
            self._servers[target.user_id] = (server, reason)
        return self._servers[target.user_id]

    def playlists_for(self, target: UserTarget) -> dict | None:
        """That user's own playlists by rating key, or None if unreachable.

        THE per-user ownership map, and the only place it is read. One PMS GET
        per user per pass, which is what the hash gate is protecting: an
        unchanged copy costs this listing and no membership read at all.
        """
        if target.user_id not in self._listings:
            server, _reason = self.server_for(target)
            self._listings[target.user_id] = (
                None if server is None
                else {str(p.ratingKey): p for p in server.playlists()}
            )
        return self._listings[target.user_id]

    def _refusal_for(self, user) -> UserSkip | None:
        """The refusal knowable from the user row alone, or None."""
        if getattr(user, "protected", False):
            return UserSkip(
                user.title, "PIN-protected; this service never holds a Plex PIN"
            )
        return None

    def _factory_for(self, user) -> Callable[[], object]:
        """A zero-argument connector for one user. The token lives in here.

        Minted on first call, not now: a definition whose plan never needs this
        user's listing must not cost a plex.tv round trip. What the caller
        receives is this closure; the token is a local of ``_mint`` and of this
        frame, and there is no attribute anywhere holding it.
        """
        baseurl = self._server._baseurl
        connect = self._connect
        mint = self._mint

        def open_server():
            token = mint(user)
            if not token:
                return None
            return connect(baseurl, token)

        return open_server

    def _mint(self, user) -> str:
        """This user's token, or ``""`` -- which is a REFUSAL, not a default.

        Ordered by cost rather than by the ``home`` flag. ``get_token`` is ONE
        plex.tv GET and the operator's read-only probe found it answers for
        their Home users as well as their shared ones, while
        ``switchHomeUser`` costs two: the switch POST plus the
        ``/api/v2/user`` GET inside ``MyPlexAccount.__init__``, whose
        ``_signin`` short-circuits to a query whenever a token is present. So
        the share token is tried for everybody and the Home switch is the
        fallback for a ``home`` user plex.tv had no share entry for.

        Every failure returns ``""``. plexapi's ``get_token`` swallows its own
        exceptions already; the handlers here exist because a swallowed
        failure and a genuine absence must arrive at the same refusal, and
        because nothing in this function may re-raise into the middle of a
        fan-out.
        """
        token = ""
        try:
            token = user.get_token(self._server.machineIdentifier) or ""
        except Exception:
            logger.exception(
                "could not read the share token for the user %r", user.title
            )
            token = ""
        if token or not getattr(user, "home", False):
            return token
        try:
            switched = self._account.switchHomeUser(user)
        except Exception:
            logger.exception(
                "could not switch to the Home user %r", user.title
            )
            return ""
        return getattr(switched, "authenticationToken", "") or ""


@dataclass
class _Planned:
    """One (definition, user) pair this pass intends to act on.

    ``result`` is None for a pair that needs no write: the hash gate covered
    it, or the hash moved for a reason this pass does not act on (an order
    change, which it does not enforce). Either way the row is still stamped, so
    ``last_reconciled_at`` reads as "this copy was confirmed" rather than "this
    copy last changed", and nothing is reported -- the admin pass's own gated
    branch appends no action for the same reason.

    ``server`` is excluded from the repr deliberately: a real ``PlexServer``
    holds ``_token``, and this dataclass's repr reaches every log line and
    exception context that formats a plan.
    """

    definition_title: str
    target: UserTarget
    server: object = field(repr=False)
    row: ManagedPlaylistUser | None
    playlist: object | None
    items: list
    summary: str | None
    sync_mode: str
    wanted_hash: str
    adding: list = field(default_factory=list)
    removing: list = field(default_factory=list)
    writes: int = 0
    result: PlaylistUserResult | None = None


@dataclass
class UserSyncPlan:
    """Everything the fan-out would do, before any of it is done.

    ``results`` is keyed by definition title and holds the per-user rows the
    preview response serves. ``targets`` is the distinct fan-out width the
    ``max_users`` cap is measured against and ``writes`` is the total the
    ``max_user_writes`` cap is measured against. ``refusal``, when set, is the
    one line that replaces all of it.

    **``actions`` and ``previews`` are two lists on purpose.** ``actions``
    holds what is true whatever the gate says -- every skip, by name. It is
    reported on every pass. ``previews`` holds the "would create"/"would
    update" lines, which are reported only when the pass is NOT going to write:
    a pass that applies reports what it did, and emitting both would tell an
    operator the same event twice in two tenses. The admin half gets the same
    effect from its ``if dry_run:`` early return, which this stage has no
    equivalent of because it must plan before it knows whether it may write.
    """

    plans: list = field(default_factory=list)
    results: dict = field(default_factory=dict)
    actions: list = field(default_factory=list)
    previews: list = field(default_factory=list)
    targets: dict = field(default_factory=dict)
    writes: int = 0
    refusal: str | None = None

    def report(self, template: str, *args) -> None:
        """One always-reported line. Redacted at the seam that builds it."""
        self.actions.append(redact_urls(template % args))

    def preview(self, template: str, *args) -> None:
        """One "would" line. Redacted at the seam that builds it."""
        self.previews.append(redact_urls(template % args))


def _summary_differs(playlist, summary: str | None) -> bool:
    """Whether this copy's summary is not the definition's. Empty is None."""
    return (getattr(playlist, "summary", None) or None) != (summary or None)


def _cap_refusal(users: int, writes: int, config) -> str | None:
    """The one sentence that replaces a whole fan-out, or None.

    Both caps in one function because both are evaluated twice -- once before
    any token is minted and once on the exact counts -- and two copies of a
    refusal sentence is two chances for them to drift apart.
    """
    if users > config.playlists.max_users:
        return redact_urls(
            "refusing the user fan-out: %d user(s) resolved, more than the "
            "max_users cap of %d; nothing was written to any user and the "
            "admin playlists were reconciled"
            % (users, config.playlists.max_users)
        )
    if writes > config.playlists.max_user_writes:
        return redact_urls(
            "refusing the user fan-out: %d write(s) planned, more than the "
            "max_user_writes cap of %d; nothing was written to any user and "
            "the admin playlists were reconciled"
            % (writes, config.playlists.max_user_writes)
        )
    return None


def _estimated_writes(fanout, rows) -> int:
    """What the fan-out would write, from the STORED rows alone.

    Everything this reads is already in memory: each definition's ``title``
    and ``summary``, the targets pass one already resolved, and one
    ``managed_playlist_users`` query -- not the resolved item lists
    themselves, which this estimate never opens. No plex.tv call, no PMS
    listing, no token. That is the whole reason it exists -- ``max_user_writes``
    gets a number to refuse on before the fan-out has spent anything.

    It is EXACT for a create -- an absent or hash-stale row is exactly one
    write, two if the definition has a summary -- and an ESTIMATE everywhere
    else, wrong in the two ways stated here rather than discovered:

    - a stale row is counted as ONE write, and the copy may need many: each
      removal is its own DELETE, and how many there are lives in that user's
      own membership. So this UNDER-counts, which is why ``plan_user_sync``
      evaluates the cap a second time on the exact counts once the listings
      are in;
    - a stale row whose only change was ORDER needs no write at all, and this
      counts one. So it can also OVER-count by one per such copy, which errs
      towards refusing early -- the same direction the summary term errs in
      below, and the safe one.
    """
    total = 0
    for definition, _items, digest, targets in fanout:
        # A create is one POST, plus one PUT if the definition sets a summary.
        per_copy = 1 + (1 if definition.summary else 0)
        for target in targets:
            row = rows.get((definition.title, target.user_id))
            if row is None or row.definition_hash != digest:
                total += per_copy
    return total


async def plan_user_sync(
    session: AsyncSession, sync: UserSync, config, definitions, resolved, hashes
) -> UserSyncPlan:
    """Every write the fan-out would make, computed before any of it is made.

    ``resolved`` maps a definition title to the ordered items the admin half
    already resolved, and ``hashes`` maps it to the desired-state digest that
    half already computed. Both are handed down rather than recomputed: the
    resolution is the expensive part of a pass, and a second computation of the
    hash would be a second definition of what "current" means.

    A definition absent from ``resolved`` gets no fan-out at all. That covers
    every way the admin half declined to act -- a refusal, a schedule gate, an
    empty source -- and the last of those is the important one: the
    empty-result law is the same law one level down, and here it protects
    seventeen accounts at once rather than one playlist.

    **Two passes, and the split is the point.** The FIRST resolves who every
    definition reaches, from the cached ``account.users()`` list and
    ``exclude_users`` and nothing else -- ``targets_for`` is pure, its own
    docstring says "no plex.tv call happens here at all" -- and evaluates both
    caps. Only if neither refuses does the SECOND pass open a session per user
    and read a listing.

    Doing it the other way round -- filling ``targets`` and reading each
    listing in one loop, then evaluating the caps at the end -- keeps the
    LETTER of C13 A6, because nothing is written either way. It loses the
    point: a ``sync_to_users: all`` that suddenly resolves to two hundred
    accounts would mint two hundred tokens and issue two hundred PMS listings
    before refusing, which is most of the cost the caps were written against
    (the recon's "17 x ... ~ 1,700 PMS requests in one pass"). "Refused
    entirely" has to mean before the first REQUEST, not merely before the
    first write.

    ``max_user_writes`` is therefore evaluated twice, on two different numbers,
    and both evaluations are the same law: ``_estimated_writes`` from the
    stored rows before anything is opened, and ``plan.writes`` exactly once the
    listings are in. See ``_estimated_writes`` for which way each is wrong.
    """
    plan = UserSyncPlan()
    rows = {
        (row.definition_key, row.plex_user_id): row
        for row in (
            await session.execute(select(ManagedPlaylistUser))
        ).scalars().all()
    }

    # --- pass one: the fan-out's shape, with no plex.tv call at all ---------
    fanout: list[tuple[object, list, str, list]] = []
    for definition in definitions:
        if not definition.sync_to_users:
            continue
        items = resolved.get(definition.title)
        if not items:
            continue
        digest = hashes[definition.title]
        outcomes = plan.results.setdefault(definition.title, [])
        targets, skips = sync.targets_for(definition)
        for skip in skips:
            outcomes.append(PlaylistUserResult(
                title=skip.title, skipped=True, reason=skip.reason
            ))
            plan.report(
                "%r -> %r: skipped: %s",
                definition.title, skip.title, skip.reason,
            )
        for target in targets:
            plan.targets[target.user_id] = target
        fanout.append((definition, items, digest, targets))

    plan.refusal = _cap_refusal(
        len(plan.targets), _estimated_writes(fanout, rows), config
    )
    if plan.refusal is not None:
        # Nothing below this line has run, which is the assertion the two cap
        # tests make with FakeUser.token_reads and Connector.calls.
        return plan

    # --- pass two: one session and one listing per user ---------------------
    for definition, items, digest, targets in fanout:
        outcomes = plan.results[definition.title]
        for target in targets:
            listing = sync.playlists_for(target)
            if listing is None:
                _unused, reason = sync.server_for(target)
                outcomes.append(PlaylistUserResult(
                    title=target.title, user_id=target.user_id,
                    skipped=True, reason=reason,
                ))
                plan.report(
                    "%r -> %r: skipped: %s",
                    definition.title, target.title, reason,
                )
                continue
            server, _reason = sync.server_for(target)
            row = rows.get((definition.title, target.user_id))
            playlist = (
                listing.get(row.plex_rating_key) if row is not None else None
            )
            entry = _Planned(
                definition_title=definition.title, target=target, server=server,
                row=row, playlist=playlist, items=items,
                summary=definition.summary, sync_mode=definition.sync_mode,
                wanted_hash=digest,
            )

            if playlist is not None and row.definition_hash == digest:
                # Gated. The ownership LISTING was read -- there is no way to
                # answer "is this copy ours" without it -- and the MEMBERSHIP
                # was not. That is the whole cost argument: a steady-state pass
                # over seventeen users reads seventeen listings and nothing
                # else. Still planned, so the row records the confirmation.
                plan.plans.append(entry)
                continue

            if playlist is None:
                stranger = next(
                    (p for p in listing.values() if p.title == definition.title),
                    None,
                )
                if stranger is not None:
                    # The admin pass's refusal one level down, with a sharper
                    # reason: a playlist somebody made in their OWN account
                    # under our title is theirs. Plex permits duplicate titles,
                    # so creating ours beside it was available and is refused.
                    reason = (
                        "a playlist titled %r already exists in this account "
                        "and is not ours" % definition.title
                    )
                    outcomes.append(PlaylistUserResult(
                        title=target.title, user_id=target.user_id,
                        skipped=True, reason=reason,
                    ))
                    plan.report(
                        "%r -> %r: skipped: %s",
                        definition.title, target.title, reason,
                    )
                    continue
                entry.adding = list(items)
                # A create is one POST; the summary, when the definition sets
                # one, is one PUT after it.
                entry.writes = 1 + (1 if definition.summary else 0)
                entry.result = PlaylistUserResult(
                    title=target.title, user_id=target.user_id,
                    creating=len(items),
                )
                plan.preview(
                    "%r -> %r: would create with %d item(s)",
                    definition.title, target.title, len(items),
                )
            else:
                adding, removing = member_diff(
                    playlist, items, definition.sync_mode
                )
                entry.adding, entry.removing = adding, removing
                # An add BATCH is one PUT (addItems groups by the ITEM's
                # server, and every item here is the admin server's), each
                # removal is one DELETE (removeItems issues one per item), and
                # a summary edit is one PUT. The summary term can over-count by
                # one when the copy's summary is unlocked and the clear
                # declines to write -- deliberate, because a cap that errs
                # towards refusing early errs in the safe direction.
                entry.writes = (
                    (1 if adding else 0)
                    + len(removing)
                    + (1 if _summary_differs(playlist, definition.summary) else 0)
                )
                if entry.writes:
                    entry.result = PlaylistUserResult(
                        title=target.title, user_id=target.user_id,
                        adding=len(adding), removing=len(removing),
                    )
                    plan.preview(
                        "%r -> %r: would update +%d -%d",
                        definition.title, target.title,
                        len(adding), len(removing),
                    )
                # Otherwise the hash moved but nothing this pass writes did --
                # an order change, under a mode this pass does not reorder in
                # (see the module docstring). The row is re-stamped below so
                # the next pass gates on the new digest instead of re-reading
                # this membership forever, and nothing is reported.
            if entry.result is not None:
                # The create/update result belongs in the SAME per-definition
                # list the skips above append to -- ``plan.results`` is what
                # the preview response and the applier's failure-marking loop
                # both read, and a result that never landed there would be
                # invisible to each.
                outcomes.append(entry.result)
            plan.writes += entry.writes
            plan.plans.append(entry)

    # The same law on the exact number. ``max_users`` cannot have changed --
    # pass two adds no target -- but it is re-checked for free rather than
    # split across two functions.
    plan.refusal = _cap_refusal(len(plan.targets), plan.writes, config)
    return plan


async def apply_user_sync(session: AsyncSession, plan: UserSyncPlan) -> list[str]:
    """Write the planned fan-out. Nothing here re-decides anything.

    Every add, removal and create was computed by ``plan_user_sync`` against a
    listing this function does not re-read, which is what makes a cap a
    REFUSAL rather than a budget spent halfway.

    The refusal guard below re-checks ``plan.refusal`` -- the same flag the
    caller's own gate check already inspected -- rather than asserting a
    second, independent lock of its own; it exists so this function, the only
    one in the service that writes into another person's account, cannot be
    reached with a refused plan by some future caller that forgets to check.

    Failures are contained PER USER and the write is COMMITTED per entry,
    immediately after each successful create or update: a copy must never
    exist without its row, because the next pass's same-title stranger check
    (see the module docstring) treats a rowless copy as somebody else's and
    refuses to ever adopt it. If the stamp/commit itself raises right after a
    CREATE, the just-created copy is deleted from that user's account
    (best-effort) so the entry is clean for a retry rather than stranded; an
    UPDATE needs no such compensation, since its copy already had a row.
    """
    actions: list[str] = []

    def _action(template: str, *args) -> None:
        """The applier's report seam. Same rule as ``UserSyncPlan.report``:
        one place a served string is built, so one place it is redacted."""
        actions.append(redact_urls(template % args))

    if plan.refusal is not None:
        return actions
    for entry in plan.plans:
        if entry.result is None:
            try:
                _stamp(session, entry, entry.playlist, 0, 0)
                await session.flush()
                await session.commit()
            except Exception as error:
                await session.rollback()
                logger.exception(
                    "could not re-stamp the playlist %r for the user %r",
                    entry.definition_title, entry.target.title,
                )
                # No ``PlaylistUserResult`` exists for a gated entry -- there
                # is nothing to write, only a re-stamp -- but a swallowed
                # failure here is still a failure: reported by name rather
                # than left for ``last_reconciled_at`` to go silently stale.
                _action(
                    "%r -> %r: failed (%s)",
                    entry.definition_title, entry.target.title,
                    type(error).__name__,
                )
            continue
        created = False
        playlist = None
        try:
            if entry.playlist is None:
                # Bound to the object THIS call minted, and ``created`` set
                # immediately after -- before the summary PUT below, which is
                # its own Plex write and can fail on its own. Everything from
                # here to the stamp is inside one try, so any of it failing
                # leaves ``created`` and ``playlist`` naming exactly the copy
                # to compensate.
                playlist = _create_copy(entry)
                created = True
                if entry.summary:
                    playlist.editSummary(entry.summary)
                added, removed = len(entry.items), 0
            else:
                playlist = entry.playlist
                added, removed = _update_copy(entry)
            _stamp(session, entry, playlist, added, removed)
            await session.flush()
            await session.commit()
        except Exception as error:
            await session.rollback()
            if created:
                # The row never landed, so without this the copy this pass
                # just created in THEIR account is a stranger to every future
                # pass -- the same-title check at plan time refuses to ever
                # re-adopt it. Best-effort: a failure deleting it is logged
                # and swallowed, never allowed to shadow the entry's own.
                try:
                    playlist.delete()
                except Exception:
                    logger.exception(
                        "could not delete the just-created copy of %r for "
                        "%r after a failed stamp",
                        entry.definition_title, entry.target.title,
                    )
            # Class name only, for the reason the whole subsystem repeats: a
            # plexapi failure's message carries the base URL and this string is
            # SERVED. The traceback goes to the log whole -- row 207 rules
            # stdout the trusted sink and wants it there.
            logger.exception(
                "could not sync the playlist %r to the user %r",
                entry.definition_title, entry.target.title,
            )
            entry.result.failed = True
            entry.result.skipped = True
            # Assigned rather than constructed, so ``__post_init__``'s
            # redaction does not run: a class name cannot carry a URL, and
            # writing it through ``redact_urls`` here would say it could.
            entry.result.reason = type(error).__name__
            # ``added``/``removed`` are left at their construction-time 0:
            # on a compensated create nothing survived, and serving a count
            # for a copy that was just deleted would be worse than serving
            # none.
            _action(
                "%r -> %r: failed (%s)",
                entry.definition_title, entry.target.title,
                type(error).__name__,
            )
            continue
        entry.result.added, entry.result.removed = added, removed
        if created:
            _action(
                "%r -> %r: created with %d item(s)",
                entry.definition_title, entry.target.title, added,
            )
        else:
            _action(
                "%r -> %r: updated +%d -%d",
                entry.definition_title, entry.target.title, added, removed,
            )
    return actions


def _create_copy(entry: _Planned):
    """One user's copy, created under THEIR token. The POST only.

    ``server.createPlaylist`` is ``Playlist.create`` is ``Playlist._create``,
    which POSTs ``/playlists`` on whatever server object it is handed -- the
    user's -- and builds its uri from that server's own ``_uriRoot()``, naming
    the same machine the items came from. Admin-fetched item objects are
    therefore the correct things to pass, not an accident that happens to work.

    The copy is created IN the definition's order, which is the whole of the
    ordering this phase provides (see the module docstring).

    The summary PUT, when the definition sets one, is deliberately NOT issued
    here: it is the caller's second write against this same object, made
    after the caller has already bound ``created = True`` to it, so a PUT
    that fails leaves the compensation able to see and delete the copy this
    call minted. Folding it into this function would let that failure unwind
    before the caller ever learns which object to compensate.
    """
    return entry.server.createPlaylist(title=entry.definition_title, items=entry.items)


def _update_copy(entry: _Planned) -> tuple[int, int]:
    """Settle the summary, add, remove. Never delete and recreate.

    ``reload()`` before ``removeItems`` is mandatory and it is the admin path's
    own reason: ``removeItems`` turns each item into a playlist-scoped id
    through ``_getPlaylistItemID``, which walks the CACHED ``items()`` -- a
    snapshot the ``addItems`` above left in place.
    """
    playlist = entry.playlist
    if entry.summary:
        if _summary_differs(playlist, entry.summary):
            playlist.editSummary(entry.summary)
    elif _summary_differs(playlist, None):
        # Imported at call time rather than module scope:
        # ``collections/playlists.py`` imports THIS module for the per-user
        # stage, so a module-scope import the other way would close the cycle
        # every validator in ``config/schema.py`` documents. The helper is
        # shared rather than copied because the narrowing it encodes -- an
        # UNLOCKED summary was never ours and is left alone -- matters more in
        # somebody else's account, not less.
        from autoposter.collections.playlists import _clear_playlist_summary

        _clear_playlist_summary(playlist)
    if entry.adding:
        playlist.addItems(entry.adding)
    if entry.removing:
        playlist.reload()
        playlist.removeItems(entry.removing)
    return len(entry.adding), len(entry.removing)


def _stamp(session: AsyncSession, entry: _Planned, playlist, added: int, removed: int):
    """Record what this copy now holds, creating the row on first sight."""
    row = entry.row
    if row is None:
        row = ManagedPlaylistUser(
            definition_key=entry.definition_title,
            plex_user_id=entry.target.user_id,
        )
        session.add(row)
        entry.row = row
    # Refreshed every pass: a user who renamed themselves in Plex keeps their
    # id, and the report should read the name they use now.
    row.plex_user_title = entry.target.title
    row.plex_rating_key = str(playlist.ratingKey)
    row.definition_hash = entry.wanted_hash
    row.member_count = len(entry.items)
    row.last_added = added
    row.last_removed = removed
    row.last_reconciled_at = func.now()


async def user_sweep_candidates(
    session: AsyncSession, sync: UserSync, definitions
) -> tuple[list, list]:
    """``(candidates, unreachable)`` -- user copies no configuration asks for.

    C13 A2: three removal events, ONE predicate. A row is a candidate when its
    ``(definition_key, plex_user_id)`` pair is not in the set this
    configuration resolves to -- which covers a user dropped from
    ``sync_to_users``, a definition deleted outright, and a definition that
    stopped naming anybody -- AND its ``plex_rating_key`` names a playlist
    currently in THAT USER'S listing.

    A row whose object is already gone is not a candidate, exactly as one level
    up: there is nothing to delete, and reporting it as deletable would invite
    an operator to authorise a deletion that cannot happen.

    A row whose user cannot be reached at all -- gone from the account,
    ``protected``, or no token -- is not a candidate either, because ownership
    is unverifiable and nothing is deleted on a guess. Those rows come back
    separately so the caller can REPORT them: a row that silently accumulated
    would be a copy in somebody's account that nobody could account for.

    Calling ``targets_for`` again costs nothing: it is pure over the cached
    user list, and the servers and listings it leads to are the ones the plan
    already opened.
    """
    wanted: set[tuple[str, int]] = set()
    for definition in definitions:
        if not definition.sync_to_users:
            continue
        targets, _skips = sync.targets_for(definition)
        for target in targets:
            wanted.add((definition.title, target.user_id))

    candidates: list = []
    unreachable: list = []
    rows = (await session.execute(select(ManagedPlaylistUser))).scalars().all()
    for row in rows:
        if (row.definition_key, row.plex_user_id) in wanted:
            continue
        target = sync.target_for_id(row.plex_user_id)
        if target is None:
            unreachable.append(row)
            continue
        listing = sync.playlists_for(target)
        if listing is None:
            unreachable.append(row)
            continue
        playlist = listing.get(row.plex_rating_key)
        if playlist is None:
            continue
        candidates.append((row, playlist, target))
    return candidates, unreachable
