"""Award dataset handling, and the ceremonies built on it.

Never fetches the real dataset -- MockTransport and committed fixtures.

All but one of those fixtures are real upstream data rather than hand-written:
``ev0000292.yml`` is five ceremony years cut verbatim out of the community
dataset's Golden Globes file, the fourteen ``ev*.yml`` added with the other
ceremonies are three years each cut the same way, and ``event_validation.yml``
is the sixteen events this service knows cut out of the dataset's own
validation list. All of it is public award data (IMDb title ids and category
names). The Oscars fixture is the older, synthetic one and stays as it is --
the golden gate is recorded against it.

**The trim rule for the fourteen**: the three most recent ceremony years, taken
in the event file's own descending order, in which that ceremony's Kometa
filters resolve at least one *winner*. Three years is what the coverage
assertion below needs and no more; the "resolves a winner" clause is what
skips the four years that would otherwise have made the cut and carried
nothing (Emmys 2026, Venice 2026 and TIFF 2026 have nominees but no recorded
winner yet, and People's Choice 2024 awarded nothing this collection's
categories name).
"""
import inspect
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest

from autoposter.collections.awards import (
    BEST_DIRECTOR,
    BEST_PICTURE,
    GOLDEN_GLOBES_BEST_DIRECTOR,
    GOLDEN_GLOBES_BEST_PICTURE,
    UnknownAwardEvent,
    fetch_event,
    fetch_event_validation,
    recent_years,
    require_known_event,
    uncovered_categories,
    winners_for_categories,
    winners_for_year,
)
from autoposter.collections.builders import REGISTRY, BuilderContext
from autoposter.collections.builders.base import LibraryTypeMismatch
from autoposter.collections.builders.imdb_award import (
    EVENTS,
    Award,
    ImdbAwardBuilder,
    ImdbAwardParams,
    ImdbAwardYearsBuilder,
    _event,
)
from autoposter.collections.engine import definition_titles
from autoposter.collections.posters import hosted_poster_url
from autoposter.config.schema import CollectionDefinition, CollectionsConfig
from autoposter.providers.cache import ProviderCache

FIXTURES = Path("tests/fixtures/collections")
FIXTURE = (FIXTURES / "ev0000003.yml").read_text(encoding="utf-8")
MIXED_KEYS_FIXTURE = (FIXTURES / "ev0000003_mixed_year_keys.yml").read_text(encoding="utf-8")
GLOBES_FIXTURE = (FIXTURES / "ev0000292.yml").read_text(encoding="utf-8")
VALIDATION_FIXTURE = (FIXTURES / "event_validation.yml").read_text(encoding="utf-8")

# One fixture per registered ceremony, keyed by the id the builder asks for.
# Derived from ``EVENTS`` rather than listed: a ceremony added without its
# dataset cut fails here, at import, instead of in whichever test happened to
# reach for it.
EVENT_FIXTURES = {
    event.event_id: (FIXTURES / ("%s.yml" % event.event_id)).read_text(encoding="utf-8")
    for event in EVENTS.values()
}

BASE = "https://raw.githubusercontent.com/Kometa-Team/IMDb-Awards/master"
VALIDATION_URL = "%s/event_validation.yml" % BASE
EVENTS_URL = "%s/events/" % BASE
OSCARS_URL = "%s/events/ev0000003.yml" % BASE
GLOBES_URL = "%s/events/ev0000292.yml" % BASE


def _client(body=None, status=200):
    def handler(request):
        return httpx.Response(status, text=body if body is not None else FIXTURE)

    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


def _events_client(requests=None):
    """Every registered ceremony and the validation list, each from its own URL.

    Anything else is an error rather than a fixture: a builder that asked for
    the wrong event would otherwise be handed the right answer.
    """
    def handler(request):
        url = str(request.url)
        if requests is not None:
            requests.append(url)
        if url == VALIDATION_URL:
            return httpx.Response(200, text=VALIDATION_FIXTURE)
        if url.startswith(EVENTS_URL) and url.endswith(".yml"):
            event_id = url[len(EVENTS_URL):-len(".yml")]
            if event_id in EVENT_FIXTURES:
                return httpx.Response(200, text=EVENT_FIXTURES[event_id])
        raise AssertionError("unexpected request: %s" % url)

    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


def _ctx(http, config=None, run_cache=None, library_type="Movie"):
    return BuilderContext(
        library="Movies", library_type=library_type, http=http,
        config=config or {}, run_cache=run_cache if run_cache is not None else {},
    )


async def test_fetch_event_parses_the_dataset():
    async with _client() as http:
        event = await fetch_event(http)
    assert "2026" in event
    assert event["2026"]["oscar"]["best motion picture of the year"]["winner"] == ["tt31193180"]


async def test_fetch_event_raises_on_an_http_error():
    """An empty result would empty every Oscars collection."""
    async with _client(status=404) as http:
        with pytest.raises(Exception):
            await fetch_event(http)


async def test_fetch_event_validation_is_cached_across_calls(session_factory):
    """Row 151: a second call within the TTL must not hit the transport
    again -- that is the whole complaint the row was filed for, sixteen
    ceremonies' worth of it per pass."""
    calls = []

    def handler(request):
        calls.append(str(request.url))
        return httpx.Response(200, text=VALIDATION_FIXTURE)

    http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    cache = ProviderCache(session_factory)

    first = await fetch_event_validation(http, cache=cache, ttl_seconds=3600)
    second = await fetch_event_validation(http, cache=cache, ttl_seconds=3600)

    assert first == second
    assert len(calls) == 1


async def test_fetch_event_is_cached_across_calls(session_factory):
    calls = []

    def handler(request):
        calls.append(str(request.url))
        return httpx.Response(200, text=FIXTURE)

    http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    cache = ProviderCache(session_factory)

    first = await fetch_event(http, cache=cache, ttl_seconds=3600)
    second = await fetch_event(http, cache=cache, ttl_seconds=3600)

    assert first == second
    assert len(calls) == 1


