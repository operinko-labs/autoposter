"""Guards against a migration that drifts from the models (see the item_facts
incident: an empty autogenerate ran clean because the target DB already had
the table, got stamped as head, and shipped with no DDL).

Runs against its own scratch database so it never races the shared test DB
that ``conftest.py`` drops and recreates on every test.
"""

import os
import subprocess
import sys
from pathlib import Path

import asyncpg
import pytest
from alembic.config import Config
from alembic.script import ScriptDirectory
from urllib.parse import urlsplit, urlunsplit

REPO_ROOT = Path(__file__).parent.parent


def _required_env(name: str) -> str:
    """An environment variable the suite cannot run without.

    No fallback: a hardcoded ``localhost:5433`` default here once happened to
    match one developer's own PostgreSQL, so a misconfigured environment
    connected anyway instead of saying so. The sanctioned way to run this
    suite is the container, which sets this.
    """
    value = os.environ.get(name)
    if not value:
        raise RuntimeError(
            f"{name} is not set. Run the suite in the container instead: "
            "`docker compose run --rm test pytest` (see docker-compose.yml's "
            "`test` service, which sets it)."
        )
    return value


MAINTENANCE_DB_URL = _required_env("AUTOPOSTER_MAINTENANCE_DATABASE_URL")
SCRATCH_DB_NAME = "autoposter_migrations_check"


def _scratch_url(database: str) -> str:
    """A scratch-database URL on the same server as the maintenance URL.

    Derived rather than hardcoded: hardcoding the host and port meant the
    scratch database was created on whichever server the maintenance URL names
    while alembic was pointed at localhost:5433, which is only the same server
    on a developer's machine running docker-compose. In CI it is not, and the
    two migration tests failed with nothing to say about migrations.
    """
    parsed = urlsplit(MAINTENANCE_DB_URL)
    return urlunsplit(("postgresql+asyncpg", parsed.netloc, "/" + database, "", ""))


SCRATCH_DB_URL = _scratch_url(SCRATCH_DB_NAME)


def _unreachable_postgres() -> None:
    """Always a hard failure, never a skip.

    These two are the only check that a migration matches the models, and the
    whole point of them is the item_facts incident above. A skip here once
    hid exactly the defect they exist to catch -- multiple alembic heads --
    from a developer running the full suite in the containerised dev
    environment, where PostgreSQL should always be reachable
    (docker-compose.yml's `test` service depends on it being healthy). If it
    is not, that is a real problem to see, not a reason to report green
    having verified nothing.
    """
    pytest.fail(
        f"postgres is not reachable at {MAINTENANCE_DB_URL}, so nothing "
        "verified that the migrations match the models. Run the suite in "
        "the container: `docker compose run --rm test pytest`."
    )


async def _postgres_reachable() -> bool:
    try:
        conn = await asyncpg.connect(MAINTENANCE_DB_URL, timeout=3)
    except (OSError, asyncpg.PostgresError):
        return False
    await conn.close()
    return True


def test_alembic_has_a_single_head():
    """Two migrations that both name the same ``down_revision`` fork the
    graph; ``alembic upgrade head`` then refuses to run at all, which is
    exactly what happened when PR #23 and PR #24 each branched off
    ``a6dc650926a0`` and were merged without a merge revision joining them.

    This only inspects the revision files present in *this* checkout, so it
    does not need a database and never skips. It would not have caught that
    incident before the merge; this Forgejo instance publishes only
    ``refs/pull/N/head`` (PR branch tips), not ``refs/pull/N/merge`` refs
    (merge previews). On a forge providing merge-preview refs, this test
    would fire when a re-run of PR #24's CI after PR #23 merged would have
    seen both heads at once. It does catch a fork once one lands in a
    checkout: a self-inflicted fork within a single branch, or the state of
    ``main`` itself right after a merge like the one above.
    """
    config = Config(str(REPO_ROOT / "alembic.ini"))
    script = ScriptDirectory.from_config(config)
    heads = script.get_heads()
    assert len(heads) == 1, (
        "multiple alembic heads, needs a merge revision: %r" % (heads,)
    )


