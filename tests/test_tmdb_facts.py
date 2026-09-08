import json
from datetime import date
from pathlib import Path

import httpx
import pytest

from conftest import session_factory_for
from autoposter.facts.tmdb_facts import (
    TMDB_RELEASE_TYPES,
    TMDBFactsClient,
    parse_movie_facts,
    parse_release_date,
    parse_season_episode_ratings,
    parse_show_facts,
)

FIXTURES = Path(__file__).parent / "fixtures" / "facts"


def load(name):
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def test_movie_facts():
    facts = parse_movie_facts(load("tmdb_movie.json"))
    assert facts.audience_rating == pytest.approx(6.3)
    assert facts.genres == ["Horror", "Drama"]
    assert facts.originally_available == date(2023, 5, 12)
    assert facts.sources["audience_rating"] == "tmdb"


def test_movie_studio_is_the_first_production_company():
    """Kometa takes companies[0] with no sorting — match it exactly."""
    assert parse_movie_facts(load("tmdb_movie.json")).studio == "First Studio"


def test_show_studio_is_the_first_network_not_a_production_company():
    facts = parse_show_facts(load("tmdb_show.json"))
    assert facts.studio == "Apple TV+"


def test_show_uses_first_air_date():
    assert parse_show_facts(load("tmdb_show.json")).originally_available == date(2022, 2, 18)


def test_missing_optional_fields_do_not_raise():
    facts = parse_movie_facts({"id": 1})
    assert facts.audience_rating is None
    assert facts.genres == []
    assert facts.studio is None
    assert facts.originally_available is None


def test_empty_company_list_gives_no_studio():
    assert parse_movie_facts({"production_companies": []}).studio is None


def test_genres_with_string_value_returns_empty_list():
    """Malformed input: genres is a string instead of a list."""
    facts = parse_movie_facts({"genres": "oops"})
    assert facts.genres == []


def test_genres_with_mixed_valid_and_invalid_entries():
    """Malformed input: genres list contains a string alongside valid dicts."""
    facts = parse_movie_facts({"genres": [{"name": "Horror"}, "junk", {"no_name": 1}]})
    assert facts.genres == ["Horror"]


def test_malformed_release_date_is_ignored():
    assert parse_movie_facts({"release_date": ""}).originally_available is None
    assert parse_movie_facts({"release_date": "not-a-date"}).originally_available is None


def test_season_episode_ratings_are_keyed_by_episode_number():
    ratings = parse_season_episode_ratings(load("tmdb_season.json"))
    assert ratings[1] == pytest.approx(7.8)
    assert ratings[2] == pytest.approx(8.1)


def test_zero_rating_is_treated_as_absent():
    """TMDB reports 0.0 for unrated episodes; writing that would show '0%'."""
    assert 3 not in parse_season_episode_ratings(load("tmdb_season.json"))


def test_season_episode_ratings_with_string_value_returns_empty_dict():
    """Malformed input: episodes is a string instead of a list."""
    ratings = parse_season_episode_ratings({"episodes": "notalist"})
    assert ratings == {}


def test_season_episode_ratings_with_mixed_valid_and_invalid_entries():
    """Malformed input: episodes list contains a string alongside a valid dict."""
    ratings = parse_season_episode_ratings({
        "episodes": [
            {"episode_number": 1, "vote_average": 7.8},
            "junk",
        ]
    })
    assert ratings == {1: pytest.approx(7.8)}


async def test_client_requests_the_season_endpoint():
    seen = {}

    def handler(request):
        seen["url"] = str(request.url)
        return httpx.Response(200, json=load("tmdb_season.json"))

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        client = TMDBFactsClient("token", http)
        ratings = await client.season_episode_ratings(95396, 2)

    assert "/tv/95396/season/2" in seen["url"]
    assert ratings[1] == pytest.approx(7.8)


