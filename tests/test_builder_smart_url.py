"""``smart_url``: a Plex Web URL pasted into config, stored as a smart filter.

Kometa's no-DSL convenience form (``builder.py:1462-1474``, extractor at
``modules/plex.py:1610-1613``, v2.4.8). The whole builder is that three-line
extractor plus 9c's reconciler, so the tests split the same way:

- an ORACLE pair against a verbatim transcription of Kometa's three lines,
  because "the same query Kometa would have extracted" is the only definition
  of correct this builder has;
- one test per refusal shape, because the cell's own words are "the URL is a
  query nobody validated, so it needs its own refusal surface";
- a token test, because this is the first pasted-URL config value in the
  collections package and a Plex Web URL can carry ``X-Plex-Token``;
- one engine-level test proving a pasted URL and the equivalent
  ``smart_filter`` definition store a BYTE-IDENTICAL filter, which is the
  acceptance criterion this phase sets: the paste is a spelling of the
  query, never a second query grammar.
"""
import logging
from types import SimpleNamespace
from urllib.parse import parse_qs, urlparse, urlsplit

import pytest
from pydantic import ValidationError

from autoposter.collections.builders.smart_url import (
    SmartUrlBuilder,
    SmartUrlNotAFilter,
    SmartUrlParams,
    smart_query_from_uri,
    strip_plex_token,
)
from autoposter.collections.builders.base import LibraryTypeMismatch
from autoposter.collections.builders.smart_filter import SmartFilterBuilder
from autoposter.collections.engine import run_library
from autoposter.collections.smart import smart_definition_hash
from autoposter.config.schema import CollectionDefinition

LABEL = "autoposter"
SECTION_KEY = "2"
_TOKEN = "SECRETSECRETSECRET01"

# A real Plex Web smart-filter URL's shape: the interesting half is the
# percent-encoded ``key`` parameter, whose value is itself a path plus a query.
_MOVIE_URL = (
    "https://app.plex.tv/desktop/#!/server/abc123/com.plexapp.plugins.library"
    "?source=1&pageType=list"
    "&key=%2Flibrary%2Fsections%2F1%2Fall%3Ftype%3D1%26sort%3Drandom"
    "%26genre%3D1138"
)
_SHOW_URL = (
    "https://app.plex.tv/desktop/#!/server/abc123/com.plexapp.plugins.library"
    "?key=%2Flibrary%2Fsections%2F4%2Fall%3Ftype%3D2%26sort%3DtitleSort%253Aasc"
    "%26show.genre%3D9&pageType=list&source=4"
)
_MOVIE_URL_WITH_YEAR = (
    "https://app.plex.tv/desktop/#!/server/abc123/com.plexapp.plugins.library"
    "?key=%2Flibrary%2Fsections%2F1%2Fall%3Ftype%3D1%26sort%3Dtitle%26year%3E%3D2000"
)
# ``_MOVIE_URL`` with a live session token on the end, exactly where Plex Web
# puts it in the address bar. Used by the engine tests below (Global
# Constraint 8's caplog/action-string proof), because ``_MOVIE_URL`` alone
# never drove a token through ``run_library``.
_MOVIE_URL_WITH_TOKEN = _MOVIE_URL + "&X-Plex-Token=" + _TOKEN


def kometa_get_smart_filter_from_uri(uri):
    """``Plex.get_smart_filter_from_uri`` (modules/plex.py:1610-1613, v2.4.8),
    transcribed verbatim minus the ``build_smart_filter`` envelope call -- the
    envelope is ``collections/smart.py``'s own and is oracled separately by
    ``tests/test_smart_collection_oracle.py``. What is compared here is the two
    things the extractor DERIVES: the query string and the libtype key."""
    smart_filter = parse_qs(urlparse(uri.replace("/#!/", "/")).query)["key"][0]
    args = smart_filter[smart_filter.index("?"):]
    return args, int(args[args.index("type=") + 5:args.index("type=") + 6])