async def test_alembic_head_matches_models():
    """``alembic upgrade head`` on a fresh DB must leave no drift from the models."""
    if not await _postgres_reachable():
        _unreachable_postgres()

    maint = await asyncpg.connect(MAINTENANCE_DB_URL, timeout=3)
    try:
        await maint.execute(f'DROP DATABASE IF EXISTS "{SCRATCH_DB_NAME}"')
        await maint.execute(f'CREATE DATABASE "{SCRATCH_DB_NAME}"')
    finally:
        await maint.close()

    env = dict(os.environ, AUTOPOSTER_DATABASE_URL=SCRATCH_DB_URL)
    try:
        upgrade = subprocess.run(
            [sys.executable, "-m", "alembic", "upgrade", "head"],
            cwd=REPO_ROOT,
            env=env,
            capture_output=True,
            text=True,
        )
        assert upgrade.returncode == 0, upgrade.stdout + upgrade.stderr

        check = subprocess.run(
            [sys.executable, "-m", "alembic", "check"],
            cwd=REPO_ROOT,
            env=env,
            capture_output=True,
            text=True,
        )
        assert check.returncode == 0, check.stdout + check.stderr
    finally:
        maint = await asyncpg.connect(MAINTENANCE_DB_URL, timeout=3)
        try:
            await maint.execute(f'DROP DATABASE IF EXISTS "{SCRATCH_DB_NAME}"')
        finally:
            await maint.close()


async def test_migrations_apply_to_a_populated_renders_table():
    """A deployed instance already has rows in ``renders``.

    ``ADD COLUMN ... NOT NULL`` without a ``server_default`` fails outright
    against a non-empty table, so a migration that passes on a fresh database
    can still break every existing deployment. This walks to the revision
    before the badge columns, puts a row in, and then upgrades.
    """
    if not await _postgres_reachable():
        _unreachable_postgres()

    before_badges = "716a0d6b8941"

    maint = await asyncpg.connect(MAINTENANCE_DB_URL, timeout=3)
    try:
        await maint.execute(f'DROP DATABASE IF EXISTS "{SCRATCH_DB_NAME}_data"')
        await maint.execute(f'CREATE DATABASE "{SCRATCH_DB_NAME}_data"')
    finally:
        await maint.close()

    url = SCRATCH_DB_URL.replace(SCRATCH_DB_NAME, SCRATCH_DB_NAME + "_data")
    env = dict(os.environ, AUTOPOSTER_DATABASE_URL=url)

    def alembic(*args):
        return subprocess.run(
            [sys.executable, "-m", "alembic", *args],
            cwd=REPO_ROOT, env=env, capture_output=True, text=True,
        )

    try:
        step = alembic("upgrade", before_badges)
        assert step.returncode == 0, step.stdout + step.stderr

        conn = await asyncpg.connect(url.replace("postgresql+asyncpg", "postgresql"), timeout=5)
        try:
            item_id = await conn.fetchval(
                "INSERT INTO media_items (kind, rating_key, title, library) "
                "VALUES ('movie', '999', 'Populated', 'Movies') RETURNING id"
            )
            await conn.execute(
                "INSERT INTO renders (item_id, art_kind, asset_path, source_mode, status) "
                "VALUES ($1, 'poster', '/x.jpg', 'generate', 'rendered')",
                item_id,
            )
        finally:
            await conn.close()

        head = alembic("upgrade", "head")
        assert head.returncode == 0, (
            "migrating a populated renders table failed:\n" + head.stdout + head.stderr
        )

        conn = await asyncpg.connect(url.replace("postgresql+asyncpg", "postgresql"), timeout=5)
        try:
            status = await conn.fetchval("SELECT upload_status FROM renders LIMIT 1")
        finally:
            await conn.close()
        assert status == "pending", "existing rows must be backfilled, got %r" % status
    finally:
        maint = await asyncpg.connect(MAINTENANCE_DB_URL, timeout=3)
        try:
            await maint.execute(f'DROP DATABASE IF EXISTS "{SCRATCH_DB_NAME}_data"')
        finally:
            await maint.close()


