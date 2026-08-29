"""The five TMDb person builders: one filmography, filtered by role.

``tmdb_actor``, ``tmdb_director``, ``tmdb_writer``, ``tmdb_producer`` and
``tmdb_crew`` are Kometa's five names for one question -- "what has this person
worked on?" -- asked five ways. They are five *registrations* of one build
path (``PlexRatingKeyBuilder``'s precedent) plus ``ROLES``, which is the only
thing that differs between them.

**Why the credits endpoints rather than discover.** ``/discover/movie`` has
``with_cast``, ``with_crew`` and ``with_people``, and none of them is the right
route: they are paged (so a prolific person costs ten requests and is capped
anyway), and ``/discover/tv`` has no credit filter at all, which would leave
``tmdb_actor`` unable to build a Show collection. ``/person/{id}/movie_credits``
and ``/person/{id}/tv_credits`` each answer in one unpaged request, so one
definition serves both library types by following ``media_types`` the way
``tmdb_chart`` does.

**The role table is the whole builder, and it is the whole risk.** TMDb has no
opinion about which credits make a collection; a wrong ``job`` or
``department`` string does not 404 and does not error. It returns a full,
plausible collection of the wrong titles, or an empty one. So each row is
pinned deliberately, and the three non-obvious ones are non-obvious in
opposite directions:

- **``tmdb_director`` matches ``job == "Director"``, not the ``Directing``
  department.** That department also holds ``First Assistant Director``,
  ``Script Supervisor``, ``Co-Director`` and ``Script Coordinator``, none of
  which is what an operator writing ``tmdb_director`` meant.
- **``tmdb_producer`` matches ``job == "Producer"``, exactly, and therefore
  not ``Executive Producer``, ``Co-Producer``, ``Associate Producer``,
  ``Line Producer`` or ``Supervising Producer``.** The ``Production``
  department is even less usable as the filter -- it holds ``Casting``,
  ``Casting Director``, ``Production Manager`` and ``Unit Production
  Manager``. Kometa's name is the singular credit and so is this.
- **``tmdb_writer`` matches ``department == "Writing"``, not a job**, and that
  is the same argument reaching the opposite answer. TMDb spreads writing
  credits across ``Screenplay``, ``Writer``, ``Story``, ``Screenstory``,
  ``Teleplay``, ``Novel``, ``Book``, ``Characters`` and ``Adaptation``, and
  every one of them is a writing credit -- an exact ``job`` match would return
  only the handful credited literally ``Writer`` and silently drop most
  screenwriters. Here the department genuinely is the concept; above it is
  not.

**Order** is TMDb's, preserved -- see ``TmdbListClient.person_credits`` for
what that order actually is (its own relevance-weighted credit order, not
chronological) and why nothing re-sorts it here. Repeats are collapsed to the
first occurrence: a director who also produced a film, or an actor credited
with two characters in one, is two credits on one title, and the ids this
builder hands back should say what the collection *is*. The resolver would
dedupe anyway; that is a reason for the list to be honest, not a reason to
leave it wrong.

**A role that matches nothing warns and returns empty.** It cannot raise:
a person with no television work at all is a legitimate answer for a Show
library, and a legitimate definition would then fail on one library and
succeed on another. It cannot be silent either -- an empty membership is read
one layer down as "make no changes" (``lists.reconcile_list_collection``), so
a wrong role string would silently freeze the collection instead of erroring,
indistinguishable from the legitimate case above. The warning names the
person, the role and how many credits were actually read, so the two cases
are distinguishable from the log alone.
``tmdb_discover`` warns at its page cap for the same reason.

**Deliberately absent -- this is the 10c line, and it is still a hard stop.**
Phase 10c-lite lifted the FIRST item off this list: ``tmdb_person`` -- the
person's biography as the collection summary and their TMDb profile photo as
its poster -- ships, through ``TmdbListClient.person_detail`` and the
``tmdb_profile`` poster source (``collections/posters.py``). The read is
contained (see ``_profile``): a dead person record leaves the membership
reconciled. Everything below is still 10c's and none of it belongs here:

- ``tmdb_popular_people``: filed on roadmap row 194 -- its upstream shape is
  a Plex actor-tag search (row 169's machinery), not a list source.
- ``tmdb_birthday`` / ``tmdb_deathday``: gating whether a definition runs at
  all on a date derived from the person. Filed on roadmap row 160, with the
  day-level window machinery it is really asking for; ``person_detail``
  deliberately does not carry ``birthday``/``deathday`` out of the transport.
- appearance thresholds and the dynamic ``actor``/``director``/``writer``/
  ``producer`` collection *types* -- "every actor with at least N titles in
  this library" -- which need the library-wide credit scan below. Roadmap row
  194, and gated twice over: the scan, and row 169's people search attributes.
- library-wide credit scans: enumerating every credit of every item in a
  library. That is 10c's expensive piece and its caching is a design decision
  the row records; this module reads one person, by id, in two requests.

The roadmap's row 83 lists the summaries and the birthday gating alongside
these builders; it CLOSED with 10c-lite, and row 194 is where the expensive
half went.
"""
import logging
from dataclasses import dataclass