async def test_fetch_event_validation_without_a_cache_hits_the_transport_every_time():
    """The prior, uncached behaviour stays available -- a direct caller with
    no ProviderCache, exactly as ``BuilderContext.cache``'s own docstring
    says None means."""
    calls = []

    def handler(request):
        calls.append(str(request.url))
        return httpx.Response(200, text=VALIDATION_FIXTURE)

    http = httpx.AsyncClient(transport=httpx.MockTransport(handler))

    await fetch_event_validation(http)
    await fetch_event_validation(http)

    assert len(calls) == 2


async def test_the_oscars_builder_reuses_a_provider_cache_across_separate_passes(session_factory):
    """Row 151's actual complaint: an N-library deployment refetches the same
    398 KB file once per library, every pass. Two separate ``run_cache``
    dicts stand in for two libraries/passes sharing one process-lifetime
    ``ProviderCache`` -- the transport must still be hit once per URL."""
    requests = []
    async with _events_client(requests) as http:
        cache = ProviderCache(session_factory)
        first_pass = _ctx(http, {"award": "best_picture"}, run_cache={})
        second_pass = _ctx(http, {"award": "best_picture"}, run_cache={})
        first_pass = replace(first_pass, cache=cache)
        second_pass = replace(second_pass, cache=cache)

        await ImdbAwardBuilder().build(first_pass)
        await ImdbAwardBuilder().build(second_pass)

    assert requests.count(VALIDATION_URL) == 1
    assert requests.count(OSCARS_URL) == 1


async def test_recent_years_skips_empty_placeholder_years():
    """The live dataset carries '2027': {} for a ceremony that has not
    happened. Including it would create an empty Oscars Winners 2027."""
    async with _client() as http:
        event = await fetch_event(http)
    assert recent_years(event, count=5) == ["2026", "2025", "2024", "2023", "2022"]


async def test_recent_years_is_newest_first():
    async with _client() as http:
        event = await fetch_event(http)
    years = recent_years(event, count=3)
    assert years == sorted(years, reverse=True)


async def test_best_picture_winners_span_category_renames():
    """'best motion picture of the year' and 'best picture' are the same
    award under names used in different eras."""
    async with _client() as http:
        event = await fetch_event(http)
    winners = winners_for_categories(event, BEST_PICTURE)
    assert "tt31193180" in winners  # 2026, "best motion picture of the year"
    assert "tt2000001" in winners  # 2024, "best picture"


async def test_best_director_winners_exclude_other_categories():
    async with _client() as http:
        event = await fetch_event(http)
    winners = winners_for_categories(event, BEST_DIRECTOR)
    assert "tt30144839" in winners
    assert "tt31193180" not in winners


async def test_nominees_are_never_included():
    async with _client() as http:
        event = await fetch_event(http)
    winners = winners_for_categories(event, BEST_PICTURE)
    assert "tt30144839" not in winners  # a 2026 best-picture nominee, not the winner


async def test_winners_are_newest_year_first():
    async with _client() as http:
        event = await fetch_event(http)
    winners = winners_for_categories(event, BEST_PICTURE)
    assert winners.index("tt31193180") < winners.index("tt1000003")


async def test_winners_for_year_returns_every_category():
    async with _client() as http:
        event = await fetch_event(http)
    assert sorted(winners_for_year(event, "2026")) == ["tt30144839", "tt31193180"]


async def test_an_unquoted_year_in_the_dataset_does_not_crash_the_run():
    """One unquoted key parses as an int, and ``sorted`` over a mix of int
    and str raises TypeError -- outside the caller's try, taking the whole
    run down. 2025 and 2023 are unquoted in this fixture."""
    async with _client(body=MIXED_KEYS_FIXTURE) as http:
        event = await fetch_event(http)

    assert 2025 in event and "2024" in event, "the fixture must mix int and str keys"
    assert recent_years(event, count=5) == ["2026", "2025", "2024", "2023", "2022"]
    assert winners_for_categories(event, BEST_PICTURE)[0] == "tt31193180"


async def test_an_unquoted_year_is_still_looked_up_by_its_string_key():
    """``recent_years`` hands back str years, so ``winners_for_year`` must
    find the int-keyed ones too -- otherwise that year silently resolves to
    no winners and its collection is left empty."""
    async with _client(body=MIXED_KEYS_FIXTURE) as http:
        event = await fetch_event(http)

    for year in recent_years(event, count=5):
        assert winners_for_year(event, year), "no winners resolved for %r" % year
    assert sorted(winners_for_year(event, "2025")) == ["tt1000002", "tt1000003"]


async def test_duplicates_are_removed_keeping_first_occurrence():
    event = {
        "2026": {"oscar": {"best picture": {"winner": ["ttX"]},
                           "best motion picture of the year": {"winner": ["ttX"]}}},
    }
    assert winners_for_categories(event, BEST_PICTURE) == ["ttX"]


# --------------------------------------------------------------------------
# Live drift detection over category vocabularies (roadmap row 153, the
# open half: a runtime guard riding data already fetched, not a new fetch).
# --------------------------------------------------------------------------

async def test_uncovered_categories_is_empty_when_every_category_appears():
    # Not BEST_PICTURE itself: the committed Oscars fixture is the older,
    # synthetic one (module docstring above) and only ever uses two of its
    # four historical category names -- the other two ("best picture,
    # production" / "...unique and artistic production") are 1930s Academy
    # names this fixture's five years never reach. Testing against the
    # categories the fixture actually carries is what "every category
    # appears" means here; BEST_PICTURE itself is exercised for drift by
    # the next test below.
    async with _client() as http:
        event = await fetch_event(http)
    covered = ("best motion picture of the year", "best picture")
    assert uncovered_categories(event, covered) == ()


async def test_uncovered_categories_names_the_ones_that_never_appear():
    async with _client() as http:
        event = await fetch_event(http)
    drifted = ("best picture", "a renamed category nobody transcribed")
    assert uncovered_categories(event, drifted) == (
        "a renamed category nobody transcribed",
    )


