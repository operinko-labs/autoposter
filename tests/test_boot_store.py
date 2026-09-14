"""Boot reads the store before it decides anything, and hands it on.

Three claims, and they fail independently.

A stored secret reaches the boot resolver at all -- otherwise the store is a
table the running service can write and nothing ever reads.

A store that cannot be read is an empty map rather than a refusal: no database,
no table yet, no key, an unreachable host, a host that accepts the connection
and then stops answering. The layers beneath the store are how every deployment
that predates it is configured, so a boot that exited over an empty table would
break all of them at once, with no UI left from which it could be fixed -- and
"never blocks a boot" has to be a bound rather than an intention.

And the FIRST-START WIZARD sees what boot read. The wizard has no database
session of its own; the admin password hash may live in the store; and a wizard
that could not see it would treat a deployment that has a password as one that
has never had one and hand a setup token to whoever asked first.
"""
import asyncio
import os
import time
from pathlib import Path

import pytest
import yaml
from httpx import ASGITransport, AsyncClient

from autoposter import boot
from autoposter.api.auth import hash_password
from autoposter.config import secret_store
from autoposter.config import state as state_module
from autoposter.config.overrides import seed_store, store_meta, write_store
from autoposter.config.schema import (
    ENVIRONMENT_SECRET_NAMES_ENV,
    STATE_FILE_NAMES_ENV,
    STORED_SECRET_NAMES_ENV,
)
from autoposter.config.state import STATE_DIR_ENV
from autoposter.db import base as db_base

HARD = (
    "AUTOPOSTER_DATABASE_URL",
    "AUTOPOSTER_TMDB_TOKEN",
    "AUTOPOSTER_TVDB_APIKEY",
    "AUTOPOSTER_FANART_APIKEY",
    "AUTOPOSTER_WEBHOOK_SECRET",
)

SOFT = (
    "AUTOPOSTER_ADMIN_PASSWORD_HASH",
    "AUTOPOSTER_API_KEY",
    "AUTOPOSTER_MDBLIST_APIKEY",
    "AUTOPOSTER_PLEX_TOKEN",
    "AUTOPOSTER_JELLYFIN_APIKEY",
)

MASTER_PASSWORD = "a-real-master-password"


@pytest.fixture(autouse=True)
def clean_secret_environment(monkeypatch, tmp_path):
    """No inherited credentials, no inherited markers, and a state directory
    nobody else shares.

    Saved and restored by hand rather than through `monkeypatch.delenv` alone,
    for `tests/test_boot.py`'s reason: `boot.main` assigns into `os.environ`
    directly -- the three markers ahead of `_export`, then every resolved
    value -- and monkeypatch records no undo entry for a name that was absent
    when the test began, so a boot test would otherwise leak a credential into
    every test after it.
    """
    names = (
        *HARD,
        *SOFT,
        "AUTOPOSTER_CONFIG",
        STATE_FILE_NAMES_ENV,
        STORED_SECRET_NAMES_ENV,
        ENVIRONMENT_SECRET_NAMES_ENV,
    )
    saved = {name: os.environ[name] for name in names if name in os.environ}
    for name in names:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv(STATE_DIR_ENV, str(tmp_path / "state"))
    yield
    for name in names:
        os.environ.pop(name, None)
    os.environ.update(saved)


@pytest.fixture
def database_url():
    """The database this pytest process owns.

    ``tests/conftest.py`` exposes no fixture for the URL itself, but it writes
    the per-worker URL it derived back into this variable -- so reading the
    variable here names exactly the database ``session_factory`` writes into,
    under xdist as well as serially.
    """
    return os.environ["AUTOPOSTER_TEST_DATABASE_URL"]


@pytest.fixture
def tableless_database_url():
    """A database that answers but holds no ``stored_secrets`` table: the
    ordinary first boot, before ``alembic upgrade head`` has ever run.

    The compose file's maintenance database, which the suite already requires
    and which this project's migrations never touch.
    """
    url = os.environ["AUTOPOSTER_MAINTENANCE_DATABASE_URL"]
    return url.replace("postgresql://", "postgresql+asyncpg://", 1)