_LIBTYPE_KEYS = {"movie": 1, "show": 2}


@pytest.mark.parametrize("uri", [_MOVIE_URL, _SHOW_URL, _MOVIE_URL_WITH_YEAR])
def test_the_extractor_is_kometas_three_lines(uri):
    """The oracle. Byte-identical query string, identical libtype key."""
    args, libtype = smart_query_from_uri(uri)
    kometa_args, kometa_type = kometa_get_smart_filter_from_uri(uri)
    assert args == kometa_args
    assert _LIBTYPE_KEYS[libtype] == kometa_type


def test_the_libtype_is_the_single_character_after_type():
    """Kometa slices ONE character (``args[i + 5:i + 6]``), so ``type=10``
    reads as ``1``. Transcribed faithfully rather than corrected: this service
    only ever runs Movie and Show libraries, and a ``type=10`` query stored on
    a movie section is caught downstream by ``require_matches``, which refuses
    a filter matching nothing. Correcting it here would be a silent divergence
    from the oracle above for no operator-visible gain."""
    uri = (
        "https://app.plex.tv/desktop/#!/server/abc123/com.plexapp.plugins.library"
        "?key=%2Flibrary%2Fsections%2F1%2Fall%3Ftype%3D10%26sort%3Dtitle"
    )
    args, libtype = smart_query_from_uri(uri)
    assert libtype == "movie"
    assert args == "?type=10&sort=title"


def test_a_token_is_stripped_before_the_url_is_parsed():
    """The named test for Global Constraint 8. The URL below is malformed (no
    ``key``), so the ONLY thing the operator gets back is the refusal message
    -- which is exactly where a pasted ``X-Plex-Token`` would leak. Stripping
    happens before the parse, so the token cannot reach the message even
    though the parse is what fails. The host is an intranet address rather
    than ``app.plex.tv``, because the fixed message text legitimately shows
    an ``app.plex.tv`` URL as a TEMPLATE example -- what must not appear is
    the OPERATOR's own host."""
    uri = (
        "http://192.168.1.10:32400/web?source=1&X-Plex-Token=SECRETSECRETSECRET01"
    )
    with pytest.raises(SmartUrlNotAFilter) as caught:
        smart_query_from_uri(uri)
    assert "SECRETSECRETSECRET01" not in str(caught.value)
    assert "X-Plex-Token" not in str(caught.value)
    # ...and neither is the pasted URL's HOST, aligned with
    # ``source_urls.py``'s decision for its sibling refusal (8379eab): a paste
    # can be an intranet address the operator did not mean to publish. The
    # refusal names the ATTRIBUTE instead.
    assert "192.168.1.10" not in str(caught.value)
    assert "params.url" in str(caught.value)


def test_a_field_name_that_merely_ends_in_token_is_not_stripped():
    """The tightened name pattern (``X-Plex-Token`` or ``token``, exactly,
    case-insensitively) rather than a suffix match: an earlier
    ``[A-Za-z0-9_.-]*token`` also ate a hypothetical ``titletoken=`` field,
    which carries no credential and is not this service's to remove."""
    assert strip_plex_token("?type=1&titletoken=9&sort=title") == (
        "?type=1&titletoken=9&sort=title"
    )


def test_a_value_ending_in_an_encoded_question_mark_survives_the_strip():
    """The regression this pins: a title containing a literal ``?`` arrives at
    the (already-once-decoded) query as a bare ``%3F`` inside its own value,
    which the old unconditional ``_EMPTY_FIRST_TERM`` cleanup could not tell
    apart from a stripped token's leftover ``?&`` -- and ate the ``&`` after
    it, losing the ``sort=`` term with no token anywhere in the URL. Byte
    identity here is the module's whole reason to exist."""
    assert strip_plex_token("?type=1&title=Who%3F&sort=title") == (
        "?type=1&title=Who%3F&sort=title"
    )