async def test_client_sends_a_bearer_token():
    seen = {}

    def handler(request):
        seen["auth"] = request.headers.get("Authorization")
        return httpx.Response(200, json=load("tmdb_movie.json"))

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        await TMDBFactsClient("tok", http).movie(940143)

    assert seen["auth"] == "Bearer tok"


async def test_client_returns_empty_facts_on_404():
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda r: httpx.Response(404, json={}))
    ) as http:
        facts = await TMDBFactsClient("tok", http).movie(1)
    assert facts.is_empty()


async def test_repeated_season_lookups_hit_the_cache_not_the_api(session):
    """A season-pack import asks for one season repeatedly — pay once.

    This is the whole reason episode ratings are affordable per-item: without
    it, importing a 10-episode season means 10 identical TMDB requests.
    """
    from autoposter.providers.cache import ProviderCache

    calls = []

    def handler(request):
        calls.append(str(request.url))
        return httpx.Response(200, json=load("tmdb_season.json"))

    cache = ProviderCache(session_factory_for(session))
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        client = TMDBFactsClient("tok", http, cache=cache, cache_ttl_seconds=3600)
        first = await client.season_episode_ratings(95396, 2)
        second = await client.season_episode_ratings(95396, 2)

    assert first == second
    assert len(calls) == 1, "second lookup should have been served from the cache"


async def test_without_a_cache_every_lookup_hits_the_api():
    calls = []

    def handler(request):
        calls.append(str(request.url))
        return httpx.Response(200, json=load("tmdb_season.json"))

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        client = TMDBFactsClient("tok", http)
        await client.season_episode_ratings(95396, 2)
        await client.season_episode_ratings(95396, 2)

    assert len(calls) == 2


# --- the three prefetch fields (roadmap rows 189/192) -----------------------


def test_movie_facts_carry_the_three_prefetch_fields():
    """The widening, read off the same payload the pipeline already fetches.

    Zero new requests: these three fields ride the ``/movie/{id}`` read that
    already pays for the rating, the genres and the studio. Roadmap rows 189
    and 192 are what they are for.
    """
    facts = parse_movie_facts(load("tmdb_movie.json"))
    assert facts.tmdb_origin_country == ["US"]
    assert facts.tmdb_original_language == "en"
    assert facts.tmdb_collection_id is None
    assert facts.sources["tmdb_origin_country"] == "tmdb"
    assert facts.sources["tmdb_original_language"] == "tmdb"


def test_the_origin_country_read_is_not_production_countries():
    """The two fields disagree on this very title, so reading the wrong one is
    a red test rather than a silent pass.

    The captured ``/movie/940143`` carries ``origin_country: ["US"]`` and
    ``production_countries: [GB, US]`` -- which is the captured counter-example
    that refutes the substitution roadmap row 189 rules out. Plex's own
    ``<Country>`` already carries the production country; this column carries
    the origin country, and they are not the same statement.
    """
    payload = load("tmdb_movie.json")
    assert [entry["iso_3166_1"] for entry in payload["production_countries"]] == ["GB", "US"]
    assert parse_movie_facts(payload).tmdb_origin_country == ["US"]


def test_the_original_language_read_is_not_the_languages_list():
    """``languages`` is the list of languages the show is available in;
    ``original_language`` is the one it was made in. Reading the former would
    yield a list where an ISO-639-1 code belongs."""
    payload = load("tmdb_show.json")
    assert payload["languages"] == ["en"]
    assert parse_show_facts(payload).tmdb_original_language == "en"


def test_a_movie_in_a_franchise_carries_its_collection_id():
    """``belongs_to_collection`` is an object or ``null``; the id is what row
    192's enumeration keys on."""
    facts = parse_movie_facts(load("tmdb_movie_franchise.json"))
    assert facts.tmdb_collection_id == 8091
    assert facts.sources["tmdb_collection_id"] == "tmdb"


