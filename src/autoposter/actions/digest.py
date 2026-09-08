"""Roadmap row 236: how many renders a pass made actionable, by flag code.

The Action Center is a pull surface -- an operator sees a flagged asset when
they open the page, and nothing tells them a pass has just produced fifty of
them. This module answers that one question: over a half-open window
``[started_at, finished_at)``, how many rows does each registry flag fire on,
counting only rows whose ``quality_scored_at`` fell inside the window.

**"Newly" is window attribution -- not a stamp, and not a stored high-water
mark.** ``renders.quality_scored_at`` is written at the pipeline write-back
(``render/pipeline.py``), BELOW the "unchanged" fingerprint short-circuit, so a
row scored inside a pass's window is a row that pass really did re-score. That
makes re-render-driven newness derivable with no new column and no migration.

Config-edit-driven newness is deliberately NOT covered, and could not be:
re-pointing ``artwork.poster.language_order`` moves hundreds of rows into the
queue with zero row writes -- the property
``tests/test_action_flags.py::test_editing_the_language_order_flips_the_flag_with_no_row_write``
exists to pin -- so no timestamp anywhere could see it. The config editor's
impact preview (``config/impact.py``) is the surface that answers the operator
who made that edit, and they are at the keyboard when they make it.

This lives here rather than in ``scheduler/run_history.py`` beside
``window_counts``, whose two grouped queries have the same shape, because a
flag predicate needs a ``Config`` and that module is deliberately config-free:
it is the only writer of ``runs``, its callers hand it a session and nothing
else, and giving it a ``Config`` would add a ``scheduler/`` -> ``actions/``
import edge as well. ``actions/`` is where the queue's SQL over ``renders``
already lives, and this is that SQL over one more predicate.

Nothing here writes anything, exactly like ``flags.py``.
"""
from datetime import datetime

from sqlalchemy import case, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from autoposter.actions import flags
from autoposter.config.schema import Config
from autoposter.db.models import MediaItem, Render


async def actionable_window_counts(
    session: AsyncSession, config: Config, started_at: datetime, finished_at: datetime
) -> tuple[int, dict[str, int]]:
    """``(actionable, per_flag)`` for the rows scored in ``[started_at, finished_at)``.

    ONE grouped round trip with a conditional sum per flag -- the shape
    ``api/action_center.py``'s ``/actions/summary`` already uses, for the same
    reason: a query per chip would be fifteen sequential scans of one table to
    build one header. The predicates are applied in SQL and never in Python
    over fetched rows: a full pass's window holds thousands of renders (the
    operator's library scored 1828 in a single pass, of 18052 rows), and
    pulling them into the process to test fourteen predicates apiece would
    trade one sequential scan for a library walk inside the scheduler's poll loop.

    ``actionable`` is the DEFAULT population's size -- the rows any
    ``default_on`` flag fires on -- and it is deliberately NOT the sum of
    ``per_flag``: one row can trip several flags at once, so summing would
    report two problems where a pass produced one. It is the number the
    digest's suppression rule reads and the number its summary sentence states.

    ``per_flag`` carries every registry code, zero-filled, in registry order --
    ``window_counts``' own rule (``scheduler/run_history.py``), for its reason:
    a consumer must never find the key it gates on missing because this pass
    happened to produce none of that kind. ``unscored`` is in it and is 0 by
    construction: its predicate is ``quality_scored_at IS NULL`` and this
    window requires the opposite.

    ``excluded_library_predicate`` leads the conditions for the reason
    ``api/action_center.py::_scope`` leads with it -- a row in a library this
    service never touches is not a narrower view of the queue, it is not part
    of the queue at all, and a digest that counted it would announce work
    nothing can do.

    Dismissals are deliberately not subtracted -- plan decision D5. If that
    changes, the way to do it is an anti-join over ``flags.evidence_expression()``
    (``flags.py:512-541``) and ``db.models.ActionDismissal``, both already
    reachable from here with no ``api/`` import. The known consequence of not
    doing it: a whole-library re-stamp -- an artwork-settings edit, which
    changes ``render_version_for`` and re-renders the library with provenance
    unchanged -- re-stamps ``quality_scored_at`` on every row inside the
    pass's window while leaving each row's live dismissal untouched, so the
    digest can announce rows the operator has already dismissed.
    """
    counters = [
        func.sum(case((entry.predicate(config), 1), else_=0)).label(f"n_{code}")
        for code, entry in flags.FLAGS.items()
    ]
    counters.append(
        func.sum(case((flags.default_predicate(config), 1), else_=0)).label("n_default")
    )

    row = (
        await session.execute(
            select(*counters)
            .select_from(Render)
            .join(MediaItem, Render.item_id == MediaItem.id)
            .where(
                flags.excluded_library_predicate(config),
                Render.quality_scored_at >= started_at,
                Render.quality_scored_at < finished_at,
            )
        )
    ).one()

    # SUM over no rows is NULL, not 0 -- /actions/summary's rule: an empty
    # window must read as zeroes, never as fifteen nulls.
    def count(name: str) -> int:
        return int(getattr(row, name) or 0)

    return count("n_default"), {code: count(f"n_{code}") for code in flags.FLAGS}
