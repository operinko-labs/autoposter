"""Award dataset handling, and the ceremonies built on it.

Never fetches the real dataset -- MockTransport and committed fixtures.

Two of those fixtures are real upstream data rather than hand-written:
``ev0000292.yml`` is five ceremony years cut verbatim out of the community
dataset's Golden Globes file, and ``event_validation.yml`` is the two events
this service knows cut out of the dataset's own validation list. Both are
public award data (IMDb title ids and category names). The Oscars fixture is
the older, synthetic one and stays as it is -- the golden gate is recorded
against it.
"""
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
    winners_for_categories,
    winners_for_year,
)
from autoposter.collections.builders import REGISTRY, BuilderContext
from autoposter.collections.builders.imdb_award import (
    EVENTS,
    ImdbAwardBuilder,
    ImdbAwardParams,
    _event,
)
from autoposter.collections.engine import definition_titles
from autoposter.collections.posters import hosted_poster_url
from autoposter.config.schema import CollectionDefinition

FIXTURE = Path("tests/fixtures/collections/ev0000003.yml").read_text(encoding="utf-8")
MIXED_KEYS_FIXTURE = Path(
    "tests/fixtures/collections/ev0000003_mixed_year_keys.yml"
).read_text(encoding="utf-8")
GLOBES_FIXTURE = Path("tests/fixtures/collections/ev0000292.yml").read_text(encoding="utf-8")
VALIDATION_FIXTURE = Path(
    "tests/fixtures/collections/event_validation.yml"
).read_text(encoding="utf-8")

BASE = "https://raw.githubusercontent.com/Kometa-Team/IMDb-Awards/master"
VALIDATION_URL = "%s/event_validation.yml" % BASE
OSCARS_URL = "%s/events/ev0000003.yml" % BASE
GLOBES_URL = "%s/events/ev0000292.yml" % BASE


def _client(body=None, status=200):
    def handler(request):
        return httpx.Response(status, text=body if body is not None else FIXTURE)

    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


def _events_client(requests=None):
    """Both ceremonies and the validation list, each from its own URL.

    Anything else is an error rather than a fixture: a builder that asked for
    the wrong event would otherwise be handed the right answer.
    """
    def handler(request):
        url = str(request.url)
        if requests is not None:
            requests.append(url)
        if url == VALIDATION_URL:
            return httpx.Response(200, text=VALIDATION_FIXTURE)
        if url == OSCARS_URL:
            return httpx.Response(200, text=FIXTURE)
        if url == GLOBES_URL:
            return httpx.Response(200, text=GLOBES_FIXTURE)
        raise AssertionError("unexpected request: %s" % url)

    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


def _ctx(http, config=None, run_cache=None):
    return BuilderContext(
        library="Movies", library_type="Movie", http=http,
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

    assert definition_titles(globes_only, collections, "Movie", None) == {
        "Golden Globe 2026", "Golden Globe 1999",
    }


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

    with pytest.raises(ValueError, match="unknown award event 'cannes'"):
        ImdbAwardParams.model_validate({"event": "cannes", "award": "best_picture"})
    with pytest.raises(ValueError, match="unknown Golden Globes award 'best_song'"):
        ImdbAwardParams.model_validate({"event": "golden_globes", "award": "best_song"})
    with pytest.raises(ValueError, match="unknown Oscars award 'best_song'"):
        ImdbAwardParams.model_validate({"award": "best_song"})


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