def test_show_facts_carry_the_two_fields_a_show_has():
    """A show has no ``belongs_to_collection`` -- TMDb collections are movie
    franchises (``builders/tmdb.py``'s ``TmdbCollectionBuilder``)."""
    facts = parse_show_facts(load("tmdb_show.json"))
    assert facts.tmdb_origin_country == ["US"]
    assert facts.tmdb_original_language == "en"
    assert facts.tmdb_collection_id is None


def test_the_three_fields_are_absent_rather_than_empty_when_tmdb_has_none():
    """Absent must never be written as a value -- ``persist_facts``' Finding 4.
    An empty ``origin_country`` list is the same statement as no key at all."""
    facts = parse_movie_facts({"id": 1})
    assert facts.tmdb_origin_country == []
    assert facts.tmdb_original_language is None
    assert facts.tmdb_collection_id is None
    assert "tmdb_origin_country" not in facts.sources


def test_a_null_collection_is_absent_not_a_shape_error():
    """``belongs_to_collection: null`` is the normal case -- most films are in
    no franchise -- and it must read exactly like the key being missing, which
    is the shape ``/tv/{id}`` sends."""
    assert parse_movie_facts({"belongs_to_collection": None}).tmdb_collection_id is None
    assert "tmdb_collection_id" not in parse_movie_facts({"belongs_to_collection": None}).sources


@pytest.mark.parametrize("payload", [
    {"origin_country": "US"},
    {"origin_country": [None, 5, "US"]},
    {"belongs_to_collection": []},
    {"belongs_to_collection": {"name": "no id here"}},
    {"belongs_to_collection": {"id": "not-a-number"}},
    {"original_language": 7},
])
def test_malformed_prefetch_fields_are_ignored_rather_than_raising(payload):
    """Every other parser here degrades on malformed input rather than taking
    the whole gather down (``_genres``, ``_first_name``, ``_as_date``); these
    three do the same. ``origin_country: "US"`` -- a bare string where TMDb
    documents a list -- is the one that would otherwise iterate to
    ``["U", "S"]``, which is a plausible wrong value, not an absent one."""
    facts = parse_movie_facts(payload)
    assert facts.tmdb_origin_country in ([], ["US"])
    if payload.get("origin_country") == [None, 5, "US"]:
        assert facts.tmdb_origin_country == ["US"]
    else:
        assert facts.tmdb_collection_id is None or isinstance(
            facts.tmdb_collection_id, int
        )


# --- the collection overview a ``tmdb_summary:`` definition borrows ---------


async def test_a_collection_summary_is_read_from_the_overview():
    calls = []

    def handler(request):
        calls.append(str(request.url))
        return httpx.Response(200, json={"id": 10, "overview": "The whole saga."})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        summary = await TMDBFactsClient("tok", http).collection_summary(10)

    assert summary == "The whole saga."
    assert calls == ["https://api.themoviedb.org/3/collection/10"]


@pytest.mark.parametrize("payload", [{"id": 10}, {"id": 10, "overview": ""}])
async def test_a_collection_with_no_overview_has_no_summary_to_borrow(payload):
    """TMDB writes an empty string, not a missing key, for a collection nobody
    has described. Both mean "nothing to borrow" -- and the caller must leave
    the collection's own summary alone rather than blank it."""
    def handler(request):
        return httpx.Response(200, json=payload)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        assert await TMDBFactsClient("tok", http).collection_summary(10) is None


async def test_a_collection_tmdb_does_not_know_has_no_summary():
    def handler(request):
        return httpx.Response(404, json={"status_message": "not found"})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        assert await TMDBFactsClient("tok", http).collection_summary(999) is None


async def test_a_collection_summary_is_served_from_the_cache(session):
    """One request per TTL. A pass over two libraries reconciles the same
    definition twice, and the summary is the same both times."""
    calls = []

    def handler(request):
        calls.append(str(request.url))
        return httpx.Response(200, json={"id": 10, "overview": "The whole saga."})

    from autoposter.providers.cache import ProviderCache

    cache = ProviderCache(session_factory_for(session))
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        client = TMDBFactsClient("tok", http, cache=cache, cache_ttl_seconds=3600)
        first = await client.collection_summary(10)
        second = await client.collection_summary(10)

    assert first == second == "The whole saga."
    assert len(calls) == 1


