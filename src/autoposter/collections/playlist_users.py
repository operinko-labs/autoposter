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
from dataclasses import dataclass

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