def _in_a_thread(url: str):
    """`stored_secrets_for_boot`, called the way `boot.main` calls it.

    It owns an `asyncio.run`, which is correct for the frame it is written for
    -- `main` is synchronous and runs before uvicorn -- and raises outright
    from inside a running loop like an async test's. Calling it directly there
    would land in its own catch-all and answer `{}`, which is
    indistinguishable from a store that held nothing: the test would pass for
    the wrong reason the day the read broke.
    """
    return asyncio.to_thread(boot.stored_secrets_for_boot, url)


@pytest.mark.asyncio
async def test_boot_reads_the_stored_secrets(session_factory, database_url):
    async with session_factory() as session:
        await secret_store.store_secret(session, "AUTOPOSTER_TMDB_TOKEN", "stored-tok")
        await session.commit()

    assert await _in_a_thread(database_url) == {"AUTOPOSTER_TMDB_TOKEN": "stored-tok"}


def test_boot_answers_empty_when_the_database_is_unreachable():
    """The resolver then falls through to the state file and the environment,
    which is exactly how every deployment that predates this behaved."""
    assert (
        boot.stored_secrets_for_boot(
            "postgresql+asyncpg://nobody:nobody@127.0.0.1:1/nothing"
        )
        == {}
    )


def test_boot_answers_empty_when_there_is_no_database_url_at_all():
    """The first-start shape: the wizard has not been through yet, so nothing
    names a database and there is nothing to connect to. Answered without an
    engine rather than by failing to build one."""
    assert boot.stored_secrets_for_boot("") == {}


@pytest.mark.asyncio
async def test_boot_answers_empty_when_the_table_does_not_exist_yet(tableless_database_url):
    """The ordinary first boot, and the one failure this function's own
    `except` is uniquely responsible for: the database answers perfectly and
    the migration that creates the table has not run, because it runs AFTER
    this read. A refusal here would mean no deployment could ever migrate.
    """
    assert await _in_a_thread(tableless_database_url) == {}


@pytest.mark.asyncio
async def test_a_database_that_never_answers_does_not_block_the_boot(
    monkeypatch, session_factory, database_url
):
    """A host that accepts the connection and then stops answering -- a paused
    VM, a failing-over pgbouncer, a DROP rule applied after accept -- is the
    case that hangs forever without a bound. Nothing bounds it underneath:
    only asyncpg has an implicit connect bound and no dialect bounds the
    QUERY. The boot decision, the migration and the exec all sit behind this
    read.
    """

    async def never_answers(session):
        await asyncio.sleep(30)
        raise AssertionError("the read must have been abandoned, not waited out")

    monkeypatch.setattr(secret_store, "load_stored_secrets", never_answers)
    monkeypatch.setattr(db_base, "PROBE_TIMEOUT_SECONDS", 0.2)

    started = time.monotonic()
    stored = await _in_a_thread(database_url)
    elapsed = time.monotonic() - started

    assert stored == {}
    assert elapsed < 10, "the read is abandoned on a bound, not waited out"


# --- what the wizard sees ---------------------------------------------------


def _write_state_secrets(values: dict[str, str]) -> None:
    path = state_module.secrets_file_path()
    state_module.write_state_file(path, state_module.render_secrets_file(values))


def _serve_the_wizard(monkeypatch):
    """Run a boot that lands in setup mode and hand back the application it
    would have served.

    Through `boot.main` rather than through `build_setup_app` directly, because
    the claim is about the ORDER inside `main`: the export has to happen before
    the branch, and a test that built the wizard itself would pass with the
    export on the wrong side of that line.
    """
    served: list[object] = []
    monkeypatch.setattr(boot, "_migrate", lambda: None)
    monkeypatch.setattr(boot.os, "execv", lambda path, argv: None)
    monkeypatch.setattr(boot.uvicorn, "run", lambda app, **kwargs: served.append(app))
    boot.main([])
    assert len(served) == 1, "this boot was meant to land in setup mode"
    return served[0]