from autoposter.collections.builders.base import (
    BuilderContext,
    BuilderResult,
    require_library_type,
)

# ``TmdbEntityParams`` and ``_TmdbBuilder`` are ``builders/tmdb.py``'s, reused
# rather than restated: the params are the same ``{id}`` the other by-id TMDb
# builders take, and the absent-client refusal must read identically wherever
# it comes from. ``tmdb_discover.py`` imports ``_TmdbBuilder`` the same way.
from autoposter.collections.builders.tmdb import TmdbEntityParams, _TmdbBuilder

# The poster kind is ``posters.py``'s constant rather than a literal here, so
# the builder that emits it and the dispatch that reads it cannot drift into two
# spellings of one string -- which would fail silently, as "no poster source".
from autoposter.collections.posters import TMDB_PROFILE_KIND
from autoposter.providers.tmdb_lists import PersonCredit

logger = logging.getLogger(__name__)

__all__ = [
    "ROLES",
    "CreditRole",
    "TmdbActorBuilder",
    "TmdbCrewBuilder",
    "TmdbDirectorBuilder",
    "TmdbProducerBuilder",
    "TmdbWriterBuilder",
]


@dataclass(frozen=True)
class CreditRole:
    """Which credits one builder name means.

    ``kind`` is the array the credit came out of (``"cast"`` or ``"crew"``).
    ``job`` and ``department`` are TMDb's own strings, and ``None`` means "do
    not narrow on this" -- so ``CreditRole(kind="crew")`` is every crew credit
    and ``CreditRole(kind="cast")`` is every cast one. At most one of the two
    is ever set; see the module docstring for why each row picks the one it
    picks.
    """

    kind: str
    job: str | None = None
    department: str | None = None

    def matches(self, credit: PersonCredit) -> bool:
        if credit.kind != self.kind:
            return False
        if self.job is not None and credit.job != self.job:
            return False
        return self.department is None or credit.department == self.department

    def described(self) -> str:
        """What this role is, for the it-matched-nothing warning."""
        if self.job is not None:
            return f"crew credits with the job {self.job!r}"
        if self.department is not None:
            return f"crew credits in the {self.department!r} department"
        return f"{self.kind} credits"


# Builder name -> the credits it means. Keyed by ``type_name`` and read by
# ``type_name``, so the table and the registrations cannot drift: a name with
# no row cannot build and a row with no name cannot be asked for, and
# ``tests/test_builder_tmdb_person.py`` pins that the two sets are equal.
ROLES: dict[str, CreditRole] = {
    "tmdb_actor": CreditRole(kind="cast"),
    "tmdb_director": CreditRole(kind="crew", job="Director"),
    "tmdb_writer": CreditRole(kind="crew", department="Writing"),
    "tmdb_producer": CreditRole(kind="crew", job="Producer"),
    "tmdb_crew": CreditRole(kind="crew"),
}


