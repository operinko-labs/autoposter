"""Where a pasted list URL becomes a ``(builder, params)`` pair.

Server-side deliberately (custom-collections UI phase, facts C2): every URL
shape lives beside the params model it feeds, so what counts as "a valid IMDb
list id" exists once, in Python -- a TypeScript transcription would drift the
first time a builder's pattern moved. The endpoint wrapping this
(``api/collections_builders.py``) does nothing but call ``parse_source`` and
shape the refusal.

Shape-only, on purpose. Nothing here asks a provider whether the list exists:
validation at parse time matches validation at config load (the same params
models), and a list that does not exist is discovered the way it always has
been -- the first pass's builder raises, and sync semantics leave the
collection untouched. The form says so in as many words.

trakt is refused BY NAME rather than falling through the generic refusal:
row 202's fence says a pasted trakt URL has nothing to parse to because no
trakt builder is shipped, and an operator pasting one deserves that answer
rather than a list of hosts theirs is not among.
"""
import re
from dataclasses import dataclass
from urllib.parse import urlsplit

from pydantic import BaseModel, ValidationError

from autoposter.collections.builders.imdb_lists import (
    ImdbListParams,
    ImdbWatchlistParams,
)
from autoposter.collections.builders.mdblist import MdblistListParams
from autoposter.collections.builders.tmdb import TmdbEntityParams
from autoposter.collections.builders.tvdb import TvdbListParams

__all__ = ["ParsedSource", "SourceUrlRefused", "parse_source"]


class SourceUrlRefused(ValueError):
    """A paste this parser cannot turn into a shipped builder.

    The message is the whole point: operator-facing, and it either reuses the
    params model's own error string (a recognised shape with a bad value) or
    names what IS supported (an unrecognised shape).
    """


@dataclass(frozen=True)
class ParsedSource:
    """One recognised paste: the registry key, the validated params, and the
    sentence the form shows beside the resolved builder."""

    builder: str
    params: dict
    display_note: str


# What the generic refusals teach. One string, so every refusal names the same
# set and a builder added later is added here once.
SUPPORTED = (
    "imdb.com/list/ls… (imdb_list), imdb.com/user/ur… (imdb_watchlist), "
    "mdblist.com/lists/<user>/<slug> (mdblist_list), "
    "themoviedb.org's list/collection/company/network/keyword pages (tmdb_…), "
    "thetvdb.com/lists/<slug> (tvdb_list)"
)

# The TMDb entity family rides free (facts C2): one host, five path kinds, one
# params model. Disclosed per kind in the display note, because a company or
# network paste builds something broader than "the list at this URL".
_TMDB_KINDS: dict[str, tuple[str, str]] = {
    "list": ("tmdb_list", "a TMDb list, in list order"),
    "collection": (
        "tmdb_collection",
        "a TMDb franchise collection's films (Movie libraries only)",
    ),
    "company": ("tmdb_company", "everything TMDb credits to this production company"),
    "network": ("tmdb_network", "everything this TV network airs (Show libraries only)"),
    "keyword": ("tmdb_keyword", "everything TMDb tags with this keyword"),
}

_SCHEME = re.compile(r"^[a-z][a-z0-9+.-]*://", re.IGNORECASE)
_INTEGER = re.compile(r"^\d+$")
# A TMDb path segment's leading id: themoviedb.org writes
# /collection/10-star-wars-collection, and the digits before the first dash
# are the id.
_LEADING_ID = re.compile(r"^(\d+)(?:-|$)")

_IMDB_LIST_NOTE = "an IMDb list, in list order"
_IMDB_WATCHLIST_NOTE = "an IMDb user's public watchlist"
_MDBLIST_NOTE = "an MDBList list"


def _validated(
    builder: str, model: type[BaseModel], params: dict, display_note: str
) -> ParsedSource:
    """The params, held to the builder's own model -- or refused with the
    model's own error string, never a paraphrase.

    ``exclude_none`` so optional fields the parser never sets (mdblist's
    ``sort``/``order``, tvdb_list's unused ``id``) do not appear as explicit
    nulls in the definition the form goes on to write.
    """
    try:
        valid = model.model_validate(params)
    except ValidationError as error:
        # pydantic prefixes a field_validator's ValueError with "Value error, "
        # -- the model's own sentence, not pydantic's framing around it.
        raise SourceUrlRefused(
            "; ".join(
                item["msg"].removeprefix("Value error, ") for item in error.errors()
            )
        ) from error
    return ParsedSource(builder, valid.model_dump(exclude_none=True), display_note)