def test_str_validationerror_for_smarturlparams_names_no_token():
    """A pasted URL that is slightly wrong (the common ``.../web/
    index.html#!/...`` mistake, Global Constraint 8's own scenario) but still
    carries a live token must not put that token into ``str(ValidationError)``
    -- not in the refusal message (already true) and not in pydantic-core's
    own ``input_value=`` envelope around it (a gap where a ``mode=
    "before"`` strip alone does not suppress that annotation without
    ``hide_input_in_errors`` too, verified against pydantic 2.12.5)."""
    bad_url_with_token = (
        "http://192.168.1.10:32400/web/index.html#!/server/abc123/"
        "com.plexapp.plugins.library?key=%2Flibrary%2Fsections%2F1%2Fall%3Ftype%3D1"
        "&X-Plex-Token=" + _TOKEN
    )
    with pytest.raises(ValidationError) as caught:
        SmartUrlParams(url=bad_url_with_token)
    assert _TOKEN not in str(caught.value)
    assert "X-Plex-Token=" not in str(caught.value)


def test_a_url_carrying_an_x_plex_token_is_refused():
    """The re-ruled Global Constraint 8 (facts, Amendment 2026-09-05): a
    ``mode="before"`` STRIP cannot clean the stored copy, because
    ``config/schema.py``'s own check (``model.model_validate(self.params)``)
    validates a throwaway instance and discards it --
    ``CollectionDefinition.params['url']``, what ``GET /api/config``, a
    config snapshot and ``/api/config/overrides/export`` all serve back
    verbatim, would still carry whatever the operator pasted. So a
    token-bearing paste is REFUSED outright, with one fixed sentence that
    names ``params.url`` and nothing else -- no host, no token, no echo of
    the pasted value."""
    with pytest.raises(ValidationError) as caught:
        SmartUrlParams(url=_MOVIE_URL_WITH_TOKEN)
    assert _TOKEN not in str(caught.value)
    [error] = caught.value.errors()
    assert _TOKEN not in error["msg"]
    assert (
        "params.url carries a Plex token: remove the X-Plex-Token query "
        "parameter; the server's own token is used"
    ) in error["msg"]


def test_a_url_carrying_a_lowercase_token_parameter_is_refused():
    """The OTHER name ``_TOKEN_TERM`` recognises -- a bare ``token=``, not
    just ``X-Plex-Token=`` -- gets the identical refusal."""
    uri = _MOVIE_URL + "&token=" + _TOKEN
    with pytest.raises(ValidationError) as caught:
        SmartUrlParams(url=uri)
    assert _TOKEN not in str(caught.value)
    [error] = caught.value.errors()
    assert _TOKEN not in error["msg"]
    assert (
        "params.url carries a Plex token: remove the X-Plex-Token query "
        "parameter; the server's own token is used"
    ) in error["msg"]


def test_a_token_bearing_url_is_refused_through_collectiondefinition_without_the_token_served():
    """The boot/config-load surface: ``CollectionDefinition`` is the model
    every config load, and ``GET /api/config``'s own re-check, actually
    validates against. Its wrapped ``errors()[*]["msg"]`` -- the projection
    ``api/routes.py``'s 422 body serves -- must stay token-free too.

    Deliberately NOT asserted here: ``str(caught.value)`` on the OUTER
    error. ``CollectionDefinition``'s own ``mode="after"`` validator
    (``config/schema.py``, out of scope for this branch) echoes its WHOLE
    raw input dict in pydantic-core's ``input_value=`` whenever any of its
    checks fail -- a documented residual (module docstring, "What this
    cannot reach"; facts, Amendment 2026-09-05) whose only sink is the
    trusted pod log (row 207), not a served surface."""
    with pytest.raises(ValidationError) as caught:
        CollectionDefinition(
            title="Pasted", builder="smart_url",
            params={"url": _MOVIE_URL_WITH_TOKEN},
        )
    [error] = caught.value.errors()
    assert _TOKEN not in error["msg"]
    assert "params.url carries a Plex token" in error["msg"]