async def test_migration_collapses_duplicate_pending_and_deferred_dedupe_rows():
    """The production incident this migration exists to fix: uq_jobs_pending_dedupe
    used to cover only ``state = 'pending'``, so every webhook/sweep event for an
    item stuck waiting on Plex minted another independent ``deferred`` row --
    11 piled up for one show. Seeded here by raw INSERT (bypassing enqueue(),
    which the fixed code no longer lets create duplicates) to reproduce exactly
    the shape a deployed database carries at migration time: 3 duplicate rows
    for one dedupe_key plus one unrelated pending row that must survive
    untouched. Walks to the revision before the widened index, seeds, then
    upgrades to head and checks exactly one survivor remains.
    """
    if not await _postgres_reachable():
        _unreachable_postgres()

    before_widen = "a9d4e70c3b15"

    maint = await asyncpg.connect(MAINTENANCE_DB_URL, timeout=3)
    try:
        await maint.execute(f'DROP DATABASE IF EXISTS "{SCRATCH_DB_NAME}_dedupe"')
        await maint.execute(f'CREATE DATABASE "{SCRATCH_DB_NAME}_dedupe"')
    finally:
        await maint.close()

    url = SCRATCH_DB_URL.replace(SCRATCH_DB_NAME, SCRATCH_DB_NAME + "_dedupe")
    env = dict(os.environ, AUTOPOSTER_DATABASE_URL=url)

    def alembic(*args):
        return subprocess.run(
            [sys.executable, "-m", "alembic", *args],
            cwd=REPO_ROOT, env=env, capture_output=True, text=True,
        )

    try:
        step = alembic("upgrade", before_widen)
        assert step.returncode == 0, step.stdout + step.stderr

        conn = await asyncpg.connect(url.replace("postgresql+asyncpg", "postgresql"), timeout=5)
        try:
            oldest_id = await conn.fetchval(
                "INSERT INTO jobs (kind, payload, dedupe_key, state, attempts, created_at) "
                "VALUES ('process_item', '{}'::jsonb, 'movie:55645', 'deferred', 0, "
                "now() - interval '2 days') RETURNING id"
            )
            await conn.execute(
                "INSERT INTO jobs (kind, payload, dedupe_key, state, attempts, created_at) "
                "VALUES ('process_item', '{}'::jsonb, 'movie:55645', 'deferred', 0, "
                "now() - interval '1 day')"
            )
            await conn.execute(
                "INSERT INTO jobs (kind, payload, dedupe_key, state, attempts, created_at) "
                "VALUES ('process_item', '{}'::jsonb, 'movie:55645', 'deferred', 0, now())"
            )
            other_id = await conn.fetchval(
                "INSERT INTO jobs (kind, payload, dedupe_key, state, attempts) "
                "VALUES ('process_item', '{}'::jsonb, 'movie:99', 'pending', 0) RETURNING id"
            )
        finally:
            await conn.close()

        head = alembic("upgrade", "head")
        assert head.returncode == 0, (
            "migrating a jobs table with duplicate dedupe rows failed:\n"
            + head.stdout + head.stderr
        )

        conn = await asyncpg.connect(url.replace("postgresql+asyncpg", "postgresql"), timeout=5)
        try:
            rows = await conn.fetch(
                "SELECT id, state FROM jobs WHERE dedupe_key = 'movie:55645' ORDER BY id"
            )
            other_state = await conn.fetchval("SELECT state FROM jobs WHERE id = $1", other_id)
        finally:
            await conn.close()

        survivors = [r for r in rows if r["state"] != "dismissed"]
        assert len(survivors) == 1, f"expected exactly one survivor, got {[dict(r) for r in rows]}"
        assert survivors[0]["id"] == oldest_id, "the oldest row must be the one kept"
        assert survivors[0]["state"] == "deferred"
        dismissed_count = sum(1 for r in rows if r["state"] == "dismissed")
        assert dismissed_count == 2
        assert other_state == "pending", "an unrelated dedupe_key's row must be untouched"
    finally:
        maint = await asyncpg.connect(MAINTENANCE_DB_URL, timeout=3)
        try:
            await maint.execute(f'DROP DATABASE IF EXISTS "{SCRATCH_DB_NAME}_dedupe"')
        finally:
            await maint.close()