class _TmdbPersonBuilder(_TmdbBuilder):
    """One person's filmography, narrowed to this builder's role.

    ``media_types`` maps this library's type to the credits endpoint that
    answers for it and doubles as the mismatch guard's allowed set, as every
    other TMDb builder's does -- a library type TMDb has no media type for has
    no endpoint to read, and there is nothing sensible to fall back to.
    """

    params_model = TmdbEntityParams
    media_types = {"Movie": "movie", "Show": "tv"}

    @property
    def role(self) -> CreditRole:
        return ROLES[self.type_name]

    async def build(self, ctx: BuilderContext) -> BuilderResult:
        params = TmdbEntityParams.model_validate(ctx.config)
        require_library_type(
            f"the {self.type_name!r} builder", ctx.library_type, self.media_types
        )
        client = self._client(ctx)
        credits = await client.person_credits(
            params.id, self.media_types[ctx.library_type]
        )

        role = self.role
        ids: list[str] = []
        seen: set[str] = set()
        for credit in credits:
            if not role.matches(credit) or credit.tmdb_id in seen:
                continue
            seen.add(credit.tmdb_id)
            ids.append(credit.tmdb_id)

        if not ids:
            logger.warning(
                "%s: TMDb person %d has no %s among the %d credit(s) it lists for "
                "this library's media type, so this collection would be emptied. "
                "Check the person id and that this role is one they hold.",
                self.type_name, params.id, role.described(), len(credits),
            )

        summary, profile_path = await self._profile(client, params.id)
        return BuilderResult(
            ids=[("tmdb", value) for value in ids],
            summary=summary,
            poster_kind=TMDB_PROFILE_KIND if profile_path else None,
            poster_key=profile_path,
        )

    async def _profile(self, client, person_id: int) -> tuple[str | None, str | None]:
        """The person's biography and profile path, or ``(None, None)``.

        CONTAINED, unlike the credits read above, and the asymmetry is the
        point. The credits ARE this collection's membership: a failure there
        has to raise, because an empty list one layer down means "remove every
        member" (``lists.reconcile_list_collection``). A biography and a poster
        are cosmetic -- ``posters.apply_poster`` says exactly that for every
        other source -- so a second endpoint being down must leave the
        membership reconciled and the artwork untouched rather than fail the
        definition over an ornament.

        Read AFTER the credits, so a wrong id fails once on the membership call
        instead of making a second doomed request first.

        ``poster_kind`` is left None when there is no photo rather than being
        set with an empty key: ``apply_poster`` would resolve that to no URL
        and report "no poster source" on every pass, which is a line in every
        report for a person who simply has no photo.
        """
        try:
            detail = await client.person_detail(person_id)
        except Exception:
            # The engine's containment invariant, one level down: the class
            # name and traceback go to the log and nothing derived from the
            # exception is returned, because a provider error commonly carries
            # the URL it failed on.
            logger.exception(
                "%s: could not read TMDb person %d's own record; the collection "
                "keeps whatever summary and poster it already has",
                self.type_name, person_id,
            )
            return None, None
        return detail.biography, detail.profile_path


class TmdbActorBuilder(_TmdbPersonBuilder):
    """Everything a person is credited in front of the camera for."""

    type_name = "tmdb_actor"


class TmdbDirectorBuilder(_TmdbPersonBuilder):
    """Everything a person directed."""

    type_name = "tmdb_director"


class TmdbWriterBuilder(_TmdbPersonBuilder):
    """Everything a person holds a writing credit on."""

    type_name = "tmdb_writer"


class TmdbProducerBuilder(_TmdbPersonBuilder):
    """Everything a person produced -- the singular credit, not the executive,
    associate, line or supervising ones. See the module docstring."""

    type_name = "tmdb_producer"


class TmdbCrewBuilder(_TmdbPersonBuilder):
    """Everything a person worked on behind the camera, in any role."""

    type_name = "tmdb_crew"