def test_a_token_inside_the_key_parameter_never_reaches_the_stored_query():
    """The percent-encoded case, which a ``parse_qs``-only strip would miss:
    the token sits inside the ``key`` parameter's own encoded query, so it is
    ``%26X-Plex-Token%3D...`` in the pasted string. It must be gone from the
    query this service STORES on the server, not merely from the message."""
    uri = (
        "https://app.plex.tv/desktop/#!/server/abc123/com.plexapp.plugins.library"
        "?key=%2Flibrary%2Fsections%2F1%2Fall%3Ftype%3D1%26sort%3Dtitle"
        "%26X-Plex-Token%3DSECRETSECRETSECRET01%26genre%3D1138"
    )
    args, libtype = smart_query_from_uri(uri)
    assert "SECRET" not in args
    assert "token" not in args.lower()
    assert args == "?type=1&sort=title&genre=1138"
    assert libtype == "movie"


def test_a_leading_token_term_does_not_corrupt_the_query():
    """The strip must not eat the structural ``?``. A token written first
    would otherwise leave ``/all&type=1``, which is not a query string at
    all."""
    assert strip_plex_token("?X-Plex-Token=SECRET01&type=1&sort=title") == (
        "?type=1&sort=title"
    )


def test_a_url_with_no_key_parameter_is_refused():
    uri = "https://app.plex.tv/desktop/#!/server/abc123/com.plexapp.plugins.library?source=1"
    with pytest.raises(SmartUrlNotAFilter, match="key"):
        smart_query_from_uri(uri)


def test_a_url_whose_fragment_hides_the_query_is_refused():
    """Kometa's rewrite is the literal ``/#!/`` -> ``/`` and nothing else, so
    the ``…/web/index.html#!/…`` spelling leaves the whole query inside the
    URL FRAGMENT, where ``urlparse`` cannot see it. Kometa refuses that URL
    too; the refusal says which spelling to paste rather than leaving an
    operator staring at a URL that looks right."""
    uri = (
        "http://192.168.1.10:32400/web/index.html#!/server/abc123/"
        "com.plexapp.plugins.library?key=%2Flibrary%2Fsections%2F1%2Fall%3Ftype%3D1"
    )
    with pytest.raises(SmartUrlNotAFilter, match="key"):
        smart_query_from_uri(uri)


def test_a_key_with_no_query_of_its_own_is_refused():
    uri = (
        "https://app.plex.tv/desktop/#!/server/abc123/com.plexapp.plugins.library"
        "?key=%2Flibrary%2Fsections%2F1%2Fall"
    )
    with pytest.raises(SmartUrlNotAFilter, match="no query"):
        smart_query_from_uri(uri)


def test_a_query_with_no_type_is_refused():
    uri = (
        "https://app.plex.tv/desktop/#!/server/abc123/com.plexapp.plugins.library"
        "?key=%2Flibrary%2Fsections%2F1%2Fall%3Fsort%3Dtitle%26genre%3D1138"
    )
    with pytest.raises(SmartUrlNotAFilter, match="type="):
        smart_query_from_uri(uri)


def test_an_unsupported_libtype_is_refused():
    """``type=8`` is an artist search. This service builds Movie and Show
    collections only, and storing an artist filter on either would be a
    collection Plex evaluates to nothing forever."""
    uri = (
        "https://app.plex.tv/desktop/#!/server/abc123/com.plexapp.plugins.library"
        "?key=%2Flibrary%2Fsections%2F5%2Fall%3Ftype%3D8%26sort%3Dtitle"
    )
    with pytest.raises(SmartUrlNotAFilter, match="movie or show"):
        smart_query_from_uri(uri)


def test_a_definition_with_an_unparseable_url_is_refused_at_config_load():
    """Kometa refuses at load too (``'smart_url' attribute is incorrectly
    formatted``, builder.py:1472-1474). Refusing here rather than at the first
    pass is the same argument ``CollectionDefinition``'s builder validator
    makes: a run-time-only refusal looks like "that collection just stopped
    updating", hours later and in a log nobody is reading."""
    with pytest.raises(ValidationError) as caught:
        CollectionDefinition(
            title="Pasted", builder="smart_url", params={"url": "not a plex url"},
        )
    assert "smart_url" in str(caught.value)


