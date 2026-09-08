"""``config/live.py``: the swap primitive and the restart-flag map.

``swap_config`` is what Task 3's write endpoint will call once a new
generation has been built and validated. It is tested here on its own, before
any endpoint exists, because the property that matters is not "the endpoint
returned 200" -- it is that everything reading the config is looking at the
new generation afterwards, through whichever of the three seams it uses: the
holder, the rebound ``app.state.config``, or the published cadence mapping.
"""
from pathlib import Path

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from autoposter.api.auth import hash_password
from autoposter.app import create_app
from autoposter.config.live import FROZEN_SECTIONS, LIVE_EXCEPTIONS, frozen_reason, swap_config
from autoposter.config.loader import load_config
from autoposter.config.schema import Config, Secrets
from autoposter.scheduler.core import Job

EXAMPLE = Path(__file__).parent.parent / "config" / "autoposter.example.yaml"
PASSWORD = "correct horse battery staple"


@pytest.fixture
def config():
    return load_config(EXAMPLE)


@pytest_asyncio.fixture
async def app(session_factory, config):
    secrets = Secrets(
        database_url="postgresql+asyncpg://unused",
        plex_token="x", tmdb_token="x", tvdb_apikey="x",
        fanart_apikey="x", webhook_secret="x",
        admin_password_hash=hash_password(PASSWORD),
    )
    return create_app(config, session_factory, secrets)


@pytest_asyncio.fixture
async def client(app):
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


@pytest_asyncio.fixture
async def auth_headers(client):
    response = await client.post("/api/login", json={"password": PASSWORD})
    return {"Authorization": f"Bearer {response.json()['token']}"}


def _with_workers(config: Config, workers: int) -> Config:
    return config.model_copy(update={"workers": workers})


# --- create_app publishes the holder ---


def test_create_app_publishes_a_holder_around_the_config_it_was_given(app, config):
    """Every application has a holder, not just the one ``main.build()``
    makes: consumers that want liveness are handed it at construction time
    (the worker handler, the scheduler jobs, the dashboard broadcaster), so an
    application without one could not be built at all."""
    assert app.state.config_holder.current is config
    assert app.state.config is config


def test_the_broadcaster_is_handed_the_holder_not_the_config(app):
    """A broadcaster holding the Config instance would report the boot
    generation on the live stream forever while /api/status, which reads the
    rebound app.state.config, reported the new one."""
    assert app.state.dashboard_broadcaster._config_holder is app.state.config_holder


# --- the swap ---


def test_a_swap_rebinds_app_state_config_to_the_new_generation(app, config):
    swapped = _with_workers(config, config.workers + 7)
    swap_config(app, swapped)

    assert app.state.config is swapped
    assert app.state.config_holder.current is swapped


async def test_a_per_request_reader_sees_the_swapped_config(app, client, auth_headers, config):
    """The acceptance item: not just that ``app.state.config`` points at the
    new object, but that a request handler reading it per request answers with
    it. ``/api/status`` reports ``config.workers``, so the same request before
    and after the swap must give two different answers.

    ``workers`` is deliberately the probe *and* a FROZEN_SECTIONS entry: the
    reported number is the configured one, the pool is still the size it was
    started at, and the editor is what tells the operator so -- asserted just
    below.
    """
    before = (await client.get("/api/status", headers=auth_headers)).json()["workers"]
    assert before == config.workers

    swap_config(app, _with_workers(config, config.workers + 7))

    after = (await client.get("/api/status", headers=auth_headers)).json()["workers"]
    assert after == config.workers + 7, (
        "the status endpoint still reports the boot generation's worker count; "
        "app.state.config was not rebound by the swap"
    )
    assert frozen_reason("workers") is not None, (
        "the number above is the configured one, not the running pool size -- "
        "the restart flag is the only thing that says so"
    )


def test_a_swap_refreshes_the_published_cadences_without_rebinding_the_mapping(app, config):
    """``app.state.scheduler_intervals`` is aliased by the dashboard
    broadcaster (see app.py and the identity test in test_app.py), so a swap
    has to update it in place. Rebinding it would leave the live stream
    reporting the boot cadences forever.
    """
    held = app.state.scheduler_intervals
    app.state.scheduler_jobs = [
        Job(
            name="ratings_drift_sweep",
            interval_seconds=lambda: app.state.config_holder.current.scheduler.drift_days
            * 24 * 3600,
            run=None,
        )
    ]
    swap_config(app, config)
    assert held["ratings_drift_sweep"] == config.scheduler.drift_days * 24 * 3600

    faster = config.model_copy(
        update={"scheduler": config.scheduler.model_copy(update={"drift_days": 1})}
    )
    swap_config(app, faster)

    assert app.state.scheduler_intervals is held, (
        "the swap rebound app.state.scheduler_intervals; the broadcaster still "
        "holds the mapping create_app gave it and would report stale cadences"
    )
    assert held["ratings_drift_sweep"] == 24 * 3600


