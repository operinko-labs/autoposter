"""THE SMART-ENVELOPE ORACLE -- phase 9c's acceptance.

9b proved the QUERY. This proves the ENVELOPE around it: the ``uri`` Kometa
hands Plex when it creates or updates a smart collection, and the two request
keys that carry it. The envelope matters on its own because ``joinArgs``
percent-encodes the whole ``uri`` VALUE, so every ``%3A`` inside a 9b golden
becomes ``%253A``, every ``%20`` becomes ``%2520`` and every ``!`` becomes
``%21``. An envelope missing one level of encoding is a URL Plex still answers,
with a different set -- the silent wrongness this phase exists to exclude.

Provenance (fetched 2026-08-26):

  Kometa v2.4.8, modules/plex.py:1580-1584     Plex.test_smart_filter
  Kometa v2.4.8, modules/plex.py:1592-1600     Plex.create_smart_collection
  Kometa v2.4.8, modules/plex.py:1615-1616     Plex.build_smart_filter
  Kometa v2.4.8, modules/plex.py:1618-1620     Plex.update_smart_collection
  Kometa v2.4.8, modules/builder.py:1476-1483  the smart_filter call site
  plexapi 4.18.2, plexapi/utils.py             joinArgs
  plexapi 4.18.2, plexapi/server.py            PlexServer._uriRoot

``joinArgs`` and ``_uriRoot`` are plexapi's, not Kometa's -- Kometa reaches for
both and defines neither (``utils.joinArgs``, ``self.PlexServer._uriRoot()``).
They are transcribed here rather than imported so this file imports nothing but
the standard library, and ``test_the_transcribed_joinArgs_matches_plexapis``
holds the transcription against the installed plexapi by value.

NOTHING from the autoposter repository is imported. Nothing from
``tests/oracle/9b/`` is imported either: the ``uri_args`` this file wraps are
supplied by its caller, so the two oracles stay independent and the test file is
the only thing that knows about both.

SIGNATURE CHANGES, all of them the same removal -- there is no ``Plex`` object
here, so everything upstream reads off ``self`` is a parameter instead:
``self.Plex.key`` becomes ``section_key``, ``self.PlexServer._uriRoot()``
becomes the transcribed ``uri_root()``, and ``update_smart_collection``'s
``collection`` (read only for ``collection.ratingKey``) becomes ``rating_key``.
``test_smart_filter``'s own Plex read -- ``self.fetchItems(uri_args)``, whose
length is the whole of upstream's verdict -- becomes ``item_count``, the count
the caller supplies; the VERDICT is what this file records, not the read.
``create_smart_collection``'s ``ignore_blank_results`` is positional and
undefaulted upstream; it defaults to False here so the refusing path -- the one
9c adopts -- is what a plain call gets.

Run:  python kometa_smart_collection.py
      -> prints the three pinned shapes for one demo query
"""
from urllib.parse import quote as _urllib_quote

# The server identity, fixed. A real ``_uriRoot()`` interpolates the server's
# machineIdentifier; pinning a literal is what keeps this comparison about
# ENCODING rather than about one server, and it is also why no committed file
# in this phase carries a real server address.
MACHINE_IDENTIFIER = "abc123"
SECTION_KEY = "2"


class Failed(Exception):
    """Kometa's own control-flow exception (modules/util.py). Its message text
    is Kometa's, verbatim, because the refusal wording is part of what this
    oracle records -- ours is deliberately different, and Task 2 says why."""


def quote(value):
    """plexapi's ``joinArgs`` quotes with ``safe=''`` -- every reserved
    character encoded, ``/`` and ``:`` included, and ``%`` itself encoded to
    ``%25``, which is where the double encoding comes from."""
    return _urllib_quote(str(value), safe="")


def joinArgs(args):
    """plexapi 4.18.2 ``plexapi/utils.py::joinArgs``, transcribed.

    The camelCase name is plexapi's own, kept so the name reads as a citation.

    Two details are load-bearing and neither is guessable from the output: the
    keys are sorted **case-insensitively** (``sectionId``, ``smart``, ``title``,
    ``type``, ``uri``), and only the VALUE is encoded, never the key.
    """
    if not args:
        return ""
    arglist = []
    for key in sorted(args, key=lambda x: x.lower()):
        value = str(args[key])
        arglist.append(f"{key}={quote(value)}")
    return f"?{'&'.join(arglist)}"


def uri_root():
    """plexapi 4.18.2 ``plexapi/server.py::PlexServer._uriRoot``."""
    return f"server://{MACHINE_IDENTIFIER}/com.plexapp.plugins.library"


def build_smart_filter(uri_args, section_key=SECTION_KEY):
    """modules/plex.py:1615-1616, verbatim.

    Note what it is NOT: it is not a URL a client fetches. It is a ``server://``
    uri Plex STORES and evaluates itself, which is the whole difference between
    a smart collection and a list one.
    """
    return f"{uri_root()}/library/sections/{section_key}/all{uri_args}"


def test_smart_filter(item_count, uri_args):
    """modules/plex.py:1580-1584.

    REMOVED: ``self.fetchItems(uri_args)``, the network read -- the caller
    supplies the count it would have returned. What is transcribed is the
    VERDICT, because the verdict is the behaviour 9c adopts (C8): fewer than one
    item is a refusal, not an empty collection.
    """
    if item_count < 1:
        raise Failed(f"Plex Error: No items for smart filter: {uri_args}")


def create_smart_collection(
    title, smart_type, uri_args, item_count, ignore_blank_results=False,
    section_key=SECTION_KEY,
):
    """modules/plex.py:1592-1600, as the request KEY it would POST.

    REMOVED: ``self._collection_by_title(title)`` and its "already exists;
    skipping creation" warning (:1593-1596) -- a Plex read, and this service
    resolves a title collision through ``reconcile.resolve_collision`` instead,
    which is a strictly stronger rule (ownership label, protected label,
    adoption) rather than a skip. REMOVED: ``self._query(..., post=True)``, the
    transport; the key is returned so it can be compared as a string.

    ``ignore_blank_results`` is transcribed because it is what Kometa's flow
    DOES, and 9c refuses to offer it (C8) -- recording the switch here is what
    makes that refusal a deliberate divergence rather than an omission.
    """
    if not ignore_blank_results:
        test_smart_filter(item_count, uri_args)
    args = {
        "type": smart_type,
        "title": title,
        "smart": 1,
        "sectionId": section_key,
        "uri": build_smart_filter(uri_args, section_key),
    }
    return f"/library/collections{joinArgs(args)}"


def update_smart_collection(rating_key, uri_args, item_count, section_key=SECTION_KEY):
    """modules/plex.py:1618-1620, as the request KEY it would PUT.

    Upstream calls ``test_smart_filter`` UNCONDITIONALLY here -- the
    ``ignore_blank_results`` escape hatch exists only on the create path. That
    is why 9c refusing at both is the stricter reading of upstream rather than a
    departure from it.
    """
    test_smart_filter(item_count, uri_args)
    args = {"uri": build_smart_filter(uri_args, section_key)}
    return f"/library/collections/{rating_key}/items{joinArgs(args)}"


def main():
    demo = "?type=1&sort=titleSort&contentRating=5&and=1&contentRating=7"
    print("uri  ", build_smart_filter(demo))
    print("post ", create_smart_collection("Oracle Collection", 1, demo, 7))
    print("put  ", update_smart_collection("12345", demo, 7))


if __name__ == "__main__":
    main()