def test_the_builder_refuses_the_same_definition_fields_smart_filter_does():
    """Global Constraint 3's structural half: the five keys are already in
    ``config/schema.py``'s ``_SMART_REFUSABLE_DEFAULTS``, which is what makes
    this builder shippable without touching that file. Asserted against
    ``smart_filter``'s own table rather than against a copied literal, so the
    two cannot drift."""
    assert set(SmartUrlBuilder.refused_definition_fields) == set(
        SmartFilterBuilder.refused_definition_fields
    )
    assert SmartUrlBuilder.smart is True
    # Not decoration: ``config/schema.py``'s
    # ``_params_must_satisfy_the_builders_own_model`` reads ``params_model``
    # off the registry entry, so a builder that lost it would accept any
    # params dict at config load and only fail at the first pass. (It also
    # keeps the ``SmartUrlParams`` import above used, which ruff's F rules
    # check.)
    assert SmartUrlBuilder.params_model is SmartUrlParams


# --- the engine-level proof --------------------------------------------------


class FakeChoice:
    def __init__(self, title, key):
        self.title = title
        self.key = key


class FakeItem:
    def __init__(self, rating_key):
        self.ratingKey = rating_key


class FakeServer:
    """``queries`` holds WRITES; ``reads`` holds the container-size-0 count."""

    def __init__(self, section=None):
        self.queries = []
        self.reads = []
        self._section = section
        self._session = type("Sess", (), {"post": "POST", "put": "PUT"})()

    def _uriRoot(self):
        return "server://abc123/com.plexapp.plugins.library"

    def query(self, key, method=None, headers=None, **kwargs):
        if method is None:
            self.reads.append((key, headers))
            return SimpleNamespace(attrib={"totalSize": "3"})
        self.queries.append((key, method))


class FakeCollection:
    def __init__(self, title, rating_key="12345"):
        self.title = title
        self.ratingKey = rating_key
        self.smart = True
        self.summary = None
        self.titleSort = None
        self.collectionMode = None
        self._real_labels = []
        self._labels = []
        self._real_fields = [type("F", (), {"name": "summary", "locked": False})()]
        self._fields = []
        self._server = FakeServer()

    @property
    def labels(self):
        return self._labels

    @property
    def fields(self):
        return self._fields

    def reload(self, **kw):
        self._labels = self._real_labels
        self._fields = self._real_fields

    def addLabel(self, label, locked=True):
        self._real_labels.append(type("L", (), {"tag": label})())
        self._labels = self._real_labels

    def editSortTitle(self, value, locked=True):
        self.titleSort = value

    def query(self, key, method=None, **kwargs):
        self._server.query(key, method)
        self._real_fields[0].locked = True


class FakeSection:
    def __init__(self):
        self.key = SECTION_KEY
        self._server = FakeServer(self)
        self._existing = {}

    def collections(self, **kw):
        return list(self._existing.values())

    def collection(self, title):
        if title not in self._existing:
            self._existing[title] = FakeCollection(title)
        return self._existing[title]

    def fetchItems(self, path, **kw):
        return [FakeItem(str(i)) for i in range(3)]

    def listFilterChoices(self, field, libtype=None):
        return [FakeChoice("Horror", "1138"), FakeChoice("Drama", "9")]


def _config(**overrides):
    options = {
        "ownership_label": LABEL, "apply_to_plex": True, "adopt": False,
        "adopt_from": ["Kometa"], "adopt_removes_prior_label": False,
        "protect_labels": [], "posters": False, "charts": False, "awards": False,
        "separators": False, "definitions": [], "presets": [],
        "libraries": ["Movies"], "delete_unconfigured": False, "max_deletes": 5,
        "enabled": True,
    }
    options.update(overrides)
    return SimpleNamespace(collections=SimpleNamespace(**options))


