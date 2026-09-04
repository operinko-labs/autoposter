"""``collections/source_urls.py``: every accepted shape and every refusal.

Pure -- no app, no network. The parse is shape-only by design (facts C2):
nothing here mocks a provider because nothing is fetched; a list that does
not exist is the first pass's discovery, and sync semantics leave the
collection untouched when it fails.

The refusal table's fragments are the load-bearing half: a recognised shape
with a bad value must surface the params model's OWN error string (not a
paraphrase), and a trakt paste must be refused BY NAME -- row 202's fence.
"""
import re

import pytest

from autoposter.collections.source_urls import SourceUrlRefused, parse_source

ACCEPTED = [
    # imdb.com: one host, two builders -- the path decides (the dispatch the
    # recon flagged).
    ("https://www.imdb.com/list/ls055350410/", "imdb_list", {"list": "ls055350410"}),
    ("imdb.com/list/ls055350410?ref_=hm", "imdb_list", {"list": "ls055350410"}),
    (
        "https://m.imdb.com/user/ur00000001/watchlist",
        "imdb_watchlist", {"user": "ur00000001"},
    ),
    (
        "https://mdblist.com/lists/linaspurinis/top-watched-movies-of-the-week",
        "mdblist_list", {"list": "linaspurinis/top-watched-movies-of-the-week"},
    ),
    # The TMDb entity family rides free on one params model (facts C2). A
    # slugged segment's id is its leading digits.
    ("https://www.themoviedb.org/list/8136243", "tmdb_list", {"id": 8136243}),
    (
        "https://www.themoviedb.org/collection/10-star-wars-collection",
        "tmdb_collection", {"id": 10},
    ),
    ("https://www.themoviedb.org/company/2", "tmdb_company", {"id": 2}),
    ("https://www.themoviedb.org/network/213-netflix", "tmdb_network", {"id": 213}),
    (
        "https://www.themoviedb.org/keyword/9715-superhero/movie",
        "tmdb_keyword", {"id": 9715},
    ),
    (
        "https://www.thetvdb.com/lists/marvel-cinematic-universe",
        "tvdb_list", {"slug": "marvel-cinematic-universe"},
    ),
    # Bare shapes (facts C2): accepted where unambiguous.
    ("ls055350410", "imdb_list", {"list": "ls055350410"}),
    ("ur00000001", "imdb_watchlist", {"user": "ur00000001"}),
    (
        "linaspurinis/top-watched-movies-of-the-week",
        "mdblist_list", {"list": "linaspurinis/top-watched-movies-of-the-week"},
    ),
    ("8136243", "tmdb_list", {"id": 8136243}),
]


@pytest.mark.parametrize("text,builder,params", ACCEPTED)
def test_accepted_shapes_resolve_to_the_shipped_builder(text, builder, params):
    parsed = parse_source(text)

    assert (parsed.builder, parsed.params) == (builder, params)


def test_a_bare_number_discloses_the_tmdb_reading():
    """int is the one bare shape more than one builder could claim (TMDb,
    TVDb and MDBList all take a numeric list id); the note says which reading
    was taken and how to get the others."""
    parsed = parse_source("8136243")

    assert "read as a TMDb list id" in parsed.display_note


def test_a_slashed_bare_value_is_an_mdblist_reference_whatever_it_starts_with():
    """The bare shapes are told apart by shape, not by dispatch order: an
    MDBList reference is the only one carrying a ``/``, so an ``ls…``-looking
    user name is still a user name and not a mangled IMDb list id."""
    parsed = parse_source("lsfan/best-of-2024")

    assert (parsed.builder, parsed.params) == (
        "mdblist_list", {"list": "lsfan/best-of-2024"}
    )


