"""Roadmap row 236's counting query: what a pass made actionable, by flag code.

Against a real database rather than a mock session, for the reason
``tests/test_run_history.py`` states: every line under test is SQL -- fourteen
conditional sums, a join and a half-open window predicate -- and a mocked
session would verify the Python around SQL that was never executed.

``_seed`` is imported from ``test_action_flags`` (tests/test_action_flags.py:29)
rather than restated: it builds exactly the ``MediaItem`` + ``Render`` pair
these predicates are written against, with a fresh rating key per call because
the column is unique and these tests share one database. A bare sibling import,
never ``from tests.x``: the CI runner collects these files with a bare pytest
where the ``tests`` package is not importable.

Every window here is built from a stamp the database itself wrote and then read
back, never from two clock readings compared across transactions -- the dev
host's container clock steps backwards, so ordering two ``now()`` calls is not a
fact this suite may rest on.
"""
from datetime import timedelta
from pathlib import Path

import pytest
from sqlalchemy import func, select

from autoposter.actions import flags
from autoposter.actions.digest import actionable_window_counts
from autoposter.config.loader import load_config
from autoposter.db.models import Render
from test_action_flags import _seed

EXAMPLE = Path(__file__).parent.parent / "config" / "autoposter.example.yaml"


@pytest.fixture
def config():
    return load_config(EXAMPLE)


async def _scored(session, **render_fields) -> Render:
    """One render row stamped ``quality_scored_at`` by the database clock."""
    return await _seed(session, quality_scored_at=func.now(), **render_fields)


async def _stamp(session, render: Render):
    """The instant the database wrote onto that row, read back."""
    render_id = render.id
    session.expire_all()
    return (
        await session.execute(
            select(Render.quality_scored_at).where(Render.id == render_id)
        )
    ).scalar_one()


def _around(stamp):
    """A window that contains ``stamp``: half-open, so start is inclusive."""
    return stamp, stamp + timedelta(seconds=1)


async def test_a_row_scored_inside_the_window_is_counted_under_its_flag_code(
    session, config
):
    render = await _scored(session, source_mode="plex_generated")
    started_at, finished_at = _around(await _stamp(session, render))

    total, counts = await actionable_window_counts(
        session, config, started_at, finished_at
    )

    assert counts["plex_generated"] == 1
    assert total == 1


async def test_a_row_scored_outside_the_window_is_not_counted(session, config):
    """Window attribution, both edges. A pass reports what IT scored -- a row
    scored before it opened belongs to an earlier digest, and the upper bound
    is exclusive so a row scored at ``finished_at`` belongs to the next one."""
    render = await _scored(session, source_mode="plex_generated")
    stamp = await _stamp(session, render)

    before = await actionable_window_counts(
        session, config, stamp - timedelta(hours=2), stamp - timedelta(hours=1)
    )
    after = await actionable_window_counts(
        session, config, stamp + timedelta(hours=1), stamp + timedelta(hours=2)
    )
    on_the_upper_edge = await actionable_window_counts(
        session, config, stamp - timedelta(seconds=1), stamp
    )

    assert before == (0, {code: 0 for code in flags.FLAGS})
    assert after == (0, {code: 0 for code in flags.FLAGS})
    assert on_the_upper_edge == (0, {code: 0 for code in flags.FLAGS})


async def test_an_empty_window_reads_as_zero_for_every_registry_code(session, config):
    """SUM over no rows is NULL, not 0. An empty window must read as fourteen
    zeroes, never as fourteen Nones a consumer would render as blanks."""
    render = await _scored(session, source_mode="plex_generated")
    stamp = await _stamp(session, render)

    total, counts = await actionable_window_counts(
        session, config, stamp + timedelta(hours=1), stamp + timedelta(hours=2)
    )

    assert total == 0
    assert counts == {code: 0 for code in flags.FLAGS}
    assert all(isinstance(value, int) for value in counts.values())


async def test_the_counts_are_keyed_by_registry_code_and_carry_nothing_else(
    session, config
):
    """Row 213 over the whole mapping, asserted as an equality rather than as a
    set of ``in`` checks: a key that is not a registry flag code -- an
    asset_path, a title, a stray total -- fails this test by existing.

    ``unscored`` is 0 by construction and that is deliberate: its predicate is
    ``quality_scored_at IS NULL`` and this window requires the opposite, so the
    code is present, always zero, and honest about it.
    """
    render = await _scored(session, source_mode="plex_generated")
    started_at, finished_at = _around(await _stamp(session, render))

    _, counts = await actionable_window_counts(
        session, config, started_at, finished_at
    )

    expected = {code: 0 for code in flags.FLAGS}
    expected["plex_generated"] = 1
    assert counts == expected
    assert list(counts) == list(flags.FLAGS), "registry order, so the chips read the same way"


async def test_one_row_tripping_two_flags_counts_once_in_the_total(session, config):
    """The total is the DEFAULT POPULATION, not the sum of the flags. Summing
    would count this row twice and tell an operator a pass produced two
    problems where it produced one."""
    render = await _scored(
        session, source_mode="plex_generated", textless_fallback=True
    )
    started_at, finished_at = _around(await _stamp(session, render))

    total, counts = await actionable_window_counts(
        session, config, started_at, finished_at
    )

    assert counts["plex_generated"] == 1
    assert counts["textless_miss"] == 1
    assert total == 1


async def test_a_non_default_flag_is_counted_but_does_not_raise_the_total(
    session, config
):
    """``skipped`` is ``default_on=False`` as deliberate flood-avoidance (row
    103): a disabled art kind would otherwise put every row of that kind in the
    queue. The digest reports its count, and the suppression gate -- which reads
    the total -- does not fire on it alone."""
    render = await _scored(session, status="skipped")
    started_at, finished_at = _around(await _stamp(session, render))

    total, counts = await actionable_window_counts(
        session, config, started_at, finished_at
    )

    assert counts["skipped"] == 1
    assert total == 0


async def test_two_rows_that_trip_the_same_flag_inside_the_window_count_as_two(
    session, config
):
    """Review finding I1: every other test in this file seeds exactly one row,
    so `func.sum` is never discriminated from `func.max`/`bool_or` -- all of
    them still pass under that mutation. Counting N is the entire purpose of
    this function, and this is the one test that seeds two rows tripping the
    same flag inside one window and asserts the count is 2, not 1."""
    first = await _scored(session, source_mode="plex_generated")
    second = await _scored(session, source_mode="plex_generated")
    first_id, second_id = first.id, second.id
    session.expire_all()
    stamps = (
        await session.execute(
            select(Render.id, Render.quality_scored_at).where(
                Render.id.in_([first_id, second_id])
            )
        )
    ).all()
    by_id = {row.id: row.quality_scored_at for row in stamps}
    started_at = min(by_id.values())
    finished_at = max(by_id.values()) + timedelta(seconds=1)

    total, counts = await actionable_window_counts(
        session, config, started_at, finished_at
    )

    assert counts["plex_generated"] == 2
    assert total == 2


async def test_a_row_in_an_excluded_library_is_not_counted(session, config):
    """``excluded_library_predicate`` leads the conditions for the reason
    ``api/action_center.py::_scope`` leads with it: nothing can re-render a row
    in a library this service never touches, so announcing it would be work an
    operator cannot do."""
    render = await _scored(
        session, library="Home Videos", source_mode="plex_generated"
    )
    started_at, finished_at = _around(await _stamp(session, render))
    config.plex.excluded_libraries = ["Home Videos"]

    total, counts = await actionable_window_counts(
        session, config, started_at, finished_at
    )

    assert counts["plex_generated"] == 0
    assert total == 0