# --- roadmap row 100, sub-phase C2c: the two status fields ------------------


def test_show_facts_carry_the_status_token_and_the_last_air_date():
    """The whole data seam in one assertion pair. Both keys come off the SAME
    `/tv/{id}` payload `parse_show_facts` is already handed -- zero new HTTP,
    zero new endpoints (`/modules/tmdb.py:272-273` reads both in one
    `_load_data` block upstream, which is where the claim that they travel
    together comes from).

    The stored status is Kometa's TOKEN, not TMDb's string: `discover_status`
    (`/modules/tmdb.py:108`) maps one onto the other, and the token is what an
    operator writes and what `check_value` is compared as
    (`/modules/tmdb.py:685-693`)."""
    facts = parse_show_facts({
        "status": "Returning Series",
        "last_air_date": "2026-08-20",
    })
    assert facts.tmdb_status == "returning"
    assert facts.last_episode_aired == date(2026, 8, 20)


@pytest.mark.parametrize("tmdb_spelling,token", [
    ("Returning Series", "returning"),
    ("Planned", "planned"),
    ("In Production", "production"),
    ("Ended", "ended"),
    ("Canceled", "canceled"),
    ("Pilot", "pilot"),
])
def test_every_tmdb_status_maps_to_kometas_own_token(tmdb_spelling, token):
    """`discover_status`, transcribed entry for entry
    (`/modules/tmdb.py:108`). A dropped or mistyped entry is not a crash: it
    is a show that silently stops matching its band, which is exactly the
    failure a transcription checksum exists to catch. Three of the six draw
    nothing in any shipped family -- `planned`, `production` and `pilot` have
    no overlay in `status.yml` -- and they are pinned anyway, because the
    VOCABULARY is the transcription even where the ART is not."""
    assert parse_show_facts({"status": tmdb_spelling}).tmdb_status == token


def test_a_status_outside_the_six_is_kept_verbatim_rather_than_dropped():
    """A DECLARED DIVERGENCE, kinder in one direction only. Upstream's
    `discover_status[item.status]` is a bare subscript with no guard
    (`/modules/tmdb.py:685`), so a seventh TMDb status raises `KeyError`
    there. Here it is stored as TMDb spelled it: it matches none of the four
    bands (which name `returning`/`canceled`/`ended` only), so the drawn
    outcome is upstream's minus the crash -- and, unlike `None`, it stays
    distinguishable from 'TMDb has not told us', which is what the NULL
    column means and what the enumeration and drift stories both depend on
    being able to tell apart."""
    facts = parse_show_facts({"status": "Rebooted"})
    assert facts.tmdb_status == "Rebooted"


def test_a_show_payload_without_the_two_keys_leaves_both_absent():
    """The shared fixture is a hand-trimmed capture of `/tv/95396` and carries
    NEITHER key; this plan deliberately does not edit it, so it is the pin for
    the absence path. Absent must read as None, not as an empty string and
    not as a guessed default: None is what the nullable column means, and
    what the missing-value rule turns into 'this item draws no status
    badge'."""
    facts = parse_show_facts(load("tmdb_show.json"))
    assert facts.tmdb_status is None
    assert facts.last_episode_aired is None


@pytest.mark.parametrize("payload", [
    {"status": 7, "last_air_date": 7},
    {"status": "", "last_air_date": ""},
    {"status": "   ", "last_air_date": "not-a-date"},
    {"status": None, "last_air_date": None},
])
def test_malformed_status_fields_are_absent_rather_than_raising(payload):
    """The `_genres`/`_countries` discipline, one field pair along: a value of
    the wrong shape is read as ABSENT, never coerced into a plausible wrong
    one and never allowed to raise out of a parse that has already produced
    good values for every other field. `_as_date` already returns None for an
    unparseable string, so `last_air_date` needs no new guard -- this pins
    that it does not."""
    facts = parse_show_facts(payload)
    assert facts.tmdb_status is None
    assert facts.last_episode_aired is None