REFUSED = [
    # trakt: refused BY NAME -- row 202's fence, verbatim in the message.
    ("https://trakt.tv/users/someone/lists/best-of", "no trakt builder is shipped"),
    # An unknown host with a scheme is a refusal naming what IS supported...
    ("https://letterboxd.com/someone/list/slasher-flicks/", "is not a supported source"),
    # ...and schemeless it falls through the bare shapes to the same teaching.
    ("letterboxd.com/someone/list/slasher-flicks", "not a URL or a bare id"),
    # A known host off its list path.
    ("https://www.imdb.com/title/tt0111161/", "not a list or a user page"),
    # A recognised shape with a bad value reuses the params model's OWN error
    # string -- including the ls/ur cross-hint the imdb models teach with.
    ("https://www.imdb.com/list/ur00000001", "use the `imdb_watchlist` builder"),
    ("ls12x4", "is not an IMDb list id"),
    # TmdbEntityParams' gt=0: pydantic's own message, not a paraphrase.
    ("0", "greater than 0"),
    ("", "paste a list URL"),
    ("just words", "not a URL or a bare id"),
    # A paste ``urlsplit`` itself refuses to split (it reads '[' as the start
    # of an IPv6 literal) is still a refusal, not a traceback -- and its own
    # refusal, not MDBList's error string about a value never offered to it.
    ("https://exam[ple.com/list", "is not a URL this form can read"),
    # A scheme-ful URL to a *dotless* host used to fall through to
    # ``_parse_bare`` and draw MDBList's error string. It now gets the same
    # clean unknown-host refusal as a dotted one, dotted or not, scheme
    # required or protocol-relative.
    ("http://localhost:32400/library/x", "is not a supported source"),
    ("https://[::1]/list/x", "is not a supported source"),
    ("//imdb.com/list/ls1", "is not a supported source"),
]


@pytest.mark.parametrize("text,fragment", REFUSED)
def test_refused_shapes_name_what_is_wrong(text, fragment):
    with pytest.raises(SourceUrlRefused, match=re.escape(fragment)):
        parse_source(text)


# Pastes that carry credentials and would once have echoed them into the 422
# detail (the redaction law -- collections_builders.py:74-82 -- forbids it).
CREDENTIAL_PASTES = [
    # Schemeless, dotted host: falls through to the generic bare-id refusal.
    "admin:hunter2@myplex.example/list",
    # A urlsplit ValueError (the '[' IPv6 guard) used to echo the whole value.
    "https://user:pass@exam[ple.com/list",
    # Dotless host: used to reach MDBList's own {value!r} error string.
    "http://user:pass@localhost/x",
    # The same dotless host WITHOUT a scheme -- the fourth variant, found by
    # the branch review. No scheme and no leading '//' means the unknown-host
    # gate never fires, and a dotless head sent the whole paste to
    # ``MdblistListParams``, whose {value!r} echoed it. The intranet form is
    # the plausible operator paste, not the contrived one.
    "user:pass@localhost/x",
    "admin:hunter2@nas/library",
]


@pytest.mark.parametrize("text", CREDENTIAL_PASTES)
def test_refusals_never_echo_the_pasted_credentials(text):
    with pytest.raises(SourceUrlRefused) as excinfo:
        parse_source(text)

    assert "hunter2" not in str(excinfo.value)
    assert "user:pass" not in str(excinfo.value)


# The query string, which the `:`/`@` guard above misses. Three bare branches
# hand the whole stripped paste to a params model that interpolates it, so
# `abc/def?apikey=SECRET` came back with the key in the 422 detail. The
# URL-shaped branches were never exposed: they pass `urlsplit` path segments
# only, discarding query and userinfo by construction.
QUERY_PASTES = [
    # `/` present, dotless head -> MdblistListParams.
    "abc/def?apikey=SECRET",
    # `ls…` -> ImdbListParams.
    "ls123?token=SECRET",
    # `ur…` -> ImdbWatchlistParams.
    "ur99?token=SECRET",
    # A fragment, for the same reason: everything after `#` is operator text
    # this parser has no reading for.
    "abc/def#SECRET",
]


@pytest.mark.parametrize("text", QUERY_PASTES)
def test_a_query_or_fragment_paste_is_refused_without_echoing_it(text):
    with pytest.raises(SourceUrlRefused) as excinfo:
        parse_source(text)

    assert "SECRET" not in str(excinfo.value)


def test_an_unknown_host_refusal_does_not_echo_the_host():
    """The unknown-host refusal is the 422 detail collections_builders.py
    serves back; the pasted host can be an intranet address the operator
    did not mean to publish, so the message teaches what IS supported and
    names nothing from the paste."""
    with pytest.raises(SourceUrlRefused) as excinfo:
        parse_source("http://plex.internal:32400/library/x")

    message = str(excinfo.value)
    assert "is not a supported source" in message
    assert "plex.internal" not in message
    assert "32400" not in message


def test_refusal_message_drops_pydantics_value_error_prefix():
    """A field_validator's ValueError comes back from pydantic as
    "Value error, <message>" -- that framing is pydantic's, not the model's
    own sentence, and does not belong in an operator-facing refusal."""
    with pytest.raises(SourceUrlRefused) as excinfo:
        parse_source("ls12x4")

    assert "Value error" not in str(excinfo.value)
