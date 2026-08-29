"""The five TMDb person filmography builders, and the role table behind them.

The transport is ``providers/tmdb_lists.person_credits``; the builders are
``src/autoposter/collections/builders/tmdb_person.py``. What these tests are
about is the one thing that can go wrong here without anybody noticing:
**the role filter**.

``/person/{id}/{movie,tv}_credits`` hands back every credit a person holds,
and which of them a collection means is entirely our decision -- TMDb has no
opinion. A wrong ``job`` or ``department`` string does not error and does not
404; it returns a full, plausible collection of the wrong titles, or an empty
one. So each role is pinned against a fixture that deliberately holds the
credits it must *not* pick up:

- an ``Executive Producer`` entry, which ``tmdb_producer`` must exclude
  (TMDb's ``Production`` department is not "producers": it also holds
  ``Casting`` and the production managers);
- a ``First Assistant Director`` entry, which ``tmdb_director`` must exclude
  (TMDb's ``Directing`` department is not "directors" either);
- a ``Screenplay`` entry *and* a ``Writer`` entry, which ``tmdb_writer`` must
  both pick up (the ``Writing`` department genuinely is the concept -- an
  exact ``job`` match would silently drop most screenwriters);
- the same person credited twice on one title, which every role must return
  once, at its first position.

**On the fixtures.** ``tests/fixtures/collections/tmdb_person_*_credits.json``
are TMDb's response *shape*, not recordings of a real person: the keys, the
nesting, the movie-vs-tv ``title``/``name`` difference and above all the
``job``/``department`` vocabulary are TMDb's, while the credits themselves are
constructed to exercise every branch of the role table. The titles say what
they are for, so nothing here can be mistaken for a transcription of a real
filmography. ``tmdb_person_detail.json`` is the same kind of construction --
TMDb's ``/person/{id}`` response shape, a person who does not exist, and a
biography whose own text says it is a constructed one.
"""
import json
import logging
from pathlib import Path

import httpx
import pytest
from pydantic import ValidationError

from autoposter.collections.builders import REGISTRY, BuilderContext, SourceClients
from autoposter.collections.builders.base import LibraryTypeMismatch
from autoposter.collections.builders.tmdb import TmdbBuilderRefused
from autoposter.collections.builders.tmdb_person import ROLES, CreditRole
from autoposter.collections.posters import TMDB_PROFILE_KIND, tmdb_profile_url
from autoposter.config.schema import CollectionDefinition
from autoposter.providers.tmdb_lists import (
    PersonCredit,
    PersonDetail,
    TmdbListClient,
    TmdbListRefused,
)

FIXTURES = Path(__file__).parent / "fixtures" / "collections"

PERSON_BUILDERS = ("tmdb_actor", "tmdb_director", "tmdb_writer", "tmdb_producer", "tmdb_crew")

MOVIE_CREDITS = "/person/4242/movie_credits"
TV_CREDITS = "/person/4242/tv_credits"
PERSON_DETAIL = "/person/4242"


def load(name):
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def _path(request) -> str:
    """The TMDb path, without the ``/3`` API-version prefix (see
    ``tests/test_tmdb_lists_client.py``, which pins that the prefix is sent)."""
    return request.url.path.removeprefix("/3")


def _credit_paths(seen: list) -> list[str]:
    """Only the filmography requests. A build makes two kinds now -- the
    credits, which are the membership, and the person's own record, which is
    the summary and the poster -- and the tests about one must not move when
    the other changes."""
    return [_path(request) for request in seen if _path(request).endswith("_credits")]


def _routed(routes: dict, seen: list | None = None):
    def handler(request):
        if seen is not None:
            seen.append(request)
        payload = routes.get(_path(request))
        if payload is None:
            return httpx.Response(404, json=load("tmdb_not_found.json"))
        return httpx.Response(200, json=payload)

    return httpx.MockTransport(handler)


def _detail(**overrides):
    """The person-detail fixture, with fields replaced for the shape tests."""
    return load("tmdb_person_detail.json") | overrides