@pytest.mark.asyncio
async def test_the_first_start_wizard_sees_a_stored_admin_password_hash(
    monkeypatch, session_factory, database_url
):
    """The store holds this deployment's admin hash, and the wizard must find
    it. It has no session of its own, so `boot` publishes what it read before
    it decides anything -- and a wizard that missed the hash would read "no
    password has ever been set" from the empty string and hand a setup token
    to whoever posted first.
    """
    from autoposter.api import setup as setup_api

    hashed = await asyncio.to_thread(hash_password, MASTER_PASSWORD)
    async with session_factory() as session:
        await secret_store.store_secret(session, "AUTOPOSTER_ADMIN_PASSWORD_HASH", hashed)
        await session.commit()
    # Configured but for one hard name, which is what puts this boot in setup
    # mode. The admin hash has nothing to do with that decision, which is
    # exactly why the wizard can be reached on a deployment that has one.
    _write_state_secrets(
        {
            "AUTOPOSTER_DATABASE_URL": database_url,
            "AUTOPOSTER_TMDB_TOKEN": "x",
            "AUTOPOSTER_TVDB_APIKEY": "x",
            "AUTOPOSTER_WEBHOOK_SECRET": "x",
        }
    )
    state_module.write_state_file(
        state_module.state_config_path(),
        yaml.safe_dump({"workers": 2, "plex": {"url": "https://plex.example"}}),
    )

    wizard = await asyncio.to_thread(_serve_the_wizard, monkeypatch)

    assert setup_api._persisted_admin_hash() == hashed
    assert os.environ[STORED_SECRET_NAMES_ENV] == "AUTOPOSTER_ADMIN_PASSWORD_HASH"

    transport = ASGITransport(app=wizard)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        refused = await client.post("/api/setup/password", json={"password": "not-the-one"})
        accepted = await client.post("/api/setup/password", json={"password": MASTER_PASSWORD})

    assert refused.status_code == 401, "a wrong password must not mint a setup token"
    assert "token" not in refused.json()
    assert accepted.status_code == 200, "the real password still gets in"


# --- the store answers "configured" -----------------------------------------
#
# Three claims again, and the same independence.
#
# The DOCUMENT the boot decision is taken over is the store's when there is
# one, so a server added from the Settings page is a server the next boot
# knows about and a deployment whose ConfigMap has been removed still boots.
#
# The store's answer is the whole answer: a stored server whose credential is
# unset is still not configured, and a store that holds nothing -- or holds a
# delta from before the store became the document -- sends the question back
# to the file, which is what every deployment that predates this gets.
#
# And both entry points that read a document at boot tolerate a missing file:
# `boot.main`, which decides the mode, and `main.build`, which builds the
# application object the mode hands over to. Before this, a deployment whose
# document and secrets both lived in the database was told it was configured
# and then died in `build()` on FileNotFoundError.

EXAMPLE = Path(__file__).parent.parent / "config" / "autoposter.example.yaml"


def _example_document() -> dict:
    return yaml.safe_load(EXAMPLE.read_text(encoding="utf-8"))


def _document_in_a_thread(url: str):
    """`stored_config_document`, called the way `boot.main` calls it.

    `_in_a_thread`'s reason, restated because the trap is the same one: this
    function owns an `asyncio.run` too, so calling it directly from an async
    test would land in its own catch-all and answer `None` -- which is
    indistinguishable from an empty store, and would pass for the wrong reason
    the day the read broke.
    """
    return asyncio.to_thread(boot.stored_config_document, url)