def test_the_two_status_fields_record_tmdb_as_their_source():
    """`sources` is 'which provider supplied each field, so a later source
    change is traceable' (`db/models.py::ItemFacts.sources`). Both new fields
    join the same loop every other TMDb-sourced field is in; leaving them out
    would make the one column an operator most wants provenance for the one
    column that has none."""
    facts = parse_show_facts({
        "status": "Ended",
        "last_air_date": "2024-05-01",
    })
    assert facts.sources["tmdb_status"] == "tmdb"
    assert facts.sources["last_episode_aired"] == "tmdb"


def test_a_movie_payload_never_carries_a_show_status():
    """`allowed_libraries: show` (`status.yml:39`) is achieved by
    CONSTRUCTION rather than by a new field, and this is the first half of
    that argument: `parse_movie_facts` has no status field to write, so a
    movie's `item_facts` row can never carry one, so every `tmdb_status`
    condition answers None for a movie and the tag missing-value rule
    excludes it. The payload here deliberately CARRIES both keys -- a movie
    endpoint would not, but proving the movie parser ignores them even when
    present is what makes the by-construction claim hold rather than
    coincide."""
    facts = parse_movie_facts({
        "status": "Released",
        "last_air_date": "2026-08-20",
        "title": "X",
    })
    assert facts.tmdb_status is None
    assert facts.last_episode_aired is None


def test_a_gather_whose_only_fact_is_a_status_is_not_empty():
    """Established facts item **z**, decided rather than defaulted.
    `is_empty()` gates `persist_facts` entirely: if a status-only gather read
    as empty, `persist_facts` would short-circuit before its own branches ran
    and the column would never be written for a show TMDb has nothing else
    for. Every field with an `item_facts` column is in `is_empty()`; these
    two join them."""
    assert parse_show_facts({"status": "Ended"}).is_empty() is False
    assert parse_show_facts({"last_air_date": "2024-05-01"}).is_empty() is False
    assert parse_show_facts({}).is_empty() is True


# --- Roadmap row 227: TMDb release dates -----------------------------------


def test_the_two_release_types_are_kometas_own_codes():
    """Transcribed from Kometa's ``modules/operations.py`` at v2.4.8. The four
    codes this service does not offer are deliberately absent: a source it
    cannot serve is a config load error, so a map entry no ``Literal`` can
    reach could only ever be a lie about what loads."""
    assert TMDB_RELEASE_TYPES == {"tmdb_premiere": 1, "tmdb_digital": 4}


def test_the_digital_date_is_the_earliest_across_every_region():
    """Kometa's rule with no region set: iterate every region, take min().
    The US type-4 entry here is 2026-05-12 (a 4K re-release of a 1999 film),
    so a parser that stops at the first region gets this wrong."""
    payload = load("tmdb_release_dates.json")
    assert parse_release_date(payload, 4) == date(1999, 12, 1)


def test_the_premiere_date_is_the_earliest_across_every_region():
    payload = load("tmdb_release_dates.json")
    assert parse_release_date(payload, 1) == date(1999, 9, 21)


def test_several_entries_of_one_type_in_one_region_resolve_to_the_earliest():
    """Measured, not theoretical: the captured response carries three type-5
    entries for one movie in one country. min() decides the value."""
    payload = load("tmdb_release_dates.json")
    assert parse_release_date(payload, 5) == date(2000, 4, 25)


def test_a_type_with_no_entry_anywhere_is_none():
    """Kometa's ``raise Failed``, at this seam: a source that yields nothing
    writes nothing, and must never yield an empty value instead."""
    payload = load("tmdb_release_dates.json")
    assert parse_release_date(payload, 6) is None


def test_an_empty_results_list_is_none():
    assert parse_release_date({"id": 550, "results": []}, 4) is None
    assert parse_release_date({}, 4) is None


