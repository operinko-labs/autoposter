"""Row 49: one blank divider per active collection group.

The lifecycle is BY DESIGN, and it is the part worth reading twice. A group
that stops having collections -- its last definition removed, its preset
switched off, or ``collections.separators`` turned off -- makes its separator an
ORDINARY delete-sweep candidate: nothing here deletes it, and it goes through
every guard the sweep applies to any other orphan (the ownership label AND a
managed row AND no protected label AND ``delete_unconfigured`` AND
``max_deletes``). With the sweep off, which is the default, it is reported and
left standing.
"""
import io

import httpx
from PIL import Image
from sqlalchemy import select

from autoposter.collections import groups
from autoposter.collections.posters import apply_poster, hosted_poster_url
from autoposter.collections.reconcile import reconcile_separator, separator_hash
from autoposter.db.models import ManagedCollection

LABEL = "autoposter"

SHIPPED_TITLE = "Ratings Collections"
SHIPPED_SUMMARY = "Section separator for Ratings Collections."
SHIPPED_SORT_TITLE = "!110_!Ratings Collections"
# The constant this module shipped, as a literal. The function has to reproduce
# it byte for byte or every live server takes a spurious re-write on the first
# pass after row 49 -- the hash is what a pass short-circuits on.
SHIPPED_HASH = "e22a14288c7962ea13a31e307aedf7e1ca204f6bf93238d65d0e436dcd5a4fb5"


def test_separator_hash_reproduces_the_shipped_constant_byte_for_byte():
    assert separator_hash(SHIPPED_TITLE, SHIPPED_SUMMARY, SHIPPED_SORT_TITLE) == SHIPPED_HASH


def test_a_new_section_number_changes_the_hash_exactly_once():
    now = separator_hash(SHIPPED_TITLE, SHIPPED_SUMMARY, "!030_!Ratings Collections")
    assert now != SHIPPED_HASH
    assert now == separator_hash(SHIPPED_TITLE, SHIPPED_SUMMARY, "!030_!Ratings Collections")


class FakeLabel:
    def __init__(self, tag):
        self.tag = tag


class FakeField:
    def __init__(self, name, locked):
        self.name, self.locked = name, locked


class FakeServer:
    def __init__(self):
        self.queries = []
        self._session = type("S", (), {"post": "POST", "put": "PUT"})()

    def _uriRoot(self):
        return "server://FAKE/com.plexapp.plugins.library"

    def query(self, path, method=None):
        self.queries.append((path, method))


class FakeCollection:
    def __init__(self, title, labels=(), rating_key="1", summary="", sort_title=""):
        self.title = title
        self.ratingKey = rating_key
        self.summary = summary
        self.titleSort = sort_title
        self._real_labels = [FakeLabel(t) for t in labels]
        self.labels = []
        self.fields = [FakeField("summary", False)]
        self._server = FakeServer()
        self.labels_added = []
        self.sort_title_set = None
        self.uploaded = []

    def reload(self):
        self.labels = list(self._real_labels)

    def addLabel(self, tag):
        self.labels_added.append(tag)
        self._real_labels.append(FakeLabel(tag))
        self.labels = list(self._real_labels)

    def removeLabel(self, tag):
        self._real_labels = [x for x in self._real_labels if x.tag != tag]
        self.labels = list(self._real_labels)

    def editSortTitle(self, value):
        self.sort_title_set = value
        self.titleSort = value

    def uploadPoster(self, filepath):
        with open(filepath, "rb") as handle:
            self.uploaded.append(handle.read())

    def lockPoster(self):
        pass


class FakeSection:
    def __init__(self, collections=()):
        self.key = 42
        self._server = FakeServer()
        self._collections = {c.title: c for c in collections}
        self.created = []

    def collections(self):
        return list(self._collections.values())

    def collection(self, title):
        made = FakeCollection(title)
        self._collections[title] = made
        self.created.append(title)
        return made


def spec(group="charts", order=groups.CANONICAL_ORDER):
    return groups.SeparatorSpec(
        group=group,
        title=groups.separator_title(group),
        summary=groups.separator_summary(group),
        sort_title=groups.separator_sort_title(group, order),
        poster_key=groups.SEPARATOR_POSTER_KEYS.get(group),
    )


async def run(session, section, target, existing=None, stored=None, dry_run=False,
              adopt=False, adopt_from=(), protect_labels=(), http=None, config=None):
    # ``session`` is tests/conftest.py's own AsyncSession fixture (pytest's
    # asyncio_mode is "auto" in pyproject.toml:69, so no marker is needed).
    return await reconcile_separator(
        session, section, "Movies", "movie", LABEL, target,
        existing if existing is not None else {},
        stored if stored is not None else {},
        adopt, list(adopt_from), False, dry_run, list(protect_labels),
        http, config,
    )