async def test_uncovered_categories_ignores_a_category_with_zero_winners_this_year():
    """Not covered != has a winner. A category present in the data with an
    empty winner list some year is ordinary; only the NAME never appearing
    anywhere is drift."""
    event = {"2026": {"oscar": {"best picture": {"nominee": ["ttX"], "winner": []}}}}
    assert uncovered_categories(event, ("best picture",)) == ()


async def test_uncovered_categories_respects_the_award_group_filter():
    """A category name that exists, but only under a group the award_filter
    excludes, still counts as uncovered for THIS award -- the group filter
    narrows what "appears" means, exactly as it narrows winners_for_categories."""
    event = {"2026": {"other_group": {"best picture": {"winner": ["ttX"]}}}}
    assert uncovered_categories(event, ("best picture",), award_filter=("oscar",)) == (
        "best picture",
    )


async def test_uncovered_categories_is_empty_for_a_ceremony_with_no_category_filter():
    """The four ceremonies with ``categories=None`` (Berlinale, Cannes,
    Sundance, the National Film Registry) have nothing named to have
    drifted."""
    event = {"2026": {"oscar": {"best picture": {"winner": ["ttX"]}}}}
    assert uncovered_categories(event, None) == ()


async def test_a_drifted_category_logs_a_warning_and_still_builds(caplog):
    """The BINDING shape (p-tails2-facts.md C1): WARNING to the pod log, not
    a raise -- the collection still builds with whatever it DID match, so one
    renamed category does not take an otherwise-healthy ceremony's whole
    build down. Proven by mutating the registry's category tuple against the
    pinned fixture, never against live IMDb (conftest.py's no_outbound_network
    fixture forbids that transport)."""
    import logging

    event = EVENTS["oscars"]
    award = event.awards["best_picture"]
    drifted_award = award._replace(
        categories=award.categories + ("a category imdb quietly renamed",)
    )
    drifted_event = replace(event, awards={**event.awards, "best_picture": drifted_award})

    with caplog.at_level(logging.WARNING):
        async with _events_client() as http:
            from unittest.mock import patch

            with patch.dict(EVENTS, {"oscars": drifted_event}):
                result = await ImdbAwardBuilder().build(
                    _ctx(http, {"event": "oscars", "award": "best_picture"})
                )

    assert result.ids, "the real categories still resolved winners"
    assert "a category imdb quietly renamed" in caplog.text
    assert "oscars" in caplog.text.lower() or event.name in caplog.text


# --------------------------------------------------------------------------
# The award group -- Kometa's outer filter (roadmap row 149).
# --------------------------------------------------------------------------
#
# One ceremony's file can hold several award *groups*: BAFTA (``ev0000123``)
# splits film, television and games under one event id. Kometa filters twice,
# group then category (``modules/imdb.py`` ``_award``, transcribed in
# ``.superpowers/sdd/archive/p8c-task-4-report.md``); until now this service
# read every group. The fixture below gives two groups the *same* category
# name, so a filter that silently did nothing would show up as extra ids
# rather than as the same answer.

TWO_GROUP_EVENT = {
    "2026": {
        "bafta film award": {
            "best film": {"winner": ["ttFilm2026"], "nominee": ["ttFilmNominee"]}
        },
        "bafta tv award": {"best film": {"winner": ["ttTv2026"]}},
    },
    "2025": {
        "bafta film award": {"best film": {"winner": ["ttFilm2025"]}},
        "bafta tv award": {"best film": {"winner": ["ttTv2025"]}},
    },
}


def test_an_award_filter_keeps_only_its_own_group():
    """Both directions, because a filter that matched nothing and a filter
    that matched everything would each pass one of them alone."""
    assert winners_for_categories(
        TWO_GROUP_EVENT, ("best film",), ("bafta film award",)
    ) == ["ttFilm2026", "ttFilm2025"]
    assert winners_for_categories(
        TWO_GROUP_EVENT, ("best film",), ("bafta tv award",)
    ) == ["ttTv2026", "ttTv2025"]


def test_no_award_filter_reads_every_group_in_the_datasets_own_order():
    """The default is the behaviour every shipped collection already has:
    every group, newest year first, in the order the file lists them. The
    Oscars and Golden Globes oracle pins below say the same thing about real
    data; this says it about a file that has more than one group to get wrong.
    """
    unfiltered = winners_for_categories(TWO_GROUP_EVENT, ("best film",))

    assert unfiltered == ["ttFilm2026", "ttTv2026", "ttFilm2025", "ttTv2025"]
    assert winners_for_categories(TWO_GROUP_EVENT, ("best film",), None) == unfiltered


def test_the_two_filters_are_not_one_filter():
    """Group and category narrow independently: the right group with a
    category it does not award is empty, not that group's other winners."""
    assert winners_for_categories(
        TWO_GROUP_EVENT, ("best director",), ("bafta film award",)
    ) == []
    assert winners_for_categories(TWO_GROUP_EVENT, ("best film",), ("bafta games award",)) == []


def test_a_year_collection_reads_every_award_group():
    """And has no way not to, deliberately.

    ``winners_for_year`` briefly took an ``award_filter`` of its own. Nothing
    ever passed one, and nothing upstream wants to: across the sixteen
    ``defaults/award/*.yml`` files Kometa ships, ``award_filter`` appears only
    under ``collections:``, never under ``dynamic_collections:``. So the year
    collections of a multi-medium ceremony carry that year's television
    winners next to its film ones -- Kometa's own behaviour -- and the gate
    that keeps those collections off the wrong library is
    ``AwardEvent.library_types``, not a filter here. The parameter was removed
    rather than carried for a caller that was never going to arrive.
    """
    assert winners_for_year(TWO_GROUP_EVENT, "2026") == ["ttFilm2026", "ttTv2026"]

    # By parameter NAME rather than by arity. This guard used to be a
    # positional call in ``pytest.raises(TypeError)``, which a keyword-only
    # reintroduction -- ``winners_for_year(event, year, *, award_filter=None)``
    # -- would have satisfied: the positional call still raises, so the
    # parameter could come back with the guard staying green.
    assert "award_filter" not in inspect.signature(winners_for_year).parameters


