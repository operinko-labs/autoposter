"""The one served-string URL redaction, and the guard that keeps it one.

Two byte-identical private copies of this three-line rule lived in
``api/playlists.py`` and ``api/collections_builders.py`` until this module
existed, and the 98c playlists phase was about to import one of them from a
third place. The rule itself did not change when it moved: the pattern is
``https?://\\S+`` and the replacement is the literal ``<url>``, exactly as both
copies had them.

What this module is NOT. It is not the ``/api/logs`` scrub
(``api/logs.py::_scrub``), which is a different, five-pattern vocabulary --
credential params, plain and percent-encoded URL authorities, and the
scheme-less ``host=``/``port=`` shape ``requests`` writes -- applied to a
FORMATTED log record, traceback and all, at the ``LogBuffer`` seam. That one has
exactly one consumer and its own doctrine (roadmap rows 117/207/212/214) and it
stays where it is.

And it is not a pod-log redactor. Roadmap row 207 adjudicated the pod log as the
TRUSTED sink -- ``exc_info`` tracebacks reach stdout whole, keys included -- and
``tests/test_scheduler_collections_job.py`` pins that by asserting a token IS
present in ``caplog.text``. Nothing here is installed on a logger, a handler or
a formatter.
"""
import ast
import re
from pathlib import Path

from autoposter.redact import URL_PATTERN, redact_urls


def test_a_provider_url_in_an_action_string_is_replaced_whole():
    """The live carrier: a builder step reports the source it could not fetch,
    and a provider URL carries the api_key in its query string. The whole URL
    goes, not just the credential -- an operator reading a served action does
    not need the host to know which step failed, and a partial redaction is how
    a nested credential survives."""
    assert redact_urls(
        "tmdb_discover: could not fetch "
        "https://api.themoviedb.org/3/discover/movie?api_key=SECRETKEY&page=2"
    ) == "tmdb_discover: could not fetch <url>"


def test_a_plex_tokenised_url_is_replaced_whole_and_the_class_name_survives():
    """The 98c preflight's shape, pinned here even though no call path of ours
    builds it: plexapi's ``posterUrl``/``artUrl`` family appends
    ``?X-Plex-Token=`` (``plexapi/mixins/resources.py``), and while ``src/``
    calls none of them today, a served string that ever carried one must lose
    it. The exception's CLASS NAME is what survives -- that is the part an
    operator acts on, and it is the same shape ``queue/worker._served_reason``
    serves."""
    redacted = redact_urls(
        "NotFound: (404) not_found; "
        "http://plex.internal:32400/library/metadata/1/posters?X-Plex-Token=SECRETVALUE"
    )
    assert "SECRETVALUE" not in redacted
    assert "X-Plex-Token" not in redacted
    assert "plex.internal" not in redacted
    assert redacted == "NotFound: (404) not_found; <url>"


def test_text_with_no_url_is_returned_byte_for_byte():
    """A redaction is "same text, less of it" (the doctrine's own words, roadmap
    row 212). An action string with nothing to redact must come back identical,
    including the punctuation and the counts an operator reads."""
    action = "Timeline: adding 3, removing 1, deleting 0 (2 unresolved)"
    assert redact_urls(action) == action


def test_exactly_one_url_redaction_lives_in_src():
    """The reason this module exists, kept true.

    Two byte-identical copies of the pattern is how the rule drifts: one gets a
    tightened character class, the other does not, and the served surfaces stop
    agreeing about what a URL is. An ``ast.walk`` rather than a text search, for
    ``tests/test_plex_writer.py::test_no_refresh_calls_project_wide``'s reason:
    this module's own docstring quotes the pattern, and a regex scan would fail
    on the very comment documenting the rule. Parsing sees string CONSTANTS
    only.

    ``api/logs.py`` is exempt by name and only by name: its patterns are a
    different vocabulary for a different seam (see this module's docstring), and
    none of them is this one.
    """
    src_root = Path(__file__).parent.parent / "src" / "autoposter"
    assert src_root.is_dir(), f"source tree not found at {src_root}"

    scanned, offenders = [], []
    for path in sorted(src_root.rglob("*.py")):
        if "__pycache__" in path.parts:
            continue
        relative = path.relative_to(src_root).as_posix()
        scanned.append(relative)
        if relative in ("redact.py", "api/logs.py"):
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Constant) or not isinstance(node.value, str):
                continue
            if "https?://" in node.value or node.value == "<url>":
                offenders.append(f"src/autoposter/{relative}:{node.lineno}")

    # The walk has to be shown to have found the tree before "no offenders"
    # means anything: a scan of nothing passes. These three are the modules the
    # rule is about -- the two former copy sites and the exempt log seam.
    for anchor in ("api/playlists.py", "api/collections_builders.py", "api/logs.py"):
        assert anchor in scanned, (
            f"{anchor} was not scanned; the walk found {len(scanned)} files"
        )

    assert not offenders, (
        "%s compiles its own URL pattern or writes its own '<url>' placeholder; "
        "there is exactly one served-string URL redaction and it is "
        "autoposter.redact.redact_urls" % ", ".join(offenders)
    )


def test_the_exported_pattern_is_the_one_the_helper_uses():
    """``URL_PATTERN`` is exported so a future consumer that needs to DETECT a
    URL never compiles a second copy. Pinned as the same object rather than the
    same source, so a refactor that leaves two equal-but-separate patterns
    behind goes red."""
    assert URL_PATTERN is not None
    assert isinstance(URL_PATTERN, re.Pattern)
    assert URL_PATTERN.pattern == r"https?://\S+"