async def test_migration_collapses_duplicate_parked_dedupe_rows():
    """The Failures-page incident this migration exists to fix: before #138
    stopped re-selecting blocked items, every backfill press for one already
    -parked item minted a fresh job that failed and parked alongside the
    previous parked rows -- 12 piled up for one movie, all "Inside Out 2".
    uq_jobs_pending_dedupe never covered ``parked`` (only pending/deferred),
    so nothing stopped the pile from growing; fail()'s park arm now sweeps
    live going forward (see test_queue.py's
    test_fail_parking_dismisses_a_parked_sibling_sharing_the_dedupe_key), but
    the historical pile needs a one-time collapse. Unlike the pending/
    deferred collapse above (which keeps the *oldest* row), this keeps the
    *newest* (highest id) -- it carries the freshest failure reason, and
    Retry always acts on whichever row is left. Seeded here by raw INSERT: 3
    duplicate parked rows for one dedupe_key plus one unrelated parked row
    that must survive untouched. Walks to the revision before this
    migration, seeds, then upgrades to head and checks exactly one survivor
    remains.
    """
    if not await _postgres_reachable():
        _unreachable_postgres()

    before_collapse = "f3aec27ebea8"

    maint = await asyncpg.connect(MAINTENANCE_DB_URL, timeout=3)
    try:
        await maint.execute(f'DROP DATABASE IF EXISTS "{SCRATCH_DB_NAME}_parked"')
        await maint.execute(f'CREATE DATABASE "{SCRATCH_DB_NAME}_parked"')
    finally:
        await maint.close()

    url = SCRATCH_DB_URL.replace(SCRATCH_DB_NAME, SCRATCH_DB_NAME + "_parked")
    env = dict(os.environ, AUTOPOSTER_DATABASE_URL=url)

    def alembic(*args):
        return subprocess.run(
            [sys.executable, "-m", "alembic", *args],
            cwd=REPO_ROOT, env=env, capture_output=True, text=True,
        )

    try:
        step = alembic("upgrade", before_collapse)
        assert step.returncode == 0, step.stdout + step.stderr

        conn = await asyncpg.connect(url.replace("postgresql+asyncpg", "postgresql"), timeout=5)
        try:
            await conn.execute(
                "INSERT INTO jobs (kind, payload, dedupe_key, state, attempts, last_error) "
                "VALUES ('process_item', '{}'::jsonb, 'movie:tmdb:1000', 'parked', 5, "
                "'first press')"
            )
            await conn.execute(
                "INSERT INTO jobs (kind, payload, dedupe_key, state, attempts, last_error) "
                "VALUES ('process_item', '{}'::jsonb, 'movie:tmdb:1000', 'parked', 5, "
                "'second press')"
            )
            newest_id = await conn.fetchval(
                "INSERT INTO jobs (kind, payload, dedupe_key, state, attempts, last_error) "
                "VALUES ('process_item', '{}'::jsonb, 'movie:tmdb:1000', 'parked', 5, "
                "'newest press') RETURNING id"
            )
            other_id = await conn.fetchval(
                "INSERT INTO jobs (kind, payload, dedupe_key, state, attempts) "
                "VALUES ('process_item', '{}'::jsonb, 'movie:tmdb:2000', 'parked', 5) "
                "RETURNING id"
            )
        finally:
            await conn.close()

        head = alembic("upgrade", "head")
        assert head.returncode == 0, (
            "migrating a jobs table with duplicate parked rows failed:\n"
            + head.stdout + head.stderr
        )

        conn = await asyncpg.connect(url.replace("postgresql+asyncpg", "postgresql"), timeout=5)
        try:
            rows = await conn.fetch(
                "SELECT id, state, last_error FROM jobs WHERE dedupe_key = 'movie:tmdb:1000' "
                "ORDER BY id"
            )
            other_state = await conn.fetchval("SELECT state FROM jobs WHERE id = $1", other_id)
        finally:
            await conn.close()

        survivors = [r for r in rows if r["state"] != "dismissed"]
        assert len(survivors) == 1, f"expected exactly one survivor, got {[dict(r) for r in rows]}"
        assert survivors[0]["id"] == newest_id, "the newest row must be the one kept"
        assert survivors[0]["state"] == "parked"
        assert survivors[0]["last_error"] == "newest press"
        dismissed_count = sum(1 for r in rows if r["state"] == "dismissed")
        assert dismissed_count == 2
        assert other_state == "parked", "an unrelated dedupe_key's row must be untouched"
    finally:
        maint = await asyncpg.connect(MAINTENANCE_DB_URL, timeout=3)
        try:
            await maint.execute(f'DROP DATABASE IF EXISTS "{SCRATCH_DB_NAME}_parked"')
        finally:
            await maint.close()