async def test_the_static_builder_applies_its_awards_group_filter(monkeypatch):
    """The filter has to travel from the registry row to the resolver. The
    Oscars fixture's one group is ``oscar``: an award filtered to it builds
    exactly what the unfiltered Oscars collection builds, and the same award
    filtered to a group this ceremony does not have builds nothing -- which is
    a filter that is being read rather than a default that is being ignored.
    """
    monkeypatch.setitem(EVENTS, "filtered_to_its_own_group", replace(
        EVENTS["oscars"], key="filtered_to_its_own_group",
        awards={"best_picture": Award(
            "Filtered Best Picture", BEST_PICTURE, None, None, ("oscar",)
        )},
    ))
    monkeypatch.setitem(EVENTS, "filtered_to_a_missing_group", replace(
        EVENTS["oscars"], key="filtered_to_a_missing_group",
        awards={"best_picture": Award(
            "Missing Best Picture", BEST_PICTURE, None, None, ("bafta film award",)
        )},
    ))

    async with _events_client() as http:
        unfiltered = await ImdbAwardBuilder().build(_ctx(http, {"award": "best_picture"}))
        matching = await ImdbAwardBuilder().build(
            _ctx(http, {"event": "filtered_to_its_own_group", "award": "best_picture"})
        )
        missing = await ImdbAwardBuilder().build(
            _ctx(http, {"event": "filtered_to_a_missing_group", "award": "best_picture"})
        )

    assert matching.ids == unfiltered.ids
    assert missing.ids == []


# --------------------------------------------------------------------------
# The event validation list: which ceremonies the dataset actually carries.
# --------------------------------------------------------------------------


async def test_every_event_this_service_offers_is_in_the_validation_list():
    """The registry's event ids are not free-form: an id the dataset does not
    carry has no file to fetch, and Kometa's answer to that -- scraping IMDb a
    year at a time -- is one this service refuses. So the ids have to be right,
    and this is what says so about the ones we ship."""
    async with _events_client() as http:
        validation = await fetch_event_validation(http)

    for key, event in EVENTS.items():
        assert event.event_id in validation, key


async def test_an_event_the_dataset_does_not_carry_is_refused():
    async with _events_client() as http:
        validation = await fetch_event_validation(http)

    with pytest.raises(UnknownAwardEvent) as error:
        require_known_event(validation, "ev9999999")

    message = str(error.value)
    assert "ev9999999" in message
    assert "scrape" in message, "the refusal has to say what it is refusing to do"


async def test_a_validation_list_that_is_not_a_mapping_is_an_error():
    """An empty or HTML body parses to something falsy, and a falsy validation
    list would refuse every event -- including the shipped Oscars ones -- with
    a message blaming the event id."""
    async with _client(body="not a mapping") as http:
        with pytest.raises(ValueError):
            await fetch_event_validation(http)


async def test_an_unknown_event_is_refused_before_its_file_is_asked_for():
    """The lookup is the point: a 404 on the event file would eventually say
    something similar, but only after asking a server for a file we already
    knew was not there."""
    requests: list[str] = []
    bogus = replace(EVENTS["oscars"], event_id="ev9999999")

    async with _events_client(requests) as http:
        with pytest.raises(UnknownAwardEvent):
            await _event(_ctx(http), bogus)

    assert requests == [VALIDATION_URL], "no event file may be requested for it"


# --------------------------------------------------------------------------
# Two ceremonies in one pass.
# --------------------------------------------------------------------------


async def test_each_ceremony_costs_one_fetch_and_gets_its_own_data():
    """The memo is keyed by event id. Sharing one key across ceremonies would
    hand the second one whichever dataset the first fetched -- a Golden Globes
    collection full of Oscars winners, built without a single failed request
    to notice."""
    requests: list[str] = []
    run_cache: dict = {}

    async with _events_client(requests) as http:
        oscars = await ImdbAwardBuilder().build(
            _ctx(http, {"award": "best_picture"}, run_cache)
        )
        globes = await ImdbAwardBuilder().build(
            _ctx(http, {"event": "golden_globes", "award": "best_picture"}, run_cache)
        )
        again = await ImdbAwardBuilder().build(
            _ctx(http, {"event": "golden_globes", "award": "best_director"}, run_cache)
        )

    assert requests == [VALIDATION_URL, OSCARS_URL, GLOBES_URL], requests
    assert oscars.ids != globes.ids
    assert globes.ids != again.ids
    assert oscars.summary.startswith("The Academy Award for Best Picture")
    assert globes.summary == "Golden Globes Best Picture Winners."


async def test_a_ceremonys_own_pattern_cannot_claim_another_ceremonys_titles():
    """``engine.definition_titles`` reads the pattern off the registry entry,
    so a pattern that matched both ceremonies would tell the leftovers report
    that a Golden Globes definition manages the Oscars year collections. The
    delete sweep reads the same set, in the other direction: a pattern too
    narrow for the ceremony it is registered for reports our own year
    collections as unmanaged, and a swept pass deletes them."""
    oscars = EVENTS["oscars"].year_pattern
    globes = EVENTS["golden_globes"].year_pattern

    assert oscars.match("Oscars Winners 2026")
    assert not oscars.match("Golden Globe 2026")
    assert globes.match("Golden Globe 2026")
    assert not globes.match("Oscars Winners 2026")

    collections = [
        SimpleNamespace(title=title)
        for title in ("Oscars Winners 2026", "Golden Globe 2026", "Golden Globe 1999")
    ]
    globes_only = [CollectionDefinition(
        title="Golden Globe Winners (recent ceremonies)",
        builder="golden_globes_award_years",
    )]

    # Separators off, so the set below is only what the PATTERN claimed: since
    # row 49 ``definition_titles`` also folds in every active group's divider,
    # and an awards heading here would say nothing about pattern isolation.
    config = SimpleNamespace(collections=CollectionsConfig(separators=False))
    assert definition_titles(globes_only, collections, "Movie", config) == {
        "Golden Globe 2026", "Golden Globe 1999",
    }


