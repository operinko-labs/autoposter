"""The per-library commit boundary in ``python -m autoposter.collections``.

Moving the commit inside the loop (rather than once after every configured
library) matters because a failure partway through must not roll back a
library that already succeeded and already wrote to Plex -- without this,
the hash gate never gets to see that library again and it is rewritten in
full on every subsequent run.
"""
from types import SimpleNamespace

from sqlalchemy import select

from autoposter.collections.__main__ import _reconcile_libraries
from autoposter.db.models import ManagedCollection

LABEL = "autoposter"


class FakeChoice:
    def __init__(self, title):
        self.title = title


class FakeCollection:
    def __init__(self, title, rating_key="1"):
        self.title = title
        self.ratingKey = rating_key
        self._labels = []

    @property
    def labels(self):
        return self._labels

    def reload(self, **kw):
        pass

    def updateFilters(self, **kw):
        pass

    def editSummary(self, summary, locked=True):
        pass

    def addLabel(self, labels, locked=True):
        self._labels.append(type("L", (), {"tag": labels})())


class FakeSection:
    def __init__(self, ratings, section_type="movie"):
        self._ratings = list(ratings)
        self._existing = {}
        self.type = section_type

    def listFilterChoices(self, field, libtype=None):
        return [FakeChoice(r) for r in self._ratings]

    def collections(self, **kw):
        return list(self._existing.values())

    def createCollection(self, title, items=None, smart=False, limit=None,
                          libtype=None, sort=None, filters=None, **kw):
        collection = FakeCollection(title, rating_key=str(len(self._existing) + 1))
        self._existing[title] = collection
        return collection


class BreaksOnSecondLibrary:
    """``server.library.section()`` returns a section for known names and
    raises for anything else -- simulating a Plex read failure partway
    through the configured libraries."""

    class _Library:
        def __init__(self, sections):
            self._sections = sections

        def section(self, name):
            if name in self._sections:
                return self._sections[name]
            raise RuntimeError("simulated failure fetching %r" % name)

    def __init__(self, sections):
        self.library = self._Library(sections)


def _config(libraries):
    return SimpleNamespace(
        collections=SimpleNamespace(
            libraries=libraries, ownership_label=LABEL, apply_to_plex=True,
        )
    )


async def test_a_failure_on_the_second_library_does_not_roll_back_the_first(
    session, session_factory
):
    server = BreaksOnSecondLibrary({"Movies": FakeSection({"R", "17"})})
    config = _config(["Movies", "TV Shows"])

    raised = False
    try:
        await _reconcile_libraries(session, server, config)
    except RuntimeError:
        raised = True
    assert raised, "expected the simulated failure fetching the second library"

    async with session_factory() as verify:
        rows = (await verify.execute(select(ManagedCollection))).scalars().all()
        assert any(r.title == "Age 17+ Movies" and r.library == "Movies" for r in rows), (
            "the first library's row must already be committed -- not rolled back "
            "by a failure on the second -- or a bare commit() at the end of the "
            "loop would never run at all"
        )