def _parse_imdb(segments: list[str]) -> ParsedSource:
    if len(segments) >= 2 and segments[0] == "list":
        return _validated(
            "imdb_list", ImdbListParams, {"list": segments[1]}, _IMDB_LIST_NOTE
        )
    if len(segments) >= 2 and segments[0] == "user":
        return _validated(
            "imdb_watchlist", ImdbWatchlistParams, {"user": segments[1]},
            _IMDB_WATCHLIST_NOTE,
        )
    raise SourceUrlRefused(
        "that imdb.com page is not a list or a user page -- supported: " + SUPPORTED
    )


def _parse_mdblist(segments: list[str]) -> ParsedSource:
    if len(segments) >= 3 and segments[0] == "lists":
        return _validated(
            "mdblist_list", MdblistListParams,
            {"list": f"{segments[1]}/{segments[2]}"},
            _MDBLIST_NOTE,
        )
    raise SourceUrlRefused(
        "that mdblist.com page is not a list -- supported: " + SUPPORTED
    )


def _parse_tmdb(segments: list[str]) -> ParsedSource:
    if len(segments) >= 2 and segments[0] in _TMDB_KINDS:
        builder, note = _TMDB_KINDS[segments[0]]
        match = _LEADING_ID.match(segments[1])
        identifier = match.group(1) if match else segments[1]
        return _validated(builder, TmdbEntityParams, {"id": identifier}, note)
    raise SourceUrlRefused(
        "that themoviedb.org page is not a list, collection, company, network "
        "or keyword -- supported: " + SUPPORTED
    )


def _parse_tvdb(segments: list[str]) -> ParsedSource:
    if len(segments) >= 2 and segments[0] == "lists":
        return _validated(
            "tvdb_list", TvdbListParams, {"slug": segments[1]}, "a TVDb list"
        )
    raise SourceUrlRefused(
        "that thetvdb.com page is not a list -- supported: " + SUPPORTED
    )


def _refuse_trakt(_segments: list[str]) -> ParsedSource:
    raise SourceUrlRefused(
        "trakt.tv is not a supported source: no trakt builder is shipped, so "
        "a pasted trakt URL has nothing to parse to -- trakt support is its "
        "own roadmap gap, not this form's. Supported: " + SUPPORTED
    )


_HOSTS = {
    "imdb.com": _parse_imdb,
    "m.imdb.com": _parse_imdb,
    "mdblist.com": _parse_mdblist,
    "themoviedb.org": _parse_tmdb,
    "thetvdb.com": _parse_tvdb,
    "trakt.tv": _refuse_trakt,
}