async def test_every_events_year_pattern_only_matches_its_own_year_title():
    """The test above pins pattern isolation by name, for the two ceremonies
    shipped today. This is the general form, over the registry itself: a
    third ceremony added later without a pattern distinct from the existing
    ones would silently reintroduce the sweep-eats-our-collections bug, and
    this is what would catch it without anyone having to remember to extend
    the named test above."""
    for key, event in EVENTS.items():
        title = event.year_title % "2026"
        for other_key, other in EVENTS.items():
            if other_key == key:
                assert other.year_pattern.match(title), key
            else:
                assert not other.year_pattern.match(title), (key, other_key)


async def test_every_events_years_builder_is_registered():
    """``years_builder`` names the registry entry that expands this
    ceremony's year collections; an event pointing at a builder that was
    never registered would fail silently at expand-time rather than here."""
    for key, event in EVENTS.items():
        assert event.years_builder in REGISTRY, key


async def test_each_ceremony_expands_into_its_own_titles_and_summaries():
    """Kometa's ``title_format`` and its ``golden_year`` translation, both
    transcribed: the year collections are "Golden Globe 2026", and the summary
    is the ceremony's own rather than the Oscars sentence with a word swapped.
    """
    async with _events_client() as http:
        units = await REGISTRY["golden_globes_award_years"].expand(_ctx(http))

    assert [unit.title for unit in units] == [
        "Golden Globe 2026", "Golden Globe 2025", "Golden Globe 2024",
        "Golden Globe 2023", "Golden Globe 2022",
    ]
    assert units[0].summary == "2026 Golden Globe Winners."
    assert units[0].builder == "golden_globes_award_years"
    assert units[0].sort == "release"


async def test_a_ceremonys_posters_come_from_its_own_folder():
    """``golden_globes`` is Kometa's ``golden`` folder -- the mapping is
    transcribed, because deriving the path from the event key would be a
    guess, and a guess that resolved would be the wrong ceremony's artwork."""
    async with _events_client() as http:
        static = await ImdbAwardBuilder().build(
            _ctx(http, {"event": "golden_globes", "award": "best_director"})
        )
        year = await REGISTRY["golden_globes_award_years"].build(
            _ctx(http, {"year": "2026"})
        )

    assert static.poster_key == "golden_globes:best_director_winner"
    assert hosted_poster_url(static.poster_kind, static.poster_key) == (
        "https://raw.githubusercontent.com/Kometa-Team/Default-Images/master"
        "/award/golden/best_director_winner.jpg"
    )
    assert year.poster_key == "golden_globes:2026"
    assert hosted_poster_url(year.poster_kind, year.poster_key) == (
        "https://raw.githubusercontent.com/Kometa-Team/Default-Images/master"
        "/award/golden/winner/2026.jpg"
    )


def test_an_award_is_only_known_within_its_ceremony():
    ImdbAwardParams.model_validate({"award": "best_picture"})
    ImdbAwardParams.model_validate({"event": "golden_globes", "award": "best_picture"})

    # A real ceremony the community dataset covers (``ev0000245``) but Kometa
    # ships no award default for, so this service has no row for it either --
    # 15 of the dataset's 31 events are in that position.
    with pytest.raises(ValueError, match="unknown award event 'filmfare'"):
        ImdbAwardParams.model_validate({"event": "filmfare", "award": "best_picture"})
    with pytest.raises(ValueError, match="unknown Golden Globes award 'best_song'"):
        ImdbAwardParams.model_validate({"event": "golden_globes", "award": "best_song"})
    with pytest.raises(ValueError, match="unknown Oscars award 'best_song'"):
        ImdbAwardParams.model_validate({"award": "best_song"})


# --------------------------------------------------------------------------
# Which libraries a ceremony can mean anything for (roadmap row 150).
# --------------------------------------------------------------------------
#
# A definition with no ``libraries:`` key runs against every library in the
# pass, so an award definition reaches Show libraries too, where a film
# ceremony's ids resolve to nothing at all -- indistinguishable from a
# ceremony that had no winners. ``require_library_type`` is the same guard the
# charts and the TMDb builders already carry; the ceremony declares its own
# types because that is what differs between them.


def test_each_ceremonys_library_types_are_the_media_it_awards():
    """A checksum over the whole table, so a row added with the default
    ``("Movie",)`` because nobody looked at its ``allowed_libraries`` shows up
    here rather than as a Show library quietly building nothing.

    The four that are not Movie-only, and why (Kometa's own
    ``defaults/award/*.yml``, fetched 2026-08-26):

    - ``emmy`` -- ``allowed_libraries: show``; the one television-only row.
    - ``choice``, ``pca``, ``sag`` -- ceremonies that award both. Kometa sets
      no ``allowed_libraries`` on their year blocks, and the People's Choice
      and Screen Actors Guild collections' own category lists mix film and
      television outright ("favorite movie" beside "favorite tv show").
    """
    both = ("Movie", "Show")
    assert EVENTS["emmy"].library_types == ("Show",)
    assert EVENTS["choice"].library_types == both
    assert EVENTS["pca"].library_types == both
    assert EVENTS["sag"].library_types == both
    assert {key for key, event in EVENTS.items() if event.library_types == ("Movie",)} == {
        "oscars", "golden_globes", "bafta", "berlinale", "cannes", "cesar",
        "nfr", "razzie", "spirit", "sundance", "tiff", "venice",
    }


def test_the_two_shipped_ceremonies_are_movie_ceremonies():
    """Pinned rather than assumed: the Golden Globes award television and
    their *year* collections carry those ids (Kometa's dynamic year block sets
    no ``allowed_libraries`` and we match it), but the ceremony is still a film
    ceremony -- the ids land in a Movie library, and the gate is what keeps the
    definitions off Show libraries entirely."""
    assert EVENTS["oscars"].library_types == ("Movie",)
    assert EVENTS["golden_globes"].library_types == ("Movie",)