def _hard_secrets(database_url: str, **extra: str) -> dict[str, str]:
    return {
        "AUTOPOSTER_DATABASE_URL": database_url,
        "AUTOPOSTER_TMDB_TOKEN": "x",
        "AUTOPOSTER_TVDB_APIKEY": "x",
        "AUTOPOSTER_FANART_APIKEY": "x",
        "AUTOPOSTER_WEBHOOK_SECRET": "x",
        **extra,
    }


@pytest.mark.asyncio
async def test_configured_is_answered_from_the_store_not_the_file(
    tmp_path, monkeypatch, session_factory, database_url
):
    """The file on the volume names no server at all; the store names Plex.
    The deployment is configured, because the store is what runs."""
    monkeypatch.setenv("AUTOPOSTER_CONFIG", str(tmp_path / "autoposter.yaml"))
    (tmp_path / "autoposter.yaml").write_text("workers: 2\n", encoding="utf-8")

    async with session_factory() as session:
        await seed_store(session, _example_document())
        await session.commit()

    resolved = _hard_secrets(database_url, AUTOPOSTER_PLEX_TOKEN="x")
    stored = await _document_in_a_thread(database_url)
    assert stored is not None and "plex" in stored
    assert boot.is_configured(resolved, stored) is True
    assert boot.is_configured(resolved, None) is False, (
        "the file alone names no server, which is what makes this test about "
        "the store rather than about the file behind it"
    )


@pytest.mark.asyncio
async def test_a_stored_server_without_its_credential_is_not_configured(
    session_factory, database_url
):
    """The store's answer is the whole answer and not a shortcut past the
    second half of the gate: a document that names Plex on a deployment with
    no Plex token is the shape the wizard exists to fix."""
    async with session_factory() as session:
        await seed_store(session, _example_document())
        await session.commit()

    stored = await _document_in_a_thread(database_url)
    assert boot.is_configured(_hard_secrets(database_url), stored) is False


def test_an_empty_store_leaves_the_file_answering(tmp_path, monkeypatch):
    """Every deployment that predates this: no store, a mounted file, and the
    same answer it has always given."""
    monkeypatch.setenv("AUTOPOSTER_CONFIG", str(tmp_path / "autoposter.yaml"))
    (tmp_path / "autoposter.yaml").write_text(
        EXAMPLE.read_text(encoding="utf-8"), encoding="utf-8"
    )
    unreachable = "postgresql+asyncpg://nobody@127.0.0.1:1/x"
    assert boot.stored_config_document(unreachable) is None
    assert (
        boot.is_configured(_hard_secrets(unreachable, AUTOPOSTER_PLEX_TOKEN="x"), None)
        is True
    )


def test_no_database_url_at_all_reads_no_store():
    """The first-start shape, answered without building an engine."""
    assert boot.stored_config_document("") is None


@pytest.mark.asyncio
async def test_a_delta_era_store_leaves_the_file_answering(
    session_factory, database_url
):
    """A row from before the store became the document is a PARTIAL document:
    a handful of overridden leaves that mean nothing without the file they
    were a delta of. Answering the boot decision with one would tell a
    deployment that has run for a year that it names no media server, and put
    an unauthenticated wizard on its port. `load_effective_config` gates on
    the same metadata and converts the delta where there is a session for it.
    """
    async with session_factory() as session:
        await write_store(session, {"workers": 3}, store_meta(format_=1))
        await session.commit()

    assert await _document_in_a_thread(database_url) is None


@pytest.mark.asyncio
async def test_boot_hands_over_to_the_application_with_no_file_anywhere(
    monkeypatch, session_factory, database_url
):
    """The deployment this row exists for, through `boot.main` itself: the
    document is in the store, the credentials resolve, and there is no mounted
    file at either path. Before this it was served the wizard."""
    async with session_factory() as session:
        await seed_store(session, _example_document())
        await session.commit()
    _write_state_secrets(_hard_secrets(database_url, AUTOPOSTER_PLEX_TOKEN="x"))
    assert not state_module.state_config_path().is_file()

    served: list[object] = []
    exec_calls: list[list[str]] = []
    monkeypatch.setattr(boot, "_migrate", lambda: None)
    monkeypatch.setattr(boot.uvicorn, "run", lambda app, **kwargs: served.append(app))
    monkeypatch.setattr(boot.os, "execv", lambda path, argv: exec_calls.append(argv))

    await asyncio.to_thread(boot.main, [])

    assert served == [], "a deployment whose store holds its document is configured"
    assert exec_calls == [list(boot.DEFAULT_COMMAND)]


