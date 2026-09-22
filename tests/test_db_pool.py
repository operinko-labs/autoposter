"""Perf spec D3: the application's pool follows ``workers``.

No database: ``create_async_engine`` is lazy and these tests only read the
pool it configured, so the URL names a host that cannot resolve.
"""
from autoposter.db.base import (
    DEFAULT_POOL_SIZE,
    POOL_HEADROOM,
    POOL_MAX_OVERFLOW,
    POOL_RECYCLE_SECONDS,
    make_engine,
    pool_size_for,
)

URL = "postgresql+asyncpg://user:pass@db.invalid:5432/nothing"


def test_the_pool_is_workers_plus_ten_over_ten():
    # The spec's numbers: at the new default of 10 workers, 20 + 10.
    assert POOL_HEADROOM == 10
    assert POOL_MAX_OVERFLOW == 10
    assert POOL_RECYCLE_SECONDS == 1800
    assert pool_size_for(10) == 20
    assert pool_size_for(5) == 15


async def test_make_engine_applies_a_workers_derived_size():
    engine = make_engine(URL, pool_size=pool_size_for(10))
    try:
        pool = engine.pool
        assert pool.size() == pool_size_for(10)
        assert pool._max_overflow == POOL_MAX_OVERFLOW
        assert pool._recycle == POOL_RECYCLE_SECONDS
        assert pool._pre_ping is True
    finally:
        await engine.dispose()


async def test_an_engine_built_without_config_keeps_the_default_size():
    """The setup wizard's probe (``database_answers``), boot's two store reads
    and the one-shot CLIs build engines before or without a config, so they
    have no ``workers`` to size from and keep SQLAlchemy's own default."""
    engine = make_engine(URL)
    try:
        assert engine.pool.size() == DEFAULT_POOL_SIZE == 5
        assert engine.pool._max_overflow == POOL_MAX_OVERFLOW
        assert engine.pool._pre_ping is True
    finally:
        await engine.dispose()
