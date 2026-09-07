"""The Plex ``Section`` protocol, once, for the collections suite.

Seven files used to restate it -- comment for comment -- and the Common Sense
port had to grow the same two members in every one of them (roadmap row 191).
The protocol lives here; each file keeps a thin subclass for its own question.

Not collected: the filename has no ``test_`` prefix, and ``pyproject.toml``'s
``testpaths = ["tests"]`` leaves pytest's default ``test_*.py`` pattern in
place, so this module is importable but never gathered. The precedent for a
cross-module test import is already in-tree and is exactly this family
(``tests/test_collection_adoption.py:21-25``).
"""

from urllib.parse import parse_qs, urlsplit


class FakeChoice:
    """One ``listFilterChoices`` entry.

    Plex answers contentRating's key and title with the same string -- the
    10a-1 dynamic probe measured it (``dynamic_types.py``'s content_rating
    note) -- so the resolver is the identity here, which is also what the
    equivalence proof's driver assumes and says out loud. ``key`` is
    overridable for the one caller that needs them to differ.
    """

    def __init__(self, title, key=None):
        self.title = title
        self.key = title if key is None else key


class FakeSection:
    """A Plex library section, and its own ``_server``.

    Since phase 10a-2 a bucket's collection is created with a raw POST and its
    filter replaced with a raw PUT, both against ``section._server.query``,
    exactly as the separator's blank collection and every ``smart_filter``
    collection already were -- so this double plays both roles rather than
    needing a second fake object.

    ``createCollection`` is ALWAYS present and ALWAYS recording, even in the
    files whose production path no longer calls it. Its job there is to prove
    it is never called: a fake that simply lacked the method would fail an
    accidental call with an ``AttributeError`` several frames away, where
    recording it lets ``section.created == []`` say what is actually being
    asserted.

    Subclasses set ``collection_factory`` (required -- the suite's 24
    ``FakeCollection`` shapes stay where they are, deliberately) and, if the
    file's created collections carry an ownership label, ``created_labels``.
    """

    #: Called as ``collection_factory(title, ...)``; see ``_new_collection``.
    collection_factory = None
    #: ``labels=`` for collections made through ``createCollection``.
    created_labels = ()

    def __init__(
        self, *, items=(), existing=(), ratings=(), hubs=(), section_type="movie", matches=None
    ):
        self._items = list(items)
        self._ratings = list(ratings)
        self._hubs = list(hubs)
        self._existing = {c.title: c for c in existing}
        # ``smart.count_matches``' container-size read (no ``title`` in the
        # query, ``method=None``): how many items a smart filter matches
        # before anything is written. ``None`` means this file's scenarios
        # never reach it, and the read falls through to the write routes.
        self._matches = matches
        self.created = []
        self.queries = []
        self.key = "42"
        self.type = section_type
        self._server = self
        self._session = type(
            "Sess",
            (),
            {
                "post": "POST-SENTINEL",
                "put": "PUT-SENTINEL",
            },
        )()

    # -- collection construction, the one thing that is not shared ----------

    def _new_collection(self, title):
        """The collection a raw create POST makes."""
        return self.collection_factory(title, rating_key=str(len(self._existing) + 1))

    def _created_collection(self, title, items=None, **kw):
        """The collection plexapi's ``createCollection`` makes."""
        return self.collection_factory(
            title,
            labels=self.created_labels,
            rating_key=str(len(self.created)),
        )

    def _record_post(self, collection, args, key, method):
        """Hook for a file that records something about the create POST."""

    # -- the protocol -------------------------------------------------------

    def _uriRoot(self):
        return "server://FAKE-MACHINE-ID/com.plexapp.plugins.library"

    def all(self):
        return list(self._items)

    def item_for(self, key):
        return next(i for i in self._items if i.ratingKey == key)

    def query(self, key, method=None, headers=None, params=None, timeout=None, **kwargs):
        """Every raw section route.

        Three of them: the create POST (which carries a ``title``), a bucket's
        filter-replacing PUT (which carries only a ``uri``), and -- where the
        file opted in with ``matches`` -- ``count_matches``' container probe,
        which is a read (``method`` is None) and returns an attrib rather than
        being recorded as a write.
        """
        if method is None and self._matches is not None:
            return type(
                "Container",
                (),
                {
                    "attrib": {"totalSize": str(self._matches)},
                },
            )()
        self.queries.append({"key": key, "method": method})
        args = parse_qs(urlsplit(key).query)
        if "title" not in args:
            return None
        title = args["title"][0]
        collection = self._new_collection(title)
        self._existing[title] = collection
        self._record_post(collection, args, key, method)
        return None

    def collection(self, title):
        return self._existing[title]

    def collections(self, **kw):
        return list(self._existing.values())

    def listFilterChoices(self, field, libtype=None):
        return [FakeChoice(rating) for rating in self._ratings]

    def managedHubs(self):
        return list(self._hubs)

    def createCollection(
        self,
        title,
        items=None,
        smart=False,
        limit=None,
        libtype=None,
        sort=None,
        filters=None,
        **kw,
    ):
        self.created.append((title, smart, libtype, sort, filters))
        collection = self._created_collection(
            title,
            items=items,
            smart=smart,
            limit=limit,
            libtype=libtype,
            sort=sort,
            filters=filters,
            **kw,
        )
        self._existing[title] = collection
        return collection