def test_a_swap_drops_cadences_for_jobs_that_are_no_longer_registered(app, config):
    """A name left in the mapping after its job is gone would be reported as
    a cadence nothing honours -- exactly what status_snapshot's merge-by-name
    exists to avoid on the database side."""
    app.state.scheduler_intervals["a_job_that_went_away"] = 999
    app.state.scheduler_jobs = []

    swap_config(app, config)

    assert "a_job_that_went_away" not in app.state.scheduler_intervals


# --- the restart-flag map ---


def test_every_frozen_section_carries_a_reason():
    assert FROZEN_SECTIONS
    for path, reason in FROZEN_SECTIONS.items():
        assert reason.strip(), path
        assert "\n" not in reason.strip(), f"{path}'s reason is not one line"


@pytest.mark.parametrize(
    "path",
    [
        "workers",
        "providers",
        "providers.order",
        "notifications",
        "notifications.url",
        "plex",
        "plex.url",
        "operations.imdb_refresh_enabled",
        "operations.imdb_refresh_hours",
        "operations.tmdb_backoff_seconds",
        "api_docs_enabled",
        "scheduler.enabled",
        "collections.enabled",
        "arr_sync.enabled",
    ],
)
def test_a_setting_a_swap_cannot_reach_is_flagged(path):
    assert frozen_reason(path) is not None, f"{path} is frozen at startup but not flagged"


@pytest.mark.parametrize(
    "path",
    [
        # Read off the config every time a job fails against Plex
        # (app.py's _handle_intent), despite living under the frozen `plex`.
        "plex.resolve_max_attempts",
        # Cadences: resolved per poll by claim_due through the holder.
        "scheduler.collections_hours",
        "scheduler.drift_days",
        "scheduler.cleanup_days",
        "arr_sync.hours",
        # Read per item off the holder inside the pipeline.
        "artwork.poster.enabled",
        "badges.upload_to_plex",
        "assets_root",
        "collections.apply_to_plex",
        "cleanup.apply",
        "operations.write_to_plex",
    ],
)
def test_a_setting_a_swap_does_reach_is_not_flagged(path):
    assert frozen_reason(path) is None, f"{path} is live but reported as needing a restart"


def test_a_live_exception_beats_the_frozen_prefix_containing_it():
    """``plex`` is frozen as a section and ``plex.resolve_max_attempts`` is
    live inside it. Without the exception, longest-prefix matching alone would
    flag it."""
    assert LIVE_EXCEPTIONS == frozenset({"plex.resolve_max_attempts"})
    assert frozen_reason("plex") is not None
    assert frozen_reason("plex.resolve_max_attempts") is None


def test_per_collection_webhooks_are_not_frozen():
    """Facts adjudication 3, the frozen split. The global notifications block
    is frozen -- the notifier is built once at startup -- but the
    per-collection URLs are fields on a definition the engine reads off the
    live config on every pass, so an edited one applies at the next pass and
    the settings editor must not claim otherwise.

    Asserted through ``frozen_reason`` rather than by scanning the prefixes:
    ``collections.enabled`` IS frozen, for the unrelated reason that the job
    set is registered once at startup, and it is a leaf prefix that does not
    cover the definitions beside it."""
    assert "notifications" in FROZEN_SECTIONS
    assert frozen_reason("notifications") is not None
    assert frozen_reason("collections.definitions") is None


def test_the_deployment_url_is_not_frozen():
    """`public_url` is read by the setup wizard and by the Settings rotation
    action, never by an object built at startup -- so a restart pill on it
    would be a promise the code does not make. Recon section 5.2."""
    from autoposter.config.live import FROZEN_SECTIONS, frozen_reason, is_inert

    assert "public_url" not in FROZEN_SECTIONS
    assert frozen_reason("public_url") is None
    assert is_inert("public_url") is False


def test_the_actionable_digest_knob_is_not_frozen():
    """Roadmap row 236's knob is a top-level setting, not a `notifications`
    field, precisely so the editor does not put a restart pill on it: the
    emitter re-reads it off the config holder on every scheduler tick, so a
    saved change applies at the next tick. A knob under `notifications` would
    be reported as "restart to apply" -- true of the notifier object, false of
    this switch -- which is a promise the code does not make."""
    from autoposter.config.live import frozen_reason, is_inert
    from autoposter.config.schema import Config

    # First, because `frozen_reason` is a prefix map over strings and would
    # answer None for a key that does not exist at all: this asserts the knob
    # is a real top-level field before asserting anything about how the editor
    # classifies it.
    assert "actionable_digest_enabled" in Config.model_fields
    assert frozen_reason("actionable_digest_enabled") is None
    assert is_inert("actionable_digest_enabled") is False
    assert frozen_reason("notifications") is not None, (
        "precondition: the notifications prefix IS frozen, which is why this "
        "knob is not in it"
    )