def _credits(**overrides):
    """The movie-credits fixture, with whole arrays replaced for the shape tests."""
    return {
        MOVIE_CREDITS: load("tmdb_person_movie_credits.json") | overrides,
        PERSON_DETAIL: _detail(),
    }


def _both():
    return {
        MOVIE_CREDITS: load("tmdb_person_movie_credits.json"),
        TV_CREDITS: load("tmdb_person_tv_credits.json"),
        PERSON_DETAIL: _detail(),
    }


def _sources(http):
    return SourceClients(tmdb=TmdbListClient("a-read-access-token", http))


def _ctx(sources: SourceClients, library_type: str = "Movie", **params) -> BuilderContext:
    library = "Movies" if library_type == "Movie" else "TV Shows"
    return BuilderContext(
        library=library, library_type=library_type, config=params, sources=sources
    )


async def _build(builder: str, routes: dict, seen: list | None = None, **ctx):
    async with httpx.AsyncClient(transport=_routed(routes, seen)) as http:
        return await REGISTRY[builder].build(_ctx(_sources(http), id=4242, **ctx))


def _ids(result) -> list[str]:
    assert all(namespace == "tmdb" for namespace, _ in result.ids)
    return [value for _, value in result.ids]


# --- the role table ----------------------------------------------------------


def test_the_role_table_names_exactly_the_five_registered_builders():
    """The table is keyed by ``type_name`` and the builders read it by
    ``type_name``, so a registration without a row would be a ``KeyError`` in
    the middle of a pass and a row without a registration would be a role
    nobody can ask for."""
    assert set(ROLES) == set(PERSON_BUILDERS)
    for name in PERSON_BUILDERS:
        assert REGISTRY[name].type_name == name
        assert isinstance(REGISTRY[name].role, CreditRole)


def test_the_pinned_job_and_department_strings_are_tmdbs_own_spelling():
    """These five strings are the entire builder. They are TMDb's, capitalised
    exactly as TMDb sends them, and a change here changes what every existing
    definition of that role builds -- so they are asserted literally rather
    than only through the fixtures."""
    assert ROLES["tmdb_actor"] == CreditRole(kind="cast")
    assert ROLES["tmdb_director"] == CreditRole(kind="crew", job="Director")
    assert ROLES["tmdb_writer"] == CreditRole(kind="crew", department="Writing")
    assert ROLES["tmdb_producer"] == CreditRole(kind="crew", job="Producer")
    assert ROLES["tmdb_crew"] == CreditRole(kind="crew")


# --- what each role picks up -------------------------------------------------


async def test_an_actor_is_the_cast_credits_in_tmdbs_order():
    seen: list = []
    result = await _build("tmdb_actor", _both(), seen)

    assert _ids(result) == ["11", "1891"]
    assert _path(seen[0]) == MOVIE_CREDITS


async def test_a_director_is_the_director_job_and_nothing_else():
    result = await _build("tmdb_director", _both())

    assert _ids(result) == ["1891", "27205"]


async def test_a_producer_is_the_exact_job_not_an_executive_producer():
    """The whole point of pinning ``job`` rather than ``department`` here.
    1892 is credited ``Executive Producer`` and 122 ``Casting`` -- both sit in
    TMDb's ``Production`` department, and neither is what an operator writing
    ``tmdb_producer`` meant."""
    result = await _build("tmdb_producer", _both())

    assert _ids(result) == ["1891"]
    assert "1892" not in _ids(result)
    assert "122" not in _ids(result)


async def test_a_writer_is_the_whole_writing_department():
    """The mirror of the producer decision, and it goes the other way: 1892 is
    credited ``Screenplay`` and 122 ``Writer``. An exact ``job: "Writer"``
    match would return only 122 and silently drop the screenwriter."""
    result = await _build("tmdb_writer", _both())

    assert _ids(result) == ["1892", "122"]