async def test_a_missing_separator_is_created_with_its_groups_sort_title(session):
    section = FakeSection()
    target = spec("charts")
    actions = await run(session, section, target)

    assert actions == ["created 'Chart Collections'"]
    assert section.created == ["Chart Collections"]
    made = section._collections["Chart Collections"]
    assert made.sort_title_set == "!010_!Chart Collections"
    assert made.labels_added == [LABEL]

    row = (await session.execute(select(ManagedCollection))).scalars().one()
    assert (row.title, row.kind) == ("Chart Collections", "separator")
    assert row.definition_hash == separator_hash(
        target.title, target.summary, target.sort_title
    )


async def test_a_current_separator_writes_nothing(session):
    target = spec("charts")
    made = FakeCollection("Chart Collections", labels=[LABEL],
                          sort_title=target.sort_title, summary=target.summary)
    row = ManagedCollection(
        library="Movies", title="Chart Collections", kind="separator",
        plex_rating_key="1",
        definition_hash=separator_hash(target.title, target.summary, target.sort_title),
    )
    session.add(row)
    await session.flush()

    actions = await run(session, FakeSection([made]), target,
                        existing={"Chart Collections": made},
                        stored={"Chart Collections": row})
    assert actions == []
    assert made.sort_title_set is None


async def test_each_group_gets_its_own_separator(session):
    for group, title, sort in [
        ("charts", "Chart Collections", "!010_!Chart Collections"),
        ("awards", "Award Collections", "!020_!Award Collections"),
        ("content_ratings", "Ratings Collections", "!030_!Ratings Collections"),
    ]:
        section = FakeSection()
        actions = await run(session, section, spec(group))
        assert actions == ["created %r" % title]
        assert section._collections[title].sort_title_set == sort


async def test_a_dry_run_writes_nothing(session):
    section = FakeSection()
    actions = await run(session, section, spec("charts"), dry_run=True)
    assert actions == ["would create 'Chart Collections'"]
    assert section.created == []
    assert (await session.execute(select(ManagedCollection))).scalars().all() == []


async def test_an_unlabelled_collision_is_refused_not_claimed(session):
    theirs = FakeCollection("Chart Collections")
    actions = await run(session, FakeSection([theirs]), spec("charts"),
                        existing={"Chart Collections": theirs})
    assert actions == [
        "conflict: 'Chart Collections' exists without the 'autoposter' label; "
        "leaving it untouched"
    ]
    assert theirs.sort_title_set is None


async def test_an_operator_blank_is_never_written_over(session):
    # The ops/blank hazard: an operator made a blank collection under a title a
    # group later claims. It carries OUR ownership label (the endpoint adds it),
    # so resolve_collision approves it -- and its managed row is what says whose
    # it is. Refused and reported; nothing is written and the row keeps saying
    # "operator".
    theirs = FakeCollection("Chart Collections", labels=[LABEL])
    row = ManagedCollection(
        library="Movies", title="Chart Collections", kind="operator",
        plex_rating_key="1", definition_hash="",
    )
    session.add(row)
    await session.flush()

    actions = await run(session, FakeSection([theirs]), spec("charts"),
                        existing={"Chart Collections": theirs},
                        stored={"Chart Collections": row})
    assert actions == [
        "'Chart Collections' was created by an operator, not by any definition; "
        "the 'charts' group's separator is not written over it"
    ]
    assert theirs.sort_title_set is None
    assert row.kind == "operator"
    assert row.definition_hash == ""


async def test_a_protected_label_wins(session):
    theirs = FakeCollection("Chart Collections",
                            labels=[LABEL, "Collection managed by Maintainerr"])
    actions = await run(session, FakeSection([theirs]), spec("charts"),
                        existing={"Chart Collections": theirs},
                        protect_labels=["Collection managed by Maintainerr"])
    assert actions == [
        "protected: 'Chart Collections' carries 'Collection managed by "
        "Maintainerr'; leaving it untouched"
    ]
    assert theirs.sort_title_set is None


async def test_a_foreign_looking_sort_title_is_corrected_not_read_as_ownership(session):
    """LAW Addendum 1 item 3: a non-empty ``titleSort`` is never evidence that
    another tool manages a collection.

    Plex writes an article-stripped ``titleSort`` onto most collections by
    itself, and an adopted Kometa separator carries Kometa's own section number
    -- so a reconciler that read either as "somebody else's" would refuse to
    correct the two states this row exists to correct. Ownership is the label
    plus the managed row, and nothing here branches on the sort title's content.
    """
    theirs = FakeCollection("Chart Collections", labels=[LABEL],
                            sort_title="!110_!Somebody Else's Heading")
    actions = await run(session, FakeSection([theirs]), spec("charts"),
                        existing={"Chart Collections": theirs})
    assert actions == ["updated 'Chart Collections'"]
    assert theirs.sort_title_set == "!010_!Chart Collections"