async def test_the_runs_history_migration_is_reversible():
    """Roadmap row 53's table, up -> down -> up on a scratch database.

    `alembic check` above already proves the head matches the models. What
    this adds is the downgrade: a table created by `op.create_table` is only
    reversible if the downgrade actually drops it, and a migration whose
    downgrade is a no-op looks identical to a correct one until somebody
    needs to roll back a bad deploy. Walking down to the previous head and
    back up also proves the create is not accidentally dependent on state a
    fresh database happens to have.
    """
    if not await _postgres_reachable():
        _unreachable_postgres()

    before_runs = "a3f81c05d6e2"

    maint = await asyncpg.connect(MAINTENANCE_DB_URL, timeout=3)
    try:
        await maint.execute(f'DROP DATABASE IF EXISTS "{SCRATCH_DB_NAME}_runs"')
        await maint.execute(f'CREATE DATABASE "{SCRATCH_DB_NAME}_runs"')
    finally:
        await maint.close()

    url = SCRATCH_DB_URL.replace(SCRATCH_DB_NAME, SCRATCH_DB_NAME + "_runs")
    env = dict(os.environ, AUTOPOSTER_DATABASE_URL=url)

    def alembic(*args):
        return subprocess.run(
            [sys.executable, "-m", "alembic", *args],
            cwd=REPO_ROOT, env=env, capture_output=True, text=True,
        )

    async def table_exists() -> bool:
        conn = await asyncpg.connect(
            url.replace("postgresql+asyncpg", "postgresql"), timeout=5
        )
        try:
            return await conn.fetchval("SELECT to_regclass('public.runs') IS NOT NULL")
        finally:
            await conn.close()

    try:
        up = alembic("upgrade", "head")
        assert up.returncode == 0, up.stdout + up.stderr
        assert await table_exists(), "upgrade to head did not create `runs`"

        down = alembic("downgrade", before_runs)
        assert down.returncode == 0, down.stdout + down.stderr
        assert not await table_exists(), (
            "downgrade left `runs` behind; the revision is not reversible"
        )

        again = alembic("upgrade", "head")
        assert again.returncode == 0, again.stdout + again.stderr
        assert await table_exists(), "the second upgrade did not recreate `runs`"
    finally:
        maint = await asyncpg.connect(MAINTENANCE_DB_URL, timeout=3)
        try:
            await maint.execute(f'DROP DATABASE IF EXISTS "{SCRATCH_DB_NAME}_runs"')
        finally:
            await maint.close()


async def test_the_item_sort_positions_migration_is_reversible():
    """Roadmap row 269's table, up -> down -> up on a scratch database, the
    ``runs`` test's shape verbatim."""
    if not await _postgres_reachable():
        _unreachable_postgres()

    before = "c1d2e3f4a5b6"
    suffix = "_sortpos"

    maint = await asyncpg.connect(MAINTENANCE_DB_URL, timeout=3)
    try:
        await maint.execute(f'DROP DATABASE IF EXISTS "{SCRATCH_DB_NAME}{suffix}"')
        await maint.execute(f'CREATE DATABASE "{SCRATCH_DB_NAME}{suffix}"')
    finally:
        await maint.close()

    url = SCRATCH_DB_URL.replace(SCRATCH_DB_NAME, SCRATCH_DB_NAME + suffix)
    env = dict(os.environ, AUTOPOSTER_DATABASE_URL=url)

    def alembic(*args):
        return subprocess.run(
            [sys.executable, "-m", "alembic", *args],
            cwd=REPO_ROOT, env=env, capture_output=True, text=True,
        )

    async def table_exists() -> bool:
        conn = await asyncpg.connect(
            url.replace("postgresql+asyncpg", "postgresql"), timeout=5
        )
        try:
            return await conn.fetchval(
                "SELECT to_regclass('public.item_sort_positions') IS NOT NULL"
            )
        finally:
            await conn.close()

    try:
        up = alembic("upgrade", "head")
        assert up.returncode == 0, up.stdout + up.stderr
        assert await table_exists(), "upgrade to head did not create `item_sort_positions`"

        down = alembic("downgrade", before)
        assert down.returncode == 0, down.stdout + down.stderr
        assert not await table_exists(), (
            "downgrade left `item_sort_positions` behind; the revision is not reversible"
        )

        again = alembic("upgrade", "head")
        assert again.returncode == 0, again.stdout + again.stderr
        assert await table_exists(), "the second upgrade did not recreate `item_sort_positions`"
    finally:
        maint = await asyncpg.connect(MAINTENANCE_DB_URL, timeout=3)
        try:
            await maint.execute(f'DROP DATABASE IF EXISTS "{SCRATCH_DB_NAME}{suffix}"')
        finally:
            await maint.close()