async def test_crew_is_every_crew_credit_and_no_cast_credit():
    result = await _build("tmdb_crew", _both())

    assert _ids(result) == ["1891", "1892", "27205", "49026", "122"]
    # 11 is a cast-only credit, and cast is not crew.
    assert "11" not in _ids(result)


async def test_a_director_and_a_producer_do_not_build_the_same_collection():
    """The falsifiability of the role filter as one assertion: the fixture is
    built so that dropping the filter would make these two identical."""
    director = _ids(await _build("tmdb_director", _both()))
    producer = _ids(await _build("tmdb_producer", _both()))

    assert director != producer
    assert set(director) - set(producer) == {"27205"}


async def test_an_assistant_director_is_not_a_director():
    """``Directing`` is a department, not a job: it also holds the assistant
    directors, the script supervisors and the script coordinators."""
    assert "49026" not in _ids(await _build("tmdb_director", _both()))
    # ...but it *is* a crew credit.
    assert "49026" in _ids(await _build("tmdb_crew", _both()))


async def test_a_casting_credit_is_not_a_producer_credit():
    assert "122" not in _ids(await _build("tmdb_producer", _both()))
    assert "122" in _ids(await _build("tmdb_crew", _both()))


# --- order and repeats -------------------------------------------------------


async def test_a_title_credited_twice_is_kept_once_at_its_first_position():
    """An actor with two roles in one film, or a director who also produced
    it, is two credits on one title. The resolver would dedupe anyway, but the
    ids a builder hands back should say what the collection is: 11 appears
    twice in the cast array and 1891 twice in the crew array."""
    actor = _ids(await _build("tmdb_actor", _both()))
    crew = _ids(await _build("tmdb_crew", _both()))

    assert actor.count("11") == 1
    assert actor == ["11", "1891"]
    assert crew.count("1891") == 1
    assert crew[0] == "1891"


async def test_the_order_is_the_order_tmdb_listed_the_credits_in():
    """Not chronological, not alphabetical, not re-sorted here: whatever TMDb
    put in the array. The fixture's crew array is deliberately not in any of
    those orders, so a sort introduced later shows up here."""
    crew = _ids(await _build("tmdb_crew", _both()))

    assert crew == ["1891", "1892", "27205", "49026", "122"]
    assert crew != sorted(crew)


# --- both library types ------------------------------------------------------


async def test_a_show_library_reads_the_tv_credits_endpoint():
    """One definition serves both libraries -- the media type comes from the
    library the pass is running against, exactly as ``tmdb_chart``'s does."""
    seen: list = []
    result = await _build("tmdb_actor", _both(), seen, library_type="Show")

    assert _ids(result) == ["95396"]
    assert _path(seen[0]) == TV_CREDITS


async def test_the_role_filter_applies_on_the_tv_endpoint_too():
    """The tv fixture's only ``Production`` credit is an ``Executive
    Producer``, so a producer definition on a Show library must come back
    empty rather than picking it up."""
    assert _ids(await _build("tmdb_director", _both(), library_type="Show")) == ["1396"]
    assert _ids(await _build("tmdb_crew", _both(), library_type="Show")) == ["1396", "1416"]
    assert _ids(await _build("tmdb_producer", _both(), library_type="Show")) == []


@pytest.mark.parametrize("builder", PERSON_BUILDERS)
async def test_every_person_builder_serves_both_library_types(builder):
    """The credits endpoints only: a build also reads the person's own record,
    which is one endpoint for both library types and is counted by its own
    tests below."""
    seen: list = []
    await _build(builder, _both(), seen)
    await _build(builder, _both(), seen, library_type="Show")

    assert _credit_paths(seen) == [MOVIE_CREDITS, TV_CREDITS]


@pytest.mark.parametrize("builder", PERSON_BUILDERS)
async def test_a_library_type_tmdb_has_no_media_type_for_is_refused(builder):
    """And refused before a request: the media-type map has to be total for
    the endpoint to be choosable at all."""
    seen: list = []
    with pytest.raises(LibraryTypeMismatch):
        await _build(builder, _both(), seen, library_type="Artist")

    assert seen == []