async def test_a_movie_ceremony_is_refused_on_a_show_library_before_any_fetch():
    """Before, not after: the refusal is worth nothing if the pass has already
    spent the request. The transport asserts on any request at all here."""
    requests: list[str] = []

    async with _events_client(requests) as http:
        with pytest.raises(LibraryTypeMismatch, match="Movie"):
            await ImdbAwardBuilder().build(
                _ctx(http, {"award": "best_picture"}, library_type="Show")
            )

    assert requests == [], "a refused definition must not fetch the dataset"


async def test_the_year_collections_are_refused_on_a_show_library_before_any_fetch():
    """The expanding builder is gated in ``expand`` rather than in ``build``:
    that is the earliest point that has a library type, it is above the fetch,
    and ``build`` is only ever reached through units ``expand`` returned
    (``engine._expand`` :188-190, ``engine._run_one`` :431)."""
    requests: list[str] = []

    async with _events_client(requests) as http:
        with pytest.raises(LibraryTypeMismatch, match="Movie"):
            await REGISTRY["imdb_award_years"].expand(_ctx(http, library_type="Show"))

    assert requests == [], "a refused definition must not fetch the dataset"


async def test_a_show_ceremony_builds_on_a_show_library(monkeypatch):
    """The gate is the ceremony's own declaration, not a hardcoded "Movie":
    an Emmys-shaped event -- everything else identical -- builds on Show and is
    refused on Movie, which is the inverse of the two tests above."""
    monkeypatch.setitem(EVENTS, "emmys_shaped", replace(
        EVENTS["oscars"], key="emmys_shaped", library_types=("Show",)
    ))

    async with _events_client() as http:
        static = await ImdbAwardBuilder().build(
            _ctx(http, {"event": "emmys_shaped", "award": "best_picture"},
                 library_type="Show")
        )
        units = await ImdbAwardYearsBuilder("emmys_shaped").expand(
            _ctx(http, library_type="Show")
        )
        with pytest.raises(LibraryTypeMismatch, match="Show"):
            await ImdbAwardBuilder().build(
                _ctx(http, {"event": "emmys_shaped", "award": "best_picture"})
            )

    assert static.ids, "a Show ceremony must resolve on a Show library"
    assert units, "and its year collections must expand there"


# --------------------------------------------------------------------------
# The Golden Globes' own category vocabulary.
# --------------------------------------------------------------------------


async def test_the_globes_top_award_spans_its_genre_split_categories():
    """The ceremony has no single "best picture": it awards drama, musical or
    comedy, animated and non-English separately, and has renamed each branch
    repeatedly. A vocabulary missing one of those silently drops that year's
    winner from the collection."""
    async with _events_client() as http:
        event = await fetch_event(http, "ev0000292")

    winners = winners_for_categories(event, GOLDEN_GLOBES_BEST_PICTURE)

    assert "tt14905854" in winners, "2026 drama"
    assert "tt30144839" in winners, "2026 musical or comedy"
    assert "tt14205554" in winners, "2026 animated"
    assert "tt27847051" in winners, "2026 non-english language"
    assert "tt1312221" not in winners, "a nominee, not a winner"


async def test_the_globes_director_award_is_not_the_oscars_one():
    """'best director - motion picture' is the Golden Globes' spelling; the
    Oscars' vocabulary has 'best achievement in directing' and never that."""
    async with _events_client() as http:
        event = await fetch_event(http, "ev0000292")

    assert winners_for_categories(event, GOLDEN_GLOBES_BEST_DIRECTOR) == [
        "tt30144839", "tt8999762", "tt15398776", "tt14208870", "tt10293406",
    ]
    assert winners_for_categories(event, BEST_DIRECTOR) == []


# --------------------------------------------------------------------------
# THE ORACLE.
# --------------------------------------------------------------------------
#
# The lists below are Kometa's, not ours. They were produced by transcribing
# Kometa's own award resolution -- ``modules/imdb.py`` ``_award`` (fetched
# 2026-08-25 from Kometa-Team/Kometa@master, the git-dataset branch of it) --
# into a standalone script, feeding it Kometa's own configuration for this
# ceremony (``defaults/award/golden.yml``: ``event_id: ev0000292``,
# ``winning: true``, ``event_year: all``, and its ``category_filter`` lists)
# and the upstream ``events/ev0000292.yml``, then dedup­ing the result
# first-occurrence-first (Kometa appends per category and lets Plex collapse
# repeats; we dedupe in ``_dedupe``). Nothing from this repository ran in that
# script. The full procedure, with the transcript, is in
# ``.superpowers/sdd/task-4-report.md``.
#
# ``ORACLE_GOLDEN_GLOBE_2026`` is an unrestricted comparison: a year
# collection reads one year, and this is every winner Kometa lists for
# ev0000292/2026. The two static lists are the same computation restricted to
# the five ceremony years the committed fixture carries, because that is the
# slice of the dataset this test can hold.

ORACLE_GOLDEN_GLOBE_2026 = [
    "tt30144839", "tt14205554", "tt14905854", "tt27847051", "tt31193180",
    "tt30252752", "tt18382850", "tt31806037", "tt22202452", "tt11815682",
    "tt32916440", "tt27714581", "tt31938062", "tt23649128", "tt32767869",
    "tt35684666",
]
ORACLE_GOLDEN_GLOBES_BEST_PICTURE = [
    "tt14205554", "tt14905854", "tt30144839", "tt27847051", "tt4772188",
    "tt8999762", "tt20221436", "tt6587046", "tt15398776", "tt17009710",
    "tt14230458", "tt1488589", "tt14208870", "tt11813216", "tt15301048",
    "tt2953050", "tt10293406", "tt3581652", "tt14039582",
]
ORACLE_GOLDEN_GLOBES_BEST_DIRECTOR = [
    "tt30144839", "tt8999762", "tt15398776", "tt14208870", "tt10293406",
]