def _stored_uri(section, title):
    """The ``uri`` argument of the smart create POST for ``title``."""
    for key, method in section._server.queries:
        args = parse_qs(urlsplit(key).query)
        if args.get("title", [None])[0] == title:
            assert method == "POST"
            return args["uri"][0]
    raise AssertionError("no smart create POST for %r" % title)


async def test_a_pasted_url_and_the_equivalent_smart_filter_store_one_filter(
    session, caplog,
):
    """THE acceptance criterion for row 184's ``smart_url`` half. The pasted
    URL names section 1 and the library is section 2 -- the stored uri uses
    OUR section key, which is Kometa's own behaviour (``build_smart_filter``
    interpolates ``self.key``, plex.py:1615-1616) and is what lets an operator
    paste a URL copied from any client. Everything else must be byte-identical
    to what ``smart_filter`` builds from the same query, or the paste would be
    a second query grammar rather than a spelling of the one 9b proved.

    A token-bearing paste is refused at construction now (Global Constraint 8,
    re-ruled -- see the token-refusal tests above), so it can never reach
    ``CollectionDefinition`` here at all; this uses the token-free URL and
    keeps the caplog/action-string pins as a general hygiene check on what
    the engine logs and reports for an accepted definition."""
    section = FakeSection()
    pasted = CollectionDefinition(
        title="Pasted Horror", builder="smart_url", params={"url": _MOVIE_URL},
    )
    written = CollectionDefinition(
        title="Written Horror",
        builder="smart_filter",
        params={"all": {"genre": "Horror"}},
    )

    with caplog.at_level(logging.DEBUG):
        run = await run_library(
            session, section, "Movies", "Movie", [pasted, written], _config(),
        )

    assert not any(result.failed for result in run.definitions)
    stored_uri = _stored_uri(section, "Pasted Horror")
    assert stored_uri == (
        "server://abc123/com.plexapp.plugins.library"
        "/library/sections/2/all?type=1&sort=random&genre=1138"
    )
    assert stored_uri == _stored_uri(section, "Written Horror")
    # The hash the phase's storm guard short-circuits a pass on, not just
    # the uri -- the two must be recognised as the SAME desired state.
    assert smart_definition_hash(stored_uri, None, pasted, None) == smart_definition_hash(
        stored_uri, None, written, None
    )
    for record in caplog.records:
        assert _TOKEN not in record.getMessage()
    for action in run.actions:
        assert _TOKEN not in action


async def test_a_movie_url_on_a_show_library_is_refused_by_class_and_by_message(
    session, caplog,
):
    """The refusal the cell asks for that the extractor alone cannot make: the
    URL parses perfectly and is still wrong for THIS library. Contained to the
    one definition, reported as an action string, and it names the libtype the
    URL asked for -- the operator's own value, per Global Constraint 7.

    A token-bearing paste is refused at construction now (Global Constraint 8,
    re-ruled -- see the token-refusal tests above), so this uses the
    token-free URL; the caplog/action-string pins stay as a general hygiene
    check on the refusal's own action string and ``logger.warning`` line."""
    section = FakeSection()
    definition = CollectionDefinition(
        title="Pasted Horror", builder="smart_url", params={"url": _MOVIE_URL},
    )

    with caplog.at_level(logging.DEBUG):
        run = await run_library(
            session, section, "Shows", "Show", [definition], _config(libraries=["Shows"]),
        )

    [action] = run.actions
    assert "refused 'Pasted Horror'" in action
    assert "movie" in action and "Show library" in action
    assert section._server.queries == [], "nothing written to Plex"
    # The builder's own class, not a bare ValueError -- the engine's log line
    # carries the class name and nothing else.
    assert LibraryTypeMismatch in SmartUrlBuilder.REFUSALS
    assert _TOKEN not in action
    for record in caplog.records:
        assert _TOKEN not in record.getMessage()