@pytest.mark.asyncio
async def test_build_reads_the_store_when_there_is_no_configuration_file(
    tmp_path, monkeypatch, session_factory, database_url
):
    """`main.build()` is the other half of the same deployment, and the half
    that used to raise FileNotFoundError after `boot` had already decided the
    deployment was configured and exec'd it.

    Called straight from this test's own running event loop, which is the
    `uvicorn autoposter.main:build --factory` shape: the store read may not
    bridge an async call with `asyncio.run` from there.
    """
    import autoposter.main as main_module
    from autoposter.config.schema import Secrets

    document = _example_document()
    document["plex"]["url"] = "http://plex.from-the-store.test:32400"
    async with session_factory() as session:
        await seed_store(session, document)
        await session.commit()

    class _Secrets:
        @staticmethod
        def from_env() -> Secrets:
            return Secrets(
                database_url=database_url,
                plex_token="x", tmdb_token="x", tvdb_apikey="x",
                fanart_apikey="x", webhook_secret="x", admin_password_hash="",
            )

    monkeypatch.setattr(main_module, "CONFIG_PATH", tmp_path / "nothing.yaml")
    monkeypatch.setattr(main_module, "Secrets", _Secrets)
    monkeypatch.setattr(main_module, "make_engine", lambda url: object())
    monkeypatch.setattr(main_module, "spa_dist", lambda: None)

    app = main_module.build()

    assert app.state.config.plex.url == "http://plex.from-the-store.test:32400"


@pytest.mark.asyncio
async def test_the_wizard_is_not_asked_for_a_document_the_store_already_holds(
    monkeypatch, session_factory, database_url
):
    """A deployment configured from the UI and then sent back here by ONE
    blanked credential still has its document -- in the store, which the
    wizard has no session to read. `boot` hands over what it read, so the
    config step is not offered; without it the operator would be asked for a
    second document, and the finish step would write it to a volume the next
    boot does not read.
    """
    async with session_factory() as session:
        await seed_store(session, _example_document())
        await session.commit()
    # Every hard name but one, which is what puts this boot in setup mode.
    held = _hard_secrets(database_url, AUTOPOSTER_PLEX_TOKEN="x")
    del held["AUTOPOSTER_TMDB_TOKEN"]
    _write_state_secrets(held)

    from autoposter.api import setup as setup_api

    wizard = await asyncio.to_thread(_serve_the_wizard, monkeypatch)
    # The upgrade the finish step runs for itself. This database's schema was
    # built from the models rather than from the migrations, so a real alembic
    # run fails on the first table it already has -- which is tolerated, and
    # is not what this test is about.
    monkeypatch.setattr(setup_api, "_migrate_for_the_store", lambda url: True)

    transport = ASGITransport(app=wizard)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        token = (
            await client.post("/api/setup/password", json={"password": MASTER_PASSWORD})
        ).json()["token"]
        headers = {"X-Setup-Token": token}
        progress = await client.get("/api/setup/progress", headers=headers)
        # The one name this deployment is missing, which is the only reason
        # the wizard was served at all.
        typed = await client.post(
            "/api/setup/providers",
            json={"values": {"AUTOPOSTER_TMDB_TOKEN": "typed-at-the-wizard"}},
            headers=headers,
        )
        finished = await client.post("/api/setup/finish", headers=headers)

    body = progress.json()
    assert body["config"] is True
    assert body["config_source"] == "configured"
    assert body["servers"]["plex"]["configured"] is True
    assert typed.status_code == 200, typed.text
    # And the wizard can be FINISHED. The gate at the end of that route asks
    # the same question the next boot will: with no file at either path, one
    # that read the volume instead would answer 400 naming the one step this
    # page deliberately hides, with nothing an operator could do about it.
    assert finished.status_code == 200, finished.text
    assert finished.json() == {"restarting": True}


