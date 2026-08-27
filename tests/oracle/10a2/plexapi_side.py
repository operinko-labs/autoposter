"""The OLD side: plexapi's own ``_buildSearchKey``, driven for real.

Not a transcription. ``LibrarySection._buildSearchKey`` ->
``_validateAdvancedSearch`` -> ``_validateFilterField`` -> ``_validateFieldOperator``
/ ``_validateFieldValue`` -> ``_validateFieldValueTag`` is the exact path
``section.createCollection(smart=True, filters={"contentRating": [...]})`` and
``collection.updateFilters(filters={"contentRating": [...]})`` take today
(``reconcile.py:648-658``), and running it is the only way to learn what it
emits per library type -- including whether the field name comes back bare or
``<libtype>.``-prefixed, which premise P4 in ``member_sets.py`` is about and
which this driver MEASURES rather than assumes.

Line references are to the INSTALLED copy, ``plexapi 4.18.2``,
``/usr/local/lib/python3.14/site-packages/plexapi/library.py``:

    _buildSearchKey          :1259-1292
    _validateAdvancedSearch  :1229-1257
    _validateFilterField     :1071-1103   (``','.join(result)`` at :1102 -- P1)
    _validateFieldOperator   :1105-1126
    _validateFieldValue      :1128-1155
    _validateFieldValueTag   :1168-1181
    _validateSortFields      :1183-1198

The section is a real ``LibrarySection`` with its filter metadata stubbed: the
four lookups that path makes (``listFields``, ``getFieldType``,
``listFilterChoices``, ``listSorts``) are answered from the fixture below, and
nothing reaches a network. ``self.key`` is the fake section number ``42``;
there is no host and no token anywhere in what this produces.

Loaded by PATH by the gate, and it deliberately does not import ``ours_side``
-- the two drivers meet only in the gate, so neither can lend the other a
shared helper that could hide a shared bug.
"""
from types import SimpleNamespace

from plexapi.library import LibrarySection


class _Section(LibrarySection):
    """``LibrarySection`` with the four filter-metadata lookups stubbed.

    Subclassed rather than faked so ``_buildSearchKey`` and everything under it
    is plexapi's real code -- a hand-written stand-in would be a transcription,
    and a transcription is what this whole task exists to avoid. ``__init__``
    does not call up: ``PlexObject.__init__`` wants a server and an XML
    element, and the search-key path reads exactly two attributes, ``key`` and
    ``TYPE``.

    ``listFilterChoices`` answers with ``key == title``, which is what a real
    Plex server answers for ``contentRating`` (measured by the 10a-1 probe) and
    is why ``_validateFieldValueTag`` returns the written rating unchanged.
    """

    def __init__(self, key, libtype, field_key, ratings):
        self.key = key
        self.TYPE = libtype
        self._field_key = field_key
        self._ratings = list(ratings)

    def listFields(self, libtype=None):
        return [SimpleNamespace(key=self._field_key, type="tag")]

    def getFieldType(self, fieldType):
        return SimpleNamespace(type="tag", operators=[SimpleNamespace(key="=")])

    def listOperators(self, fieldType):
        return self.getFieldType(fieldType).operators

    def listFilterChoices(self, field, libtype=None):
        return [SimpleNamespace(key=one, title=one) for one in self._ratings]

    def listSorts(self, libtype=None):
        return [SimpleNamespace(key="originallyAvailableAt")]


def old_query(libtype: str, values, ratings, field_key: str) -> str:
    """The query string plexapi builds for one bucket's ratings.

    ``sort`` is ``reconcile.SORT`` verbatim and ``filters`` is the dict
    ``reconcile.py:650`` passes verbatim. Returned from ``?`` onward, to match
    ``build_search_url``'s shape.

    ``field_key`` is a parameter deliberately: whether a Plex section answers
    ``/library/sections/N/filters`` with a bare ``contentRating`` or a dotted
    ``<libtype>.contentRating`` is a fact about the SERVER, and this driver
    must not decide it. ``_validateFilterField`` matches on
    ``key.split('.')[-1]`` (:1082) and then emits ``filterField.key`` whole
    (:1094), so whichever spelling the section reports is the spelling in the
    query -- the gate runs both for the show case.
    """
    section = _Section("42", libtype, field_key, ratings)
    key = section._buildSearchKey(
        sort="originallyAvailableAt:desc", libtype=libtype,
        filters={"contentRating": list(values)},
    )
    return "?" + key.partition("?")[2]