async def test_the_jobs_state_widening_is_reversible():
    """`b3d91f7c05ea`, up -> down -> up on a scratch database.

    This one carries LOGIC, not just a schema move: narrowing
    `varchar(24) -> varchar(16)` rewrites the table and errors outright on any
    row whose state does not fit, so the downgrade has to rewrite
    `done_with_warnings` rows to `done` FIRST. A downgrade that lost that
    UPDATE would look identical to a correct one until somebody rolled back a
    deploy with such a row in the table -- and `alembic check` cannot catch it
    either: it compares the models against a database already at head and
    never runs a downgrade at all, so nothing it reports says whether one
    would survive the rows it would find.
    """
    if not await _postgres_reachable():
        _unreachable_postgres()

    before = "a3f7e15c92b8"
    suffix = "_jobstate"

    maint = await asyncpg.connect(MAINTENANCE_DB_URL, timeout=3)
    try:
        await maint.execute(f'DROP DATABASE IF EXISTS "{SCRATCH_DB_NAME}{suffix}"')
        await maint.execute(f'CREATE DATABASE "{SCRATCH_DB_NAME}{suffix}"')
    finally:
        await maint.close()

    url = SCRATCH_DB_URL.replace(SCRATCH_DB_NAME, SCRATCH_DB_NAME + suffix)
    env = dict(os.environ, AUTOPOSTER_DATABASE_URL=url)

    def alembic(*args):
        return subprocess.run(
            [sys.executable, "-m", "alembic", *args],
            cwd=REPO_ROOT, env=env, capture_output=True, text=True,
        )

    async def _with_conn(body):
        conn = await asyncpg.connect(
            url.replace("postgresql+asyncpg", "postgresql"), timeout=5
        )
        try:
            return await body(conn)
        finally:
            await conn.close()

    async def width(conn):
        return await conn.fetchval(
            "SELECT character_maximum_length FROM information_schema.columns "
            "WHERE table_name = 'jobs' AND column_name = 'state'"
        )

    async def seed(conn):
        await conn.execute(
            "INSERT INTO jobs (kind, payload, state, attempts, run_after, "
            "created_at, updated_at) VALUES ('process_item', '{}', "
            "'done_with_warnings', 1, now(), now(), now())"
        )

    async def states(conn):
        return [r["state"] for r in await conn.fetch("SELECT state FROM jobs")]

    try:
        up = alembic("upgrade", "head")
        assert up.returncode == 0, up.stdout + up.stderr
        assert await _with_conn(width) == 24, "upgrade to head did not widen jobs.state"
        await _with_conn(seed)

        down = alembic("downgrade", before)
        assert down.returncode == 0, down.stdout + down.stderr
        assert await _with_conn(states) == ["done"], (
            "the downgrade did not rewrite the row before narrowing the column"
        )
        assert await _with_conn(width) == 16, "the downgrade left jobs.state widened"

        again = alembic("upgrade", "head")
        assert again.returncode == 0, again.stdout + again.stderr
        assert await _with_conn(width) == 24, "the second upgrade did not widen jobs.state"
    finally:
        maint = await asyncpg.connect(MAINTENANCE_DB_URL, timeout=3)
        try:
            await maint.execute(f'DROP DATABASE IF EXISTS "{SCRATCH_DB_NAME}{suffix}"')
        finally:
            await maint.close()