async def test_a_group_with_no_measured_artwork_gets_no_poster(session, config_factory,
                                                               tmp_path):
    """Seven of the ten groups have no ``Default-Images`` separator stem, so
    their spec carries ``poster_key=None`` and the poster step is not reached at
    all -- no request, and no guessed URL that would 404 and leave the
    collection quietly bare."""
    config = config_factory(assets_root=str(tmp_path))
    target = spec(groups.OPERATOR_GROUP)
    assert target.poster_key is None

    async def handler(request):
        raise AssertionError("a group with no measured stem must not be fetched for")

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        actions = await run(session, FakeSection(), target, http=http, config=config)

    assert actions == ["created 'Collections'"]
    row = (await session.execute(select(ManagedCollection))).scalars().one()
    assert row.poster_sha256 is None


async def test_a_key_with_no_hosted_path_leaves_the_poster_alone(session, config_factory,
                                                                 tmp_path):
    """The rule three docstrings assert and nothing pinned until row 49
    (LAW Addendum 2 item 5): a key ``hosted_poster_url`` cannot build a path for
    answers ``None``, and ``apply_poster`` reads ``None`` as "leave the poster
    alone" -- reported, never fetched, never uploaded, and the row's
    ``poster_sha256`` is left NULL so a later pass can still try."""
    assert hosted_poster_url("award_static", "no_such_ceremony:winner") is None

    config = config_factory(assets_root=str(tmp_path))
    row = ManagedCollection(
        library="Movies", title="Chart Collections", kind="separator",
        plex_rating_key="1", definition_hash="",
    )
    session.add(row)
    await session.flush()
    collection = FakeCollection("Chart Collections", labels=[LABEL])

    async def handler(request):
        raise AssertionError("must not fetch without a resolvable URL")

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        message = await apply_poster(
            session, http, config, collection, row, "Movies",
            "award_static", "no_such_ceremony:winner", dry_run=False,
        )

    assert message == "no poster source for 'Chart Collections'"
    assert collection.uploaded == []
    assert row.poster_sha256 is None


async def test_the_engine_hands_a_separator_its_poster(session, config_factory, tmp_path):
    """The wiring ``golden_port.json`` records, pinned directly.

    ``engine._separators`` has to thread the pass's HTTP client and config
    through to the reconciler; without them ``posters_enabled`` reads False and
    every divider silently loses the poster the Common Sense one has shipped
    with, which would make ``SeparatorSpec.poster_key`` dead in production.
    """
    from autoposter.collections.engine import _separators
    from autoposter.config.schema import CollectionDefinition

    config = config_factory(assets_root=str(tmp_path))
    config.collections.apply_to_plex = True
    buffer = io.BytesIO()
    Image.new("RGB", (4, 4), "red").save(buffer, format="JPEG")
    data = buffer.getvalue()
    seen = []

    async def handler(request):
        seen.append(str(request.url))
        return httpx.Response(200, content=data)

    section = FakeSection()
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        results = await _separators(
            session, section, "Movies", "Movie",
            [CollectionDefinition(title="Common Sense age ratings",
                                  builder="cs_bucket")],
            config, label=LABEL, dry_run=False, listing=dict, http=http,
        )

    assert [result.title for result in results] == ["Ratings Collections"]
    assert seen == [hosted_poster_url("separator", "content_rating")]
    assert section._collections["Ratings Collections"].uploaded == [data]


def test_a_group_that_goes_quiet_leaves_an_ordinary_sweep_candidate():
    # By design, and the whole of it: nothing in the separator path deletes,
    # so a group with no collections simply stops appearing in
    # ``separator_titles`` -- which is what makes its divider an orphan like any
    # other, judged only by ``engine._sweep``'s guards.
    from types import SimpleNamespace

    from autoposter.config.schema import CollectionDefinition, CollectionsConfig

    config = SimpleNamespace(collections=CollectionsConfig())
    charts = [CollectionDefinition(title="IMDb Top 250", builder="imdb_chart",
                                   params={"chart": "top_movies"})]
    assert groups.separator_titles(charts, "Movie", config) == {"Chart Collections"}
    assert groups.separator_titles([], "Movie", config) == set()

    off = SimpleNamespace(collections=CollectionsConfig(separators=False))
    assert groups.separator_titles(charts, "Movie", off) == set()
