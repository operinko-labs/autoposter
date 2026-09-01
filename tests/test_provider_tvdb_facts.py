"""Row 84 -- TVDb as a named source for genres, studio and release date.

The parser reads the SAME extended payload the artwork client already fetches
(providers/tvdb.py::fetch), so this row costs no new endpoint and no new
request shape. The fixtures are cut from live responses captured in this
phase; the key names this parser reads are pinned by them and by nothing else
(see the plan's S4).
"""
import json
from datetime import date
from pathlib import Path

import pytest

from autoposter.config.schema import OperationsConfig
from autoposter.providers.tvdb import parse_tvdb_facts

FIXTURES = Path(__file__).parent / "fixtures" / "providers"


def _payload(name):
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def test_a_movie_extended_payload_yields_genres_and_date():
    facts = parse_tvdb_facts(_payload("tvdb_movie_extended.json"), is_movie=True)
    assert facts.genres == ["Drama", "Fantasy"]
    assert facts.originally_available == date(2006, 8, 25)


def test_a_movie_with_an_empty_studio_bucket_yields_no_studio():
    # The captured fixture's `companies.studio` bucket is empty and its
    # `companies.production` bucket carries seven arbitrary entries -- reading
    # the first `production` entry as "the studio" was tried and reverted
    # (mass-ops-2 review) because one fixture cannot establish that TVDb
    # orders `production` by studio-ness. Filed rather than guessed; see the
    # roadmap row this pins.
    facts = parse_tvdb_facts(_payload("tvdb_movie_extended.json"), is_movie=True)
    assert facts.studio is None
    assert "studio" not in facts.sources


def test_a_series_extended_payload_yields_genres_network_and_first_aired():
    facts = parse_tvdb_facts(_payload("tvdb_series_extended.json"), is_movie=False)
    assert facts.genres == ["Fantasy", "Drama", "Adventure", "Action"]
    assert facts.studio == "HBO"
    assert facts.originally_available == date(2011, 4, 17)


def test_an_empty_payload_yields_nothing_rather_than_empty_values():
    facts = parse_tvdb_facts({}, is_movie=True)
    assert facts.genres == []
    assert facts.studio is None
    assert facts.originally_available is None


def test_the_parser_records_tvdb_as_the_source_for_what_it_found():
    facts = parse_tvdb_facts(_payload("tvdb_movie_extended.json"), is_movie=True)
    assert facts.sources["genres"] == "tvdb"


def test_the_source_keys_default_to_unset():
    operations = OperationsConfig()
    assert operations.genres_source is None
    assert operations.studio_source is None
    assert operations.originally_available_source is None


def test_an_unknown_field_source_is_a_config_load_error():
    with pytest.raises(Exception):
        OperationsConfig(genres_source="omdb")
