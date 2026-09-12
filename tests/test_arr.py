import json
from pathlib import Path
from typing import get_args

import pytest

from autoposter.intake.arr import (
    RADARR_EVENTS,
    SONARR_EVENTS,
    RadarrPayload,
    RenderIntent,
    SonarrPayload,
    parse_radarr,
    parse_sonarr,
)

FIXTURES = Path(__file__).parent / "fixtures" / "webhooks"


def load(name: str) -> dict:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


@pytest.mark.parametrize(
    "fixture", ["radarr_download.json", "radarr_rename.json", "radarr_movie_added.json"]
)
def test_radarr_events_yield_one_movie_intent(fixture):
    intents = parse_radarr(load(fixture))
    assert len(intents) == 1
    assert intents[0].kind == "movie"
    assert intents[0].tmdb_id is not None


def test_radarr_download_extracts_identity():
    intent = parse_radarr(load("radarr_download.json"))[0]
    assert intent.tmdb_id == 693134
    assert intent.imdb_id == "tt15239678"
    assert intent.title == "Dune: Part Two"
    assert intent.year == 2024


def test_radarr_test_payload_is_ignored():
    assert parse_radarr(load("radarr_test.json")) == []


def test_sonarr_test_payload_is_ignored():
    assert parse_sonarr(load("sonarr_test.json")) == []


def test_sonarr_episode_import_fans_out_to_show_season_and_episode():
    intents = parse_sonarr(load("sonarr_download_single.json"))
    kinds = [i.kind for i in intents]
    assert kinds == ["show", "season", "episode"]
    show, season, episode = intents
    assert show.tvdb_id == 371980
    assert season.season_number == 2
    assert episode.season_number == 2
    assert episode.episode_number == 3
    assert episode.title == "Who Is Alive?"


def test_season_pack_emits_one_season_intent_per_distinct_season():
    intents = parse_sonarr(load("sonarr_import_complete_seasonpack.json"))
    seasons = [i.season_number for i in intents if i.kind == "season"]
    episodes = [(i.season_number, i.episode_number) for i in intents if i.kind == "episode"]
    assert sorted(seasons) == [2, 3]
    assert sorted(episodes) == [(2, 1), (2, 2), (3, 1)]
    assert len([i for i in intents if i.kind == "show"]) == 1


def test_sonarr_rename_has_no_episodes_and_yields_show_only():
    intents = parse_sonarr(load("sonarr_rename.json"))
    assert [i.kind for i in intents] == ["show"]


def test_sonarr_series_add_yields_show_only():
    intents = parse_sonarr(load("sonarr_series_add.json"))
    assert [i.kind for i in intents] == ["show"]
    assert intents[0].title == "The Studio"


def test_zero_ids_are_treated_as_missing():
    payload = load("sonarr_series_add.json")
    payload["series"]["tmdbId"] = 0
    assert parse_sonarr(payload)[0].tmdb_id is None


def test_missing_optional_keys_do_not_raise():
    payload = load("sonarr_download_single.json")
    del payload["series"]["imdbId"]
    del payload["episodes"][0]["airDate"]
    intents = parse_sonarr(payload)
    assert intents[0].imdb_id is None


def test_event_type_is_matched_case_insensitively():
    payload = load("radarr_download.json")
    payload["eventType"] = "download"
    assert len(parse_radarr(payload)) == 1


def test_unknown_event_types_are_ignored():
    payload = load("radarr_download.json")
    payload["eventType"] = "HealthRestored"
    assert parse_radarr(payload) == []


def test_dedupe_keys_are_stable_and_distinct():
    first = [i.dedupe_key for i in parse_sonarr(load("sonarr_download_single.json"))]
    second = [i.dedupe_key for i in parse_sonarr(load("sonarr_download_single.json"))]
    assert first == second
    assert len(set(first)) == 3
    assert "s02e03" in first[2]


def test_a_rating_key_does_not_change_the_dedupe_key():
    """The Plex rating key must stay out of the queue key.

    Jobs enqueued before the rating key was carried are still pending in the
    queue, and ``uq_jobs_pending_dedupe`` is what stops a new full pass from
    queueing the same item a second time. If the rating key entered the key,
    every one of those in-flight jobs would stop matching and the first pass
    after the deploy would double-queue the whole library.
    """
    without = RenderIntent(
        kind="episode", title="Pilot", tvdb_id=200, season_number=1, episode_number=1
    )
    with_key = RenderIntent(
        kind="episode", title="Pilot", tvdb_id=200, season_number=1, episode_number=1,
        refs={"plex": "77632"},
    )
    assert with_key.dedupe_key == without.dedupe_key
    assert without.dedupe_key == "process_item:episode:tvdb200:s01e01"


def test_a_payload_queued_before_rating_keys_existed_still_rebuilds():
    """``queue/worker.py`` does ``RenderIntent.from_payload(job.payload)``. Pending
    rows written by the previous release carry no ``refs`` key at all, so the
    field has to default rather than be required."""
    intent = RenderIntent(**{"kind": "movie", "title": "A Movie", "tmdb_id": 100})
    assert intent.native_id_on("plex") is None


def test_webhook_intents_carry_no_rating_key():
    """Sonarr/Radarr know nothing about Plex, so the webhook path keeps
    resolving by GUID -- there is no key to shortcut with."""
    intents = parse_sonarr(load("sonarr_download_single.json"))
    assert [i.native_id_on("plex") for i in intents] == [None, None, None]
    assert parse_radarr(load("radarr_download.json"))[0].native_id_on("plex") is None


def test_intent_is_hashable_for_set_deduplication():
    a = RenderIntent(kind="show", title="X", tvdb_id=1)
    b = RenderIntent(kind="show", title="X", tvdb_id=1)
    assert len({a, b}) == 1


def test_the_accepted_event_literals_are_the_event_sets():
    """Defined once, read twice. The Literal is what the intake gate validates
    against; the set is what the parsers match on. If the two ever drift, a body
    validates and then fans out to nothing -- or, worse, the reverse."""
    assert set(get_args(RadarrPayload.model_fields["eventType"].annotation)) == RADARR_EVENTS
    assert set(get_args(SonarrPayload.model_fields["eventType"].annotation)) == SONARR_EVENTS
