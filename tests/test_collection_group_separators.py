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
from types import SimpleNamespace

import httpx
from PIL import Image
from sqlalchemy import select

from autoposter.collections import groups
from autoposter.collections.posters import (
    DEFAULT_IMAGES_BASE,
    apply_poster,
    hosted_poster_url,
)
from autoposter.collections.reconcile import reconcile_separator, separator_hash
from autoposter.db.models import ManagedCollection

LABEL = "autoposter"

SHIPPED_TITLE = "Ratings Collections"
SHIPPED_SUMMARY = "Section separator for Ratings Collections."
SHIPPED_SORT_TITLE = "!110_!Ratings Collections"
# The constant this module shipped, as a literal. What it hashes DID change
# this phase -- the section number moved from "!110_" to "!030_" -- so every
# live server re-writes this divider once regardless, and no ``group_order``
# can put the group back at position 11. What the pin proves is that the
# payload and the format are otherwise untouched: the hash the migration pass
# stores short-circuits every pass after it, and nothing else about hashing
# moved silently.
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
    """``queries`` holds WRITES. A write always names a ``method``; the only
    read through here is ``smart.count_matches``'s container-size-0 count
    (roadmap row 198), which names none -- answered from the section's own
    ``fetchItems`` so the count and the fetch cannot disagree."""

    def __init__(self, section=None):
        self.queries = []
        self.reads = []
        self._section = section
        self._session = type("S", (), {"post": "POST", "put": "PUT"})()

    def _uriRoot(self):
        return "server://FAKE/com.plexapp.plugins.library"

    def query(self, path, method=None, headers=None, **kwargs):
        if method is None:
            self.reads.append((path, headers))
            return SimpleNamespace(
                attrib={"totalSize": str(len(self._section.fetchItems(path)))}
            )
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
        self.deleted = False

    def reload(self):
        self.labels = list(self._real_labels)

    def delete(self):
        self.deleted = True

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

    def sortUpdate(self, sort=None):
        # A no-op the separator path never reaches. It is here because
        # ``tests/test_collection_groups.py`` drives ``reconcile_list_collection``
        # through this same double for the end-to-end sort-title assertions, and
        # a third fake section in this phase's own files would be committing
        # roadmap row 191's forked-double defect fresh rather than inheriting it.
        self.sorted_by = sort

    def uploadPoster(self, filepath):
        with open(filepath, "rb") as handle:
            self.uploaded.append(handle.read())

    def lockPoster(self):
        pass


class FakeSection:
    def __init__(self, collections=()):
        self.key = 42
        self._server = FakeServer(self)
        self._collections = {c.title: c for c in collections}
        self.created = []

    def collections(self):
        return list(self._collections.values())

    def collection(self, title):
        made = FakeCollection(title)
        self._collections[title] = made
        self.created.append(title)
        return made

    def createCollection(self, title, items=None, smart=False):
        # plexapi's own create route, which the separator never uses (an empty
        # collection has to be a raw POST) and which the list reconciler does --
        # see ``sortUpdate`` above for why this double answers both.
        return self.collection(title)

    def fetchItems(self, path, **kw):
        # ``smart.count_matches``'s probe. The separator never reaches it; the
        # SMART reconcile in ``tests/test_collection_groups.py`` does, and it
        # refuses at zero -- so ``items`` has to be non-empty there. Same
        # reason ``sortUpdate`` above lives here rather than in a fourth fake.
        return list(getattr(self, "items", []))


def spec(group="charts", order=groups.CANONICAL_ORDER, config=None):
    cfg = config or SimpleNamespace(
        collections=SimpleNamespace(separator_style="orig")
    )
    return groups.SeparatorSpec(
        group=group,
        title=groups.separator_title(group),
        summary=groups.separator_summary(group),
        sort_title=groups.separator_sort_title(group, order),
        poster_key=groups.separator_poster_key(group, cfg),
    )