# --- a role that matches nothing ---------------------------------------------


async def test_a_role_that_matches_no_credit_warns_rather_than_emptying_in_silence(caplog):
    """Empty is legitimate data -- a film director genuinely has no TV credits
    -- so this is not a raise. But an empty membership means "remove every
    member" one layer down, and a wrong job string produces exactly the same
    empty, so it does not get to be silent either. The same judgement
    ``tmdb_discover`` made at the page cap."""
    with caplog.at_level(logging.WARNING, logger="autoposter.collections.builders.tmdb_person"):
        result = await _build("tmdb_producer", _both(), library_type="Show")

    assert result.ids == []
    assert len(caplog.records) == 1
    logged = caplog.records[0].getMessage()
    assert "4242" in logged and "tmdb_producer" in logged
    # It says how much it looked at, so "wrong role" and "person has no TV
    # work" are distinguishable from the log line alone.
    assert "3" in logged


async def test_a_role_that_matches_something_does_not_warn(caplog):
    """A warning on every build would be no signal at all."""
    with caplog.at_level(logging.WARNING, logger="autoposter.collections.builders.tmdb_person"):
        result = await _build("tmdb_director", _both())

    assert _ids(result) == ["1891", "27205"]
    assert caplog.records == []


# --- the transport -----------------------------------------------------------


async def test_the_credits_read_is_one_request_and_sends_no_page():
    """``/person/{id}/*_credits`` answers with every credit at once, so this
    is the ``collection_parts`` shape: no pager, no ``page`` parameter, and
    therefore one cache key per person per media type. The person's own record
    is a *second* endpoint, deliberately -- it is one more request and one more
    cache entry, not a page of this one."""
    seen: list = []
    await _build("tmdb_crew", _both(), seen)

    credits = [request for request in seen if _path(request).endswith("_credits")]
    assert len(credits) == 1
    assert "page" not in credits[0].url.params


async def test_a_missing_person_raises_naming_the_id_rather_than_building_nothing():
    """``fetch_json`` turns a 404 into ``None``, and ``None`` must not become
    an empty collection -- in sync mode that removes every member."""
    with pytest.raises(TmdbListRefused) as caught:
        await _build("tmdb_actor", {})

    message = str(caught.value)
    assert "4242" in message
    assert "movie_credits" in message


async def test_a_response_with_no_crew_array_is_refused():
    """A shape TMDb does not send. Reading it as "no crew credits" would build
    an empty collection out of a broken response."""
    with pytest.raises(TmdbListRefused, match="crew"):
        await _build("tmdb_director", _credits(crew="not an array"))


async def test_a_response_with_no_cast_array_is_refused():
    with pytest.raises(TmdbListRefused, match="cast"):
        await _build("tmdb_actor", _credits(cast=None))


async def test_a_credit_with_no_id_is_refused_rather_than_dropped():
    """A silently dropped member is a slightly smaller collection every pass
    and no way at all to notice."""
    with pytest.raises(TmdbListRefused, match="'id'"):
        await _build(
            "tmdb_director",
            _credits(crew=[{"title": "no id here", "job": "Director", "department": "Directing"}]),
        )


async def test_a_person_with_no_credits_at_all_is_not_an_error():
    """An empty ``cast``/``crew`` is TMDb saying "this person has none of
    that", which is data. The 404 above is the id being wrong."""
    result = await _build("tmdb_actor", _credits(cast=[], crew=[]))

    assert result.ids == []


def test_a_credit_carries_only_what_the_role_table_reads():
    """The transport's return type as the enforcement, rather than everyone
    remembering. It matters MORE now that a person's biography and photo do
    ship: they come from the person's own record (``person_detail``), which is
    one call and one cache entry, and a credit entry also carries a profile
    path of its own. Widening this dataclass is how a later hand would end up
    reading artwork off whichever credit happened to be first."""
    assert set(PersonCredit.__dataclass_fields__) == {"tmdb_id", "kind", "job", "department"}


# --- the person's own record -------------------------------------------------