async def test_the_globes_year_collection_matches_kometas_members():
    async with _events_client() as http:
        result = await REGISTRY["golden_globes_award_years"].build(
            _ctx(http, {"year": "2026"})
        )

    assert [imdb_id for _, imdb_id in result.ids] == ORACLE_GOLDEN_GLOBE_2026
    assert all(namespace == "imdb" for namespace, _ in result.ids)


@pytest.mark.parametrize(
    "award,oracle",
    [
        ("best_picture", ORACLE_GOLDEN_GLOBES_BEST_PICTURE),
        ("best_director", ORACLE_GOLDEN_GLOBES_BEST_DIRECTOR),
    ],
)
async def test_the_globes_winner_collections_match_kometas_members(award, oracle):
    async with _events_client() as http:
        result = await ImdbAwardBuilder().build(
            _ctx(http, {"event": "golden_globes", "award": award})
        )

    assert [imdb_id for _, imdb_id in result.ids] == oracle


# --------------------------------------------------------------------------
# All sixteen ceremonies: the category coverage assertion (roadmap row 153).
# --------------------------------------------------------------------------
#
# The failure this catches is the quiet one. A category vocabulary that names
# nothing the dataset has -- a typo, a rename we transcribed from the wrong
# ceremony, an award group spelled the way IMDb prints it rather than the way
# the dataset keys it -- produces a collection with no members, which is
# indistinguishable from a ceremony that had no winners. Nothing else in this
# file would notice it for fourteen of the sixteen rows.
#
# One case per (ceremony, award), derived from ``EVENTS``, so a row added
# later is covered without anyone extending a list.

AWARD_CASES = [
    (key, award_key)
    for key, event in sorted(EVENTS.items())
    for award_key in sorted(event.awards)
]


@pytest.mark.parametrize("key,award_key", AWARD_CASES)
async def test_every_awards_filters_resolve_winners_in_its_own_dataset(key, award_key):
    """Built end to end rather than by calling the resolver directly: this way
    the award group, the category vocabulary, the event id, the library gate
    and the poster key all have to be right together, which is the same set of
    things a row gets wrong."""
    event = EVENTS[key]

    async with _events_client() as http:
        result = await ImdbAwardBuilder().build(
            _ctx(http, {"event": key, "award": award_key},
                 library_type=event.library_types[0])
        )

    assert result.ids, "%s/%s resolved no winners at all" % (key, award_key)
    assert all(namespace == "imdb" for namespace, _ in result.ids)


@pytest.mark.parametrize("key", sorted(EVENTS))
async def test_every_ceremonys_year_collections_expand_and_build(key):
    """The other half of a ceremony. An event whose year block is wrong -- a
    title format that cannot be filled, a builder name nothing registered, a
    dataset whose recent years are all empty -- fails here."""
    event = EVENTS[key]
    library_type = event.library_types[0]

    async with _events_client() as http:
        units = await REGISTRY[event.years_builder].expand(
            _ctx(http, library_type=library_type)
        )
        first = await REGISTRY[event.years_builder].build(
            _ctx(http, {"year": units[0].params["year"]}, library_type=library_type)
        )

    assert units, key
    assert all(event.year_pattern.match(unit.title) for unit in units), key
    assert first.ids, "%s built an empty year collection" % key


def test_every_ceremony_maps_to_a_default_images_folder():
    """Both kinds of poster resolve to a URL for every ceremony, which is
    exactly two claims and no more: the event has an ``AWARD_SEGMENTS`` row
    (without one ``hosted_poster_url`` returns None for both kinds), and every
    award row carries a ``poster_stem``.

    That the URLs this builds actually EXIST in Default-Images is a different
    claim, and this loop cannot see the repository to make it. It is pinned
    path by path, against the fetched file listing, in
    ``tests/test_collection_posters.py``."""
    for key, event in EVENTS.items():
        assert hosted_poster_url("award_year", "%s:2026" % key), key
        for award_key, award in event.awards.items():
            assert award.poster_stem, (key, award_key)
            assert hosted_poster_url(
                "award_static", "%s:%s" % (key, award.poster_stem)
            ), (key, award_key)


async def test_a_ceremony_with_no_category_filter_reads_every_category():
    """Four ceremonies are configured that way upstream, and Cannes is the
    clearest: its collection *is* the ``palme d'or`` award group, whose
    categories nobody enumerates. ``categories=None`` has to mean "every
    category of the groups that survived", not "no categories" -- the second
    reading builds an empty collection and looks like a quiet ceremony."""
    assert EVENTS["cannes"].awards["palm"].categories is None

    async with _events_client() as http:
        event = await fetch_event(http, "ev0000147")

    everything = winners_for_categories(event, None, ("palme d'or",))
    assert everything
    # ...and it is the group filter doing the narrowing, not nothing at all.
    assert len(winners_for_categories(event, None)) > len(everything)
    # An empty tuple reads every category too, because Kometa's rule is
    # falsiness rather than None-ness (``if data["category_filter"] and ...``).
    # No row spells it that way, but the two must not disagree.
    assert winners_for_categories(event, (), ("palme d'or",)) == everything


# --------------------------------------------------------------------------
# THE THREE ORACLES: BAFTA, the Emmys, Cannes.
# --------------------------------------------------------------------------
#
# Produced exactly as the Golden Globes lists above were, and by the same
# procedure: Kometa's ``modules/imdb.py`` ``_award`` (:1110-1135) and the two
# filter parses of its ``modules/builder.py`` (:2317-2327, ``datatype=
# "lowerlist"``) transcribed into a standalone script, driven with the
# ``imdb_award:`` block of Kometa's OWN ``defaults/award/{bafta,emmy,
# cannes}.yml``, over the upstream ``event_validation.yml`` and event files.
# Nothing from this repository ran in that script. Its output is deduped
# first-occurrence-first -- Kometa appends per category and lets Plex collapse
# repeats -- and that is the only adjustment. Full procedure and transcript:
# ``.superpowers/sdd/task-2-report.md``.
#
# Why these three: the Emmys are the only Show-gated ceremony; Cannes is the
# festival shape, an ``award_filter`` and no ``category_filter`` at all; BAFTA
# is the multi-group event (film, television, games and three more under one
# id) that Kometa does NOT filter by group, so it is the row where reading
# every group is the correct answer rather than the lazy one.
#
# The static lists are restricted to the three ceremony years each committed
# fixture carries; the unrestricted counts are recorded beside them so the
# size of what is not pinned is visible rather than implied. The year lists
# are unrestricted: a year collection reads exactly one ceremony year.