def wanted_hash(target):
    """The digest ``reconcile_separator`` computes for this spec.

    Spelled once here because every spec carries a poster key since the hybrid,
    so the fourth component is never absent in these tests -- and a test that
    recomputed it three-argument would be asserting the pre-C4 payload while
    reading like the current one.
    """
    return separator_hash(
        target.title, target.summary, target.sort_title, target.poster_key or ""
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
    assert row.definition_hash == wanted_hash(target)


async def test_a_current_separator_writes_nothing(session):
    target = spec("charts")
    made = FakeCollection("Chart Collections", labels=[LABEL],
                          sort_title=target.sort_title, summary=target.summary)
    row = ManagedCollection(
        library="Movies", title="Chart Collections", kind="separator",
        plex_rating_key="1",
        definition_hash=wanted_hash(target),
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


async def test_a_row_a_definition_already_owns_is_never_written_over(session):
    # The same hazard one kind over, and the one the operator group's own
    # divider title reaches: a DEFINITION titled "Collections" builds the
    # collection this group's separator is named for. Config load refuses that
    # document, so this is the second belt -- and it is not blank-shaped: the
    # row says "manual", written by ``lists.py``, carrying the members hash.
    # Unrefused, each writer would overwrite what the other stored -- summary,
    # sort title, hash -- every pass, forever.
    theirs = FakeCollection("Collections", labels=[LABEL])
    row = ManagedCollection(
        library="Movies", title="Collections", kind="manual",
        plex_rating_key="1", definition_hash="the-members-hash",
    )
    session.add(row)
    await session.flush()

    actions = await run(session, FakeSection([theirs]), spec("operator"),
                        existing={"Collections": theirs},
                        stored={"Collections": row})
    assert actions == [
        "'Collections' is already managed as a 'manual' collection, not as a "
        "separator; the 'operator' group's separator is not written over it"
    ]
    assert theirs.sort_title_set is None
    assert (row.kind, row.definition_hash) == ("manual", "the-members-hash")


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


async def test_generation_unavailable_reports_no_poster_source(session, config_factory,
                                                               tmp_path):
    """The graceful half of the hybrid. A group with no upstream stem carries a
    GENERATED key now (``orig:@operator``) rather than ``None``; when the art
    cannot be produced -- here because nothing answers for the ``@base`` layer
    -- ``hosted_poster_url`` refuses the '@' stem, the divider reports "no
    poster source", and ``poster_sha256`` stays NULL so a later pass retries.
    The same posture as a 404ing hosted default, and what the golden harness
    records for the fence."""
    config = config_factory(assets_root=str(tmp_path))
    config.collections.apply_to_plex = True
    target = spec(groups.OPERATOR_GROUP)
    assert target.poster_key == "orig:@operator"

    async def handler(request):
        assert "separators/@base/" in str(request.url)
        return httpx.Response(404, text="not found")

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        actions = await run(session, FakeSection(), target, http=http, config=config)

    assert actions == [
        "created 'Collections'",
        "no poster source for 'Collections'",
    ]
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

    # The fence (C5) reconciles alongside the group's divider and has no
    # upstream art, so it asks for the generated layer rather than a hosted
    # separator. Both requests are pinned, in order and exhaustively: pinning
    # the LIST rather than element zero pins the request count too, so a
    # spurious extra fetch cannot pass unseen.
    assert [result.title for result in results] == [
        "Ratings Collections", "Other Collections",
    ]
    assert seen == [
        hosted_poster_url("separator", "orig:content_rating"),
        f"{DEFAULT_IMAGES_BASE}/separators/@base/orig.png",
    ]
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
    # The fence rides along with whatever is active -- and goes quiet with it.
    assert groups.separator_titles(charts, "Movie", config) == {
        "Chart Collections", "Other Collections",
    }
    assert groups.separator_titles([], "Movie", config) == set()

    off = SimpleNamespace(collections=CollectionsConfig(separators=False))
    assert groups.separator_titles(charts, "Movie", off) == set()


async def test_a_switched_off_groups_separator_sweeps_like_any_other_orphan(session):
    """The clause the test above only asserts one link of: a switched-off
    group's separator is not merely absent from ``separator_titles`` -- it is
    an ORDINARY delete-sweep candidate, driven through ``engine._sweep``'s
    full guard chain, the same as any other orphan carrying our label and a
    managed row. Pinned directly, the armed-sweep pattern
    ``tests/test_builder_knobs.py`` already uses for every other guard.

    And the other half of the lifecycle: with the group back on, the same
    title is in this pass's managed set, so the row is never a sweep
    candidate at all -- protected simply by being wanted, before any of the
    label or ``delete_unconfigured`` guards are even reached.
    """
    from types import SimpleNamespace

    from autoposter.collections.engine import _sweep
    from autoposter.config.schema import CollectionDefinition, CollectionsConfig

    chart_definitions = [CollectionDefinition(
        title="IMDb Top 250", builder="imdb_chart", params={"chart": "top_movies"},
    )]
    armed = SimpleNamespace(collections=CollectionsConfig(delete_unconfigured=True))

    # The group is off (no definition builds a chart): the row is an
    # ordinary orphan, and armed ``delete_unconfigured`` deletes it.
    orphan = FakeCollection("Chart Collections", labels=[LABEL])
    row = ManagedCollection(
        library="Movies", title="Chart Collections", kind="separator",
        plex_rating_key="1", definition_hash="",
    )
    session.add(row)
    await session.flush()

    results = await _sweep(
        session, FakeSection([orphan]), "Movies", "Movie", [], armed,
        label=LABEL, dry_run=False,
        listing=lambda: {"Chart Collections": orphan}, run_cache={},
    )

    assert orphan.deleted is True
    assert any(
        "deleted 'Chart Collections'" in action
        for result in results for action in result.actions
    )
    assert (
        await session.execute(
            select(ManagedCollection).where(ManagedCollection.title == "Chart Collections")
        )
    ).scalar_one_or_none() is None

    # The group is back on: the same title is in this pass's managed set
    # (``groups.separator_titles`` folded into ``definition_titles_for``), so
    # a freshly-recreated row for it is protected before any candidacy check
    # runs at all -- not deleted, and not even reported on.
    kept = FakeCollection("Chart Collections", labels=[LABEL])
    row = ManagedCollection(
        library="Movies", title="Chart Collections", kind="separator",
        plex_rating_key="2", definition_hash="",
    )
    session.add(row)
    await session.flush()

    results = await _sweep(
        session, FakeSection([kept]), "Movies", "Movie", chart_definitions, armed,
        label=LABEL, dry_run=False,
        listing=lambda: {"Chart Collections": kept}, run_cache={},
    )

    assert kept.deleted is False
    assert not any(
        "Chart Collections" in action
        for result in results for action in result.actions
    )


def test_hosted_separator_urls_carry_the_style_and_refuse_generated_stems():
    assert hosted_poster_url("separator", "orig:chart") == (
        "https://raw.githubusercontent.com/Kometa-Team/Default-Images/master"
        "/separators/orig/chart.jpg"
    )
    assert hosted_poster_url("separator", "sand:chart") == (
        "https://raw.githubusercontent.com/Kometa-Team/Default-Images/master"
        "/separators/sand/chart.jpg"
    )
    # A generated stem has no hosted path -- None, never a guessed URL.
    assert hosted_poster_url("separator", "orig:@content") is None
    # An unknown style is refused the same way (the config validator is the
    # loud refusal; this is the quiet belt behind it).
    assert hosted_poster_url("separator", "taupe:chart") is None
    # The pre-style bare key no longer resolves: nothing composes it any more,
    # and a silent orig-fallback would hide a composition bug.
    assert hosted_poster_url("separator", "chart") is None


def test_the_poster_key_folds_into_the_separator_hash():
    base = separator_hash(SHIPPED_TITLE, SHIPPED_SUMMARY, SHIPPED_SORT_TITLE)
    # No key -> the shipped digest, byte for byte: the pin survives.
    assert base == SHIPPED_HASH
    keyed = separator_hash(
        SHIPPED_TITLE, SHIPPED_SUMMARY, SHIPPED_SORT_TITLE, "orig:content_rating"
    )
    assert keyed != base
    # Style change -> different hash -> the pass has work; same style -> same
    # hash -> it settles. This is the whole C4 mechanism.
    sand = separator_hash(
        SHIPPED_TITLE, SHIPPED_SUMMARY, SHIPPED_SORT_TITLE, "sand:content_rating"
    )
    assert sand not in (base, keyed)
    assert keyed == separator_hash(
        SHIPPED_TITLE, SHIPPED_SUMMARY, SHIPPED_SORT_TITLE, "orig:content_rating"
    )
    # An empty key is the no-key case, not a fourth component of "": that is
    # what keeps SHIPPED_HASH reproducible while every LIVE divider, whose spec
    # always carries a key, re-hashes exactly once.
    assert separator_hash(SHIPPED_TITLE, SHIPPED_SUMMARY, SHIPPED_SORT_TITLE, "") == base


async def test_a_style_change_rewrites_once_and_settles(session, config_factory,
                                                        tmp_path):
    """C4's whole point, as behaviour. Pass 1 under orig posters the chart
    divider; the style flips to sand; pass 2 has work (the key is in the
    hash), rewrites, fetches the sand art, uploads once; pass 3 writes
    nothing. A HOSTED group so no magick is involved -- the generated half
    settles through the identical hash/sha mechanics."""
    config = config_factory(assets_root=str(tmp_path))
    config.collections.apply_to_plex = True

    def image_bytes(color):
        buffer = io.BytesIO()
        Image.new("RGB", (4, 4), color).save(buffer, format="JPEG")
        return buffer.getvalue()

    bodies = {"orig": image_bytes("red"), "sand": image_bytes("yellow")}

    async def handler(request):
        url = str(request.url)
        style = "sand" if "/separators/sand/" in url else "orig"
        return httpx.Response(200, content=bodies[style])

    section = FakeSection()
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        first = await run(session, section, spec("charts"), http=http, config=config)
        made = section._collections["Chart Collections"]
        existing = {"Chart Collections": made}
        stored = {
            row.title: row
            for row in (await session.execute(select(ManagedCollection))).scalars()
        }

        config.collections.separator_style = "sand"
        sand_spec = spec("charts", config=SimpleNamespace(collections=config.collections))
        assert sand_spec.poster_key == "sand:chart"

        second = await run(session, section, sand_spec, existing=existing,
                           stored=stored, http=http, config=config)
        third = await run(session, section, sand_spec, existing=existing,
                          stored=stored, http=http, config=config)

    assert first[0] == "created 'Chart Collections'"
    assert second[0] == "updated 'Chart Collections'"
    assert any("set the poster" in action for action in second)
    assert made.uploaded[-1] == bodies["sand"]
    assert third == []


async def test_a_generated_groups_divider_uploads_the_cached_art(session, config_factory,
                                                                 tmp_path):
    """The hybrid's generated half, wired end to end with no magick: the
    cache file IS the render (separator_art returns it untouched), apply_poster
    hashes and uploads it, and nothing fetches -- not the @base layer (cache
    hit) and not any hosted URL (an '@' stem has none)."""
    config = config_factory(assets_root=str(tmp_path))
    config.collections.apply_to_plex = True
    cached = tmp_path / ".generated" / "separators" / "orig" / "operator.jpg"
    cached.parent.mkdir(parents=True)
    buffer = io.BytesIO()
    Image.new("RGB", (20, 30), "olive").save(buffer, format="JPEG")
    cached.write_bytes(buffer.getvalue())

    async def handler(request):
        raise AssertionError("nothing may fetch: the render is cached and an "
                             "'@' stem has no hosted URL")

    target = spec(groups.OPERATOR_GROUP)
    assert target.poster_key == "orig:@operator"
    section = FakeSection()
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        actions = await run(session, section, target, http=http, config=config)

    assert actions[0] == "created 'Collections'"
    assert any(action.startswith("set the poster for 'Collections'")
               for action in actions)
    assert section._collections["Collections"].uploaded == [cached.read_bytes()]
    made_row = (await session.execute(select(ManagedCollection))).scalars().one()
    assert made_row.poster_sha256 is not None


async def test_an_operator_override_still_outranks_generated_art(session,
                                                                 config_factory,
                                                                 tmp_path):
    """Generated art is a SOURCE, not an override. ``prioritize_assets``'
    guarantee is unchanged: a file the operator placed under assets_root wins
    over it, the same way it wins over a hosted default."""
    config = config_factory(assets_root=str(tmp_path))
    config.collections.apply_to_plex = True

    def jpeg(color):
        buffer = io.BytesIO()
        Image.new("RGB", (20, 30), color).save(buffer, format="JPEG")
        return buffer.getvalue()

    cached = tmp_path / ".generated" / "separators" / "orig" / "operator.jpg"
    cached.parent.mkdir(parents=True)
    cached.write_bytes(jpeg("olive"))
    override = tmp_path / "Movies" / "Collections" / "poster.jpg"
    override.parent.mkdir(parents=True)
    override.write_bytes(jpeg("red"))

    async def handler(request):
        raise AssertionError("a local override needs no request")

    section = FakeSection()
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        await run(session, section, spec(groups.OPERATOR_GROUP), http=http,
                  config=config)

    assert section._collections["Collections"].uploaded == [override.read_bytes()]