def test_the_iso_8601_shape_with_a_time_is_what_is_parsed():
    """The trap this test exists for: TMDb answers
    ``"1999-10-15T00:00:00.000Z"``, and ``_as_date`` (strptime "%Y-%m-%d")
    returns None for it. A build that reuses ``_as_date`` unchanged ships a
    feature that silently never fires, and a hand-typed "1999-10-15" fixture
    would not catch it -- so this test asserts BOTH halves."""
    from autoposter.facts.tmdb_facts import _as_date

    assert _as_date("1999-10-15T00:00:00.000Z") is None

    payload = {"results": [
        {"iso_3166_1": "US", "release_dates": [
            {"release_date": "1999-10-15T00:00:00.000Z", "type": 4},
        ]},
    ]}
    assert parse_release_date(payload, 4) == date(1999, 10, 15)


def test_a_malformed_release_date_is_ignored_rather_than_raising():
    payload = {"results": [
        {"iso_3166_1": "US", "release_dates": [
            {"release_date": "", "type": 4},
            {"release_date": "not-a-date", "type": 4},
            {"release_date": None, "type": 4},
            {"release_date": "2001-01-05T00:00:00.000Z", "type": 4},
        ]},
    ]}
    assert parse_release_date(payload, 4) == date(2001, 1, 5)


def test_a_malformed_results_shape_is_ignored_rather_than_raising():
    assert parse_release_date({"results": "oops"}, 4) is None
    assert parse_release_date({"results": ["junk", {"release_dates": "nope"}]}, 4) is None


async def test_the_client_requests_the_release_dates_endpoint():
    seen = {}

    def handler(request):
        seen["url"] = str(request.url)
        return httpx.Response(200, json=load("tmdb_release_dates.json"))

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        client = TMDBFactsClient("tok", http)
        found = await client.release_date(550, "tmdb_digital")

    assert seen["url"] == "https://api.themoviedb.org/3/movie/550/release_dates"
    assert "append_to_response" not in seen["url"]
    assert found == date(1999, 12, 1)


async def test_the_client_returns_none_on_404():
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda r: httpx.Response(404, json={}))
    ) as http:
        found = await TMDBFactsClient("tok", http).release_date(550, "tmdb_premiere")
    assert found is None


async def test_the_release_dates_read_does_not_orphan_the_movie_cache_entry(session):
    """The single highest-value acceptance line of this task.

    ``build_cache_key`` hashes ``[method, url, params]``, so reaching the
    release dates by adding ``?append_to_response=release_dates`` to
    ``/movie/{id}`` would change the movie request's key and orphan every
    cached movie-facts entry in the table at once -- 25,044 rows on this
    deployment. This is the same failure ``providers/tmdb.py``'s ``fetch``
    docstring already records for the artwork client. A separate endpoint is
    a separate key, and the proof is that the movie read is still served from
    the cache AFTER the new read has happened.
    """
    from autoposter.facts.tmdb_facts import BASE_URL
    from autoposter.providers.cache import ProviderCache, build_cache_key

    calls = []

    def handler(request):
        calls.append(str(request.url))
        if request.url.path.endswith("/release_dates"):
            return httpx.Response(200, json=load("tmdb_release_dates.json"))
        return httpx.Response(200, json=load("tmdb_movie.json"))

    cache = ProviderCache(session_factory_for(session))
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        client = TMDBFactsClient("tok", http, cache=cache, cache_ttl_seconds=3600)
        await client.movie(550)
        await client.release_date(550, "tmdb_digital")
        again = await client.movie(550)

    assert calls == [
        "https://api.themoviedb.org/3/movie/550",
        "https://api.themoviedb.org/3/movie/550/release_dates",
    ], "the third read must have been served from the cache, not the api"
    assert again.originally_available == date(2023, 5, 12)
    assert build_cache_key("GET", f"{BASE_URL}/movie/550", None) != build_cache_key(
        "GET", f"{BASE_URL}/movie/550/release_dates", None
    )