async def _detail_of(routes: dict):
    async with httpx.AsyncClient(transport=_routed(routes)) as http:
        return await TmdbListClient("a-read-access-token", http).person_detail(4242)


async def test_person_detail_carries_the_biography_and_the_profile_path():
    detail = await _detail_of({PERSON_DETAIL: _detail()})

    assert detail.biography.startswith("A constructed biography.")
    assert detail.profile_path == "/a-constructed-profile-path.jpg"


async def test_person_detail_carries_nothing_else():
    """The same rule ``PersonCredit`` keeps, for the same reason. The payload
    also holds ``birthday``, ``deathday``, ``place_of_birth``, ``also_known_as``
    and ``popularity``; every one of them is a feature nobody has decided on
    (birthday gating is roadmap row 160's filed work), and a field nobody reads
    is a field a builder can start reading without the decision being made."""
    assert set(PersonDetail.__dataclass_fields__) == {"biography", "profile_path"}


async def test_the_detail_read_is_one_unpaged_request():
    """``/person/{id}`` answers in one response, like ``/collection/{id}``, so
    this sends no ``page`` -- and no ``append_to_response`` either, which is
    also what upstream's own bare person read sends."""
    seen: list = []
    async with httpx.AsyncClient(transport=_routed({PERSON_DETAIL: _detail()}, seen)) as http:
        await TmdbListClient("a-read-access-token", http).person_detail(4242)

    assert [_path(request) for request in seen] == [PERSON_DETAIL]
    assert "page" not in seen[0].url.params
    assert "append_to_response" not in seen[0].url.params


async def test_an_empty_biography_is_none_rather_than_an_empty_summary():
    """TMDb answers ``""`` -- not null -- for a person with no biography in the
    requested language. Passed through, that is a collection summary set to the
    empty string, which reads as "somebody chose this" one layer down."""
    detail = await _detail_of({PERSON_DETAIL: _detail(biography="   ")})

    assert detail.biography is None


async def test_a_missing_profile_photo_is_none_rather_than_a_guessable_path():
    detail = await _detail_of({PERSON_DETAIL: _detail(profile_path=None)})

    assert detail.profile_path is None


async def test_a_response_with_no_name_is_refused():
    """The response-shape probe. TMDb's person record always carries a name; a
    payload without one is not "a person with no biography", it is a response
    this client cannot read -- and the two must not be the same answer, because
    the first quietly leaves a collection without artwork forever."""
    with pytest.raises(TmdbListRefused, match="'name'"):
        await _detail_of({PERSON_DETAIL: {"id": 4242}})


async def test_an_unknown_person_id_raises_rather_than_returning_nothing():
    with pytest.raises(TmdbListRefused, match="404"):
        await _detail_of({})


# --- params ------------------------------------------------------------------


@pytest.mark.parametrize("builder", PERSON_BUILDERS)
def test_a_person_definition_is_validated_at_config_load(builder):
    """``id`` and nothing else, checked at the moment of the edit rather than
    mid-pass hours later."""
    CollectionDefinition(title="Their films", builder=builder, params={"id": 4242})
    with pytest.raises(ValidationError):
        CollectionDefinition(title="Their films", builder=builder, params={"person": 4242})


@pytest.mark.parametrize("builder", PERSON_BUILDERS)
async def test_the_person_builders_refuse_params_they_do_not_understand(builder):
    with pytest.raises(ValidationError):
        await REGISTRY[builder].build(_ctx(SourceClients(), id=4242, threshold=3))


@pytest.mark.parametrize("builder", PERSON_BUILDERS)
async def test_the_person_builders_refuse_a_missing_id(builder):
    with pytest.raises(ValidationError):
        await REGISTRY[builder].build(_ctx(SourceClients()))


async def test_a_person_id_of_zero_is_refused():
    """``0`` is what a mis-read config or an unfilled template renders to, and
    TMDb answers it with a 404 the operator then has to go and read."""
    with pytest.raises(ValidationError):
        await REGISTRY["tmdb_actor"].build(_ctx(SourceClients(), id=0))