ORACLE_BAFTA_BEST_FILMS = ["tt30144839", "tt20215234", "tt15398776"]  # all years: 79
ORACLE_BAFTA_2026 = [
    "tt1757678", "tt26443597", "tt27449033", "tt35875811", "tt31514146",
    "tt33035197", "tt30144839", "tt1312221", "tt34965515", "tt27714581",
    "tt14905854", "tt31193180", "tt16311594", "tt35695538", "tt32410998",
    "tt13684264", "tt27568682", "tt11608566", "tt31190108", "tt21874900",
    "tt37504739", "tt19359688", "tt35407689", "tt31806037", "tt41236384",
    "tt30249123", "tt32750053", "tt37024060", "tt36592872", "tt35050741",
    "tt33248156", "tt35622414", "tt4189570", "tt23649128", "tt41070842",
    "tt32061880", "tt0239164", "tt35916200", "tt19386538", "tt33059458",
    "tt36777907", "tt20449596", "tt29768339", "tt33305711", "tt38235199",
    "tt0088512", "tt9253284", "tt36455190", "tt32918601", "tt13683866",
    "tt5875444", "tt34996965", "tt32591698",
]
ORACLE_EMMY_BEST_IN_CATEGORY = [  # all years: 152
    "tt11126994", "tt23649128", "tt31938062", "tt13309742", "tt11815682",
    "tt2788316", "tt0096697", "tt14452776", "tt7660850",
]
ORACLE_EMMY_2025 = [
    "tt30342963", "tt11126994", "tt23649128", "tt31938062", "tt31806037",
    "tt19037550", "tt8740790", "tt27613329", "tt34510105", "tt14126234",
    "tt15557874", "tt11280740", "tt37593384", "tt15435876", "tt35144655",
    "tt14419140", "tt5875444", "tt3697842", "tt35469118", "tt9253284",
    "tt0159881", "tt11815682", "tt12057284", "tt27790101", "tt9561862",
    "tt38203330", "tt0072562", "tt35984846", "tt33332486", "tt33354100",
    "tt23181400", "tt13406094", "tt1190634", "tt0115147", "tt11198330",
    "tt13207736", "tt14124236", "tt14674086", "tt34874258", "tt33552770",
    "tt3530232", "tt37594665", "tt3581920", "tt26420234", "tt33986138",
    "tt7259746", "tt8634332", "tt12759100", "tt11301886", "tt35445387",
    "tt36986906",
]
ORACLE_CANNES_GOLDEN_PALM = ["tt35410859", "tt36491653", "tt28607951"]  # all years: 80
ORACLE_CANNES_2026 = [
    "tt35410859", "tt37118301", "tt21188986", "tt39328391", "tt36834996",
    "tt35511966", "tt37304295", "tt41593304", "tt38820979", "tt42006686",
    "tt41592057", "tt36639956", "tt29279937", "tt41592086", "tt41592077",
    "tt38765198", "tt39172310", "tt42082777", "tt38841455", "tt32459280",
    "tt34459901", "tt42004508", "tt35298123", "tt38468551", "tt38991615",
    "tt42029134", "tt41593500", "tt42053546", "tt36822560", "tt30495381",
    "tt39121543", "tt35495082",
]


@pytest.mark.parametrize(
    "event,award,library_type,oracle",
    [
        ("bafta", "best", "Movie", ORACLE_BAFTA_BEST_FILMS),
        ("emmy", "best", "Show", ORACLE_EMMY_BEST_IN_CATEGORY),
        ("cannes", "palm", "Movie", ORACLE_CANNES_GOLDEN_PALM),
    ],
)
async def test_the_winner_collections_match_kometas_members(
    event, award, library_type, oracle
):
    async with _events_client() as http:
        result = await ImdbAwardBuilder().build(
            _ctx(http, {"event": event, "award": award}, library_type=library_type)
        )

    assert [imdb_id for _, imdb_id in result.ids] == oracle
    assert all(namespace == "imdb" for namespace, _ in result.ids)


@pytest.mark.parametrize(
    "event,year,library_type,oracle",
    [
        ("bafta", "2026", "Movie", ORACLE_BAFTA_2026),
        ("emmy", "2025", "Show", ORACLE_EMMY_2025),
        ("cannes", "2026", "Movie", ORACLE_CANNES_2026),
    ],
)
async def test_the_year_collections_match_kometas_members(
    event, year, library_type, oracle
):
    async with _events_client() as http:
        result = await REGISTRY["%s_award_years" % event].build(
            _ctx(http, {"year": year}, library_type=library_type)
        )

    assert [imdb_id for _, imdb_id in result.ids] == oracle


async def test_baftas_film_categories_belong_to_the_film_group_alone():
    """Why the multi-group ceremony still needs no ``award_filter``.

    ``ev0000123`` holds six award groups in a recent year -- film, television,
    games, children's and two named sponsorships -- so this is the row where
    an unfiltered read *could* pick up another medium's winner. Kometa sets no
    filter here, and this is the fact that makes that safe: the two category
    names it does filter on occur only inside ``bafta film award``. If a
    television category were ever spelled "best film", the first two
    assertions would stop agreeing and the oracle above would move with them.
    """
    assert EVENTS["bafta"].awards["best"].award_filter is None
    categories = EVENTS["bafta"].awards["best"].categories

    async with _events_client() as http:
        event = await fetch_event(http, "ev0000123")

    assert winners_for_categories(event, categories) == ORACLE_BAFTA_BEST_FILMS
    assert winners_for_categories(
        event, categories, ("bafta film award",)
    ) == ORACLE_BAFTA_BEST_FILMS
    assert winners_for_categories(event, categories, ("bafta tv award",)) == []