def _parse_bare(value: str) -> ParsedSource:
    """The four bare shapes (facts C2), accepted where unambiguous.

    Told apart by shape rather than by the order of the tests below: MDBList's
    ``<user>/<slug>`` is the only one carrying a ``/``, so it is recognised
    first and an MDBList user whose name begins ``ls``/``ur`` is still a user
    name here rather than a mangled IMDb id.

    Residual narrowing, honestly stated: the ``"." not in head`` heuristic
    reads a dotted first segment as an unknown host, not a user name, so a
    real MDBList user whose name contains a dot (``a.b/c``, valid per
    ``MdblistListParams``' own pattern) is refused here rather than accepted.
    The escape hatch is the full URL (``mdblist.com/lists/a.b/c``), which does
    not go through this heuristic at all.
    """
    head, _, _tail = value.partition("/")
    if "/" in value and (":" in head or "@" in head):
        # A head carrying userinfo or a port (``user:pass@localhost/x``) is
        # not an MDBList reference and must never be offered to
        # ``MdblistListParams``, whose own error string interpolates
        # ``{value!r}`` -- which is how a schemeless credential paste to a
        # DOTLESS host used to come back with the password in it. Nothing
        # valid is refused here: ``_SEGMENT`` (``builders/mdblist.py:58``)
        # admits neither ':' nor '@' in either half of ``<user>/<slug>``, so
        # the guard is lossless by MDBList's own grammar. The scheme-ful and
        # protocol-relative shapes of the same paste are already caught by
        # the unknown-host gate in ``parse_source``; this is the schemeless
        # one, which never reaches it.
        raise SourceUrlRefused(
            "that looks like a URL to a host this form does not know (its "
            "first segment carries a ':' or an '@'), and it is not repeated "
            "back in case it carries credentials -- supported: " + SUPPORTED
        )
    if "?" in value or "#" in value:
        # The same defect class as the guard above, one character away from
        # being covered by it. A bare id and an MDBList ``<user>/<slug>`` have no
        # query and no fragment -- ``_SEGMENT``, ``_LIST_ID``, ``_USER_ID`` and
        # ``_INTEGER`` admit neither character -- so nothing valid is refused
        # here, and every URL shape that legitimately carries one
        # (``imdb.com/list/ls1?ref_=hm``) is resolved by the ``_HOSTS`` table
        # and never reaches this function. What DID reach it was
        # ``abc/def?apikey=SECRET``, whose head is dotless and carries no ':'
        # or '@', so it passed the guard above and went to
        # ``MdblistListParams``, whose error string used to interpolate it.
        raise SourceUrlRefused(
            "that carries a query string or a fragment ('?' or '#'), which no "
            "bare id or MDBList reference does, and it is not repeated back in "
            "case it carries credentials -- supported: " + SUPPORTED
        )
    if "/" in value and "." not in head:
        # user/slug, MDBList's own two-part reference. A dotted first segment
        # reads as a host this parser does not know, not as a user name.
        return _validated(
            "mdblist_list", MdblistListParams, {"list": value}, _MDBLIST_NOTE
        )
    lowered = value.lower()
    if lowered.startswith("ls"):
        return _validated(
            "imdb_list", ImdbListParams, {"list": value}, _IMDB_LIST_NOTE
        )
    if lowered.startswith("ur"):
        return _validated(
            "imdb_watchlist", ImdbWatchlistParams, {"user": value},
            _IMDB_WATCHLIST_NOTE,
        )
    if _INTEGER.match(value):
        # The one bare shape more than one builder could claim (TMDb, TVDb
        # and MDBList all take a numeric list id). Read as TMDb -- the
        # flagship /list/<id> URL among the sources -- and the note says so,
        # so an operator who meant another service knows to paste its URL.
        return _validated(
            "tmdb_list", TmdbEntityParams, {"id": value},
            "a TMDb list, in list order -- a bare number is read as a TMDb "
            "list id; paste the full URL for an MDBList or TVDb list",
        )
    raise SourceUrlRefused(
        "that is not a URL or a bare id this form recognises. Supported "
        "URLs: " + SUPPORTED + ". Bare values accepted: an IMDb ls…/ur… id, "
        "an MDBList <user>/<slug>, or a TMDb numeric list id."
    )


def parse_source(text: str) -> ParsedSource:
    """The ``(builder, params)`` a pasted source resolves to, or a refusal.

    Dispatch is by host for anything URL-shaped, then by bare shape. Every
    accepted parse goes through the builder's own params model, so what this
    returns is exactly what config validation will accept -- and every value
    refusal is that model's own error string.
    """
    value = text.strip()
    if not value:
        raise SourceUrlRefused("paste a list URL -- supported: " + SUPPORTED)
    candidate = value if _SCHEME.match(value) else "https://" + value
    try:
        parts = urlsplit(candidate)
    except ValueError:
        # ``urlsplit`` refuses a few pastes outright -- a '[' reads as the
        # start of an IPv6 literal. Refused here rather than fed to the bare
        # shapes, which would answer a broken URL with MDBList's error string.
        # What it must not do either way is reach the operator as a traceback.
        raise SourceUrlRefused(
            "that is not a URL this form can read -- supported: " + SUPPORTED
        ) from None
    host = (parts.hostname or "").lower().removeprefix("www.")
    handler = _HOSTS.get(host)
    if handler is not None:
        return handler([segment for segment in parts.path.split("/") if segment])
    if _SCHEME.match(value) or value.startswith("//"):
        # A real (scheme-ful or protocol-relative) URL to a host this parser
        # does not know -- dotted or not: ``localhost``, ``[::1]`` and an
        # empty host (``https:///x``) are all refused here now too, rather
        # than falling into the bare shapes below and drawing MDBList's
        # error string about a value that was never an MDBList reference.
        raise SourceUrlRefused(
            # The host is not echoed: this is the 422 detail
            # collections_builders.py serves back, and the paste can be an
            # intranet address the operator did not mean to publish.
            "that host is not a supported source -- supported: " + SUPPORTED
        )
    return _parse_bare(value)