async def test_a_person_id_written_as_a_string_still_works():
    result = await _build("tmdb_actor", _both())
    async with httpx.AsyncClient(transport=_routed(_both())) as http:
        as_text = await REGISTRY["tmdb_actor"].build(_ctx(_sources(http), id="4242"))

    assert as_text.ids == result.ids


# --- the absent client -------------------------------------------------------


@pytest.mark.parametrize("builder", PERSON_BUILDERS)
async def test_every_person_builder_raises_when_tmdb_is_not_configured(builder):
    with pytest.raises(TmdbBuilderRefused, match="TMDb"):
        await REGISTRY[builder].build(_ctx(SourceClients(), id=4242))


# --- the person's biography and photo ----------------------------------------


@pytest.mark.parametrize("builder", PERSON_BUILDERS)
async def test_a_person_builder_takes_its_summary_and_poster_from_the_person(builder):
    """The first item off the 8c hard stop. The biography is the collection's
    summary and the TMDb profile photo is its poster -- for all five names,
    because all five are one build path and a person's own record has nothing to
    do with which credits the role table keeps."""
    result = await _build(builder, _both())

    assert result.summary.startswith("A constructed biography.")
    assert result.poster_kind == TMDB_PROFILE_KIND
    assert result.poster_key == "/a-constructed-profile-path.jpg"


async def test_the_poster_kind_is_the_one_the_poster_module_dispatches_on():
    """Producer and consumer, pinned to one constant. Two spellings of
    ``"tmdb_profile"`` would not fail anything: ``apply_poster`` would fall
    through to ``hosted_poster_url``, get ``None``, and report "no poster
    source" forever."""
    result = await _build("tmdb_actor", _both())

    assert tmdb_profile_url(result.poster_key) is not None


async def test_a_person_with_no_biography_and_no_photo_offers_neither():
    """Data, not failure. The definition's own ``summary:`` is still how an
    operator sets one, and a collection with no poster simply keeps none."""
    routes = _both() | {PERSON_DETAIL: _detail(biography="", profile_path=None)}
    result = await _build("tmdb_actor", routes)

    assert result.ids
    assert result.summary is None
    assert (result.poster_kind, result.poster_key) == (None, None)


async def test_a_dead_person_record_leaves_the_membership_built(caplog):
    """The containment, and it is the asymmetry this build path is built
    around. The credits ARE the membership, so a failure there raises -- an
    empty list one layer down means "remove every member". A biography and a
    poster are cosmetic, so the second endpoint being down must leave the
    collection reconciled and its artwork untouched, not fail the definition."""
    routes = {
        MOVIE_CREDITS: load("tmdb_person_movie_credits.json"),
        TV_CREDITS: load("tmdb_person_tv_credits.json"),
    }
    with caplog.at_level(logging.WARNING):
        result = await _build("tmdb_actor", routes)

    assert result.ids
    assert result.summary is None
    assert (result.poster_kind, result.poster_key) == (None, None)
    assert "tmdb_actor" in caplog.text


async def test_a_dead_credits_call_still_fails_the_definition():
    """The other half of the same sentence, pinned so a later hand cannot
    contain both reads with one ``try``."""
    with pytest.raises(TmdbListRefused):
        await _build("tmdb_actor", {PERSON_DETAIL: _detail()})


async def test_the_persons_own_record_is_read_once_per_build():
    """One extra request per definition, not one per credit."""
    seen: list = []
    await _build("tmdb_actor", _both(), seen)

    assert [_path(request) for request in seen].count(PERSON_DETAIL) == 1


async def test_the_persons_record_is_read_after_their_credits():
    """A wrong id fails once, on the membership call, rather than making a
    second doomed request first."""
    seen: list = []
    with pytest.raises(TmdbListRefused):
        await _build("tmdb_actor", {PERSON_DETAIL: _detail()}, seen)

    assert [_path(request) for request in seen] == [MOVIE_CREDITS]