@pytest.mark.asyncio
async def test_a_document_cannot_be_staged_over_the_one_the_store_holds(
    monkeypatch, session_factory, database_url
):
    """The config step's refusal is a SERVER rule and not a client courtesy:
    the page stops offering the step, and a direct POST must not route around
    it.

    Accepted, the staged document would be written to the volume at finish,
    declined by a store that already holds one, and then ignored by the next
    boot, which asks the store first -- the operator's work discarded without
    a word.
    """
    async with session_factory() as session:
        await seed_store(session, _example_document())
        await session.commit()
    held = _hard_secrets(database_url, AUTOPOSTER_PLEX_TOKEN="x")
    del held["AUTOPOSTER_TMDB_TOKEN"]
    _write_state_secrets(held)

    from autoposter.api import setup as setup_api

    wizard = await asyncio.to_thread(_serve_the_wizard, monkeypatch)

    transport = ASGITransport(app=wizard)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        token = (
            await client.post("/api/setup/password", json={"password": MASTER_PASSWORD})
        ).json()["token"]
        response = await client.post(
            "/api/setup/config",
            json={"plex_url": "http://plex.elsewhere.test:32400"},
            headers={"X-Setup-Token": token},
        )

    assert response.status_code == 400
    assert response.json()["detail"] == setup_api.CONFIG_ALREADY_PROVIDED
    assert wizard.state.setup.config_document is None


@pytest.mark.asyncio
async def test_a_mounted_file_makes_build_read_no_store_at_all(tmp_path, monkeypatch):
    """The cheap half of the same decision. Every deployment that mounts a
    document must go on paying nothing for the store at build time -- a
    regression that always read it would still be green on the test above,
    because that one has no file to prefer.
    """
    import autoposter.main as main_module
    from autoposter.config.schema import Secrets

    path = tmp_path / "autoposter.yaml"
    path.write_text(EXAMPLE.read_text(encoding="utf-8"), encoding="utf-8")

    def _must_not_be_read(url):
        raise AssertionError("the file is there; the store must not be read")

    class _Secrets:
        @staticmethod
        def from_env() -> Secrets:
            return Secrets(
                database_url="postgresql+asyncpg://unused",
                plex_token="x", tmdb_token="x", tvdb_apikey="x",
                fanart_apikey="x", webhook_secret="x", admin_password_hash="",
            )

    monkeypatch.setattr(main_module, "CONFIG_PATH", path)
    monkeypatch.setattr(main_module, "stored_config_document", _must_not_be_read)
    monkeypatch.setattr(main_module, "Secrets", _Secrets)
    monkeypatch.setattr(main_module, "make_engine", lambda url: object())
    monkeypatch.setattr(main_module, "spa_dist", lambda: None)

    app = main_module.build()

    assert app.state.config.plex.url == "https://<plex-host>"


def test_build_refuses_when_neither_the_file_nor_the_store_answers(tmp_path, monkeypatch):
    """Unreachable through `boot`, which serves the wizard for this shape
    rather than exec'ing this module -- so it is raised rather than invented,
    and it names both places without claiming the deployment was never
    configured. From here an outage and an empty store are the same fact, and
    the read's own log line is what tells them apart.
    """
    import autoposter.main as main_module

    monkeypatch.setattr(main_module, "CONFIG_PATH", tmp_path / "nothing.yaml")

    with pytest.raises(ValueError) as caught:
        main_module._boot_config("postgresql+asyncpg://nobody@127.0.0.1:1/x")

    message = str(caught.value)
    assert "nothing.yaml" in message
    assert "answered none" in message
    assert "nobody" not in message, "the refusal never carries the DSN"