async def test_the_perf_index_migration_is_reversible():
    """Perf spec D1 (`c3e8a1f5b7d2`), up -> down -> up on a scratch database.

    Index-only, so `alembic check` above already proves the head matches the
    models. What it cannot prove is the downgrade -- it never runs one. This
    compares the WHOLE `pg_indexes` picture rather than a list of names: the
    downgrade must restore exactly the index set the previous head had (every
    dropped duplicate back, the due index full again, the claim index as it
    was), and the second upgrade must land exactly where the first did. A
    downgrade that forgot one `create_index` would otherwise look identical to
    a correct one until somebody rolled back a deploy.
    """
    if not await _postgres_reachable():
        _unreachable_postgres()

    before = "a1f4c2d90e73"
    suffix = "_perfidx"

    maint = await asyncpg.connect(MAINTENANCE_DB_URL, timeout=3)
    try:
        await maint.execute(f'DROP DATABASE IF EXISTS "{SCRATCH_DB_NAME}{suffix}"')
        await maint.execute(f'CREATE DATABASE "{SCRATCH_DB_NAME}{suffix}"')
    finally:
        await maint.close()

    url = SCRATCH_DB_URL.replace(SCRATCH_DB_NAME, SCRATCH_DB_NAME + suffix)
    env = dict(os.environ, AUTOPOSTER_DATABASE_URL=url)

    def alembic(*args):
        return subprocess.run(
            [sys.executable, "-m", "alembic", *args],
            cwd=REPO_ROOT, env=env, capture_output=True, text=True,
        )

    async def indexes() -> dict[str, str]:
        conn = await asyncpg.connect(
            url.replace("postgresql+asyncpg", "postgresql"), timeout=5
        )
        try:
            rows = await conn.fetch(
                "SELECT indexname, indexdef FROM pg_indexes WHERE schemaname = 'public'"
            )
        finally:
            await conn.close()
        return {row["indexname"]: row["indexdef"] for row in rows}

    try:
        step = alembic("upgrade", before)
        assert step.returncode == 0, step.stdout + step.stderr
        previous = await indexes()

        up = alembic("upgrade", "head")
        assert up.returncode == 0, up.stdout + up.stderr
        head = await indexes()

        for dropped in (
            "ix_jobs_state",
            "ix_item_facts_item_id",
            "ix_render_deliveries_render_id",
            "ix_renders_item_id",
            "ix_item_metadata_overrides_item_id",
            "ix_action_dismissals_item_id",
        ):
            assert dropped in previous, f"{dropped} was expected at {before}"
            assert dropped not in head, f"{dropped} survived the upgrade"
        assert "(state, updated_at DESC, id DESC)" in head["ix_jobs_state_updated"]
        assert "(dedupe_key, id DESC)" in head["ix_jobs_latest_per_key"]
        assert "WHERE" in head["ix_jobs_latest_per_key"]
        assert "(next_attempt_at, id)" in head["ix_render_deliveries_next_attempt_at"]
        assert "WHERE" in head["ix_render_deliveries_next_attempt_at"]
        assert "WHERE" not in previous["ix_render_deliveries_next_attempt_at"]
        # The claim index -- Task 1 Step 5's decision (the plan's variant A).
        assert "ix_jobs_claimable" not in head
        assert "(run_after, id)" in head["ix_jobs_claim"]
        assert "WHERE" in head["ix_jobs_claim"]

        down = alembic("downgrade", before)
        assert down.returncode == 0, down.stdout + down.stderr
        assert await indexes() == previous, "the downgrade did not restore the previous index set"

        again = alembic("upgrade", "head")
        assert again.returncode == 0, again.stdout + again.stderr
        assert await indexes() == head, "the second upgrade did not land where the first did"
    finally:
        maint = await asyncpg.connect(MAINTENANCE_DB_URL, timeout=3)
        try:
            await maint.execute(f'DROP DATABASE IF EXISTS "{SCRATCH_DB_NAME}{suffix}"')
        finally:
            await maint.close()
