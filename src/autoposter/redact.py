"""The one served-string URL redaction.

Action strings are built by the collections and playlists passes, and not all of
them are safe to echo: a provider step reports the source it could not fetch,
and provider URLs carry credentials often enough that none of them reaches a
response. The same rule the engine applies to a builder's exception one layer
in.

One home rather than two. ``api/playlists.py`` and
``api/collections_builders.py`` each carried a byte-identical private copy of
the pattern and the substitution until this module existed, and the playlists
user-sync phase (98c) was about to import one of them from a third place. Two
copies of a redaction rule is how a redaction rule drifts -- one gets a
tightened character class, the other does not, and the served surfaces stop
agreeing about what a URL is. ``tests/test_redact.py`` keeps it at one.

**This is not the ``/api/logs`` scrub.** ``api/logs.py::_scrub`` is a different,
five-pattern vocabulary -- credential params (plain and percent-encoded), URL
authorities (plain and percent-encoded), and the scheme-less ``host=``/``port=``
shape ``requests`` writes for its own connection target -- applied to a
FORMATTED log record, traceback included, at the ``LogBuffer`` seam. It has one
consumer and its own doctrine (roadmap rows 117/207/212/214) and it deliberately
stays there. The two vocabularies also disagree on purpose: that one keeps the
path (``http://REDACTED/library/sections/9/all``) because a served LOG line has
to stay debuggable; this one takes the URL whole, because a served ACTION string
does not need it.

**And it is not a pod-log redactor.** Roadmap row 207 adjudicated the pod log as
the TRUSTED sink: ``exc_info`` tracebacks reach stdout whole, keys included,
because that unscrubbed copy is the compensating control rows 136/188 lean on --
pinned by ``tests/test_scheduler_collections_job.py``, which asserts a token IS
present in ``caplog.text``. Nothing in this module is installed on a logger, a
handler or a formatter, and nothing here should ever be.
"""
import re

__all__ = ["URL_PATTERN", "redact_urls"]

# ``\S+`` deliberately: a URL runs to the next whitespace, and an action string
# is a sentence with spaces in it. Exported so a consumer needing to DETECT a
# URL never compiles a second copy.
URL_PATTERN = re.compile(r"https?://\S+")


def redact_urls(text: str) -> str:
    """``text`` with every URL replaced whole by the literal ``<url>``.

    The whole URL, not just its credential: an operator reading a served action
    does not need the host to know which step failed, and a partial redaction is
    how a credential nested inside another URL's query value survives. Every
    other character is returned unchanged -- a redaction is "same text, less of
    it", never a transform.
    """
    return URL_PATTERN.sub("<url>", text)
