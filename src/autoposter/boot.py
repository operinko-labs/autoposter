"""The container's entrypoint: decide first, then hand over.

Every deployment's boot used to be one shell line --
``alembic upgrade head && exec python -m autoposter.main`` -- and that line is
unreachable without a database URL: ``alembic/env.py`` raises, ``sh`` exits
non-zero, ``&&`` short-circuits, and the container never reaches Python at
all. A first-start wizard living inside the FastAPI application is unreachable
behind it no matter how it is written, which is why this module is the
entrypoint rather than a branch inside ``main.build()``.

The decision is re-derived here at every boot and stored nowhere. There is no
"setup complete" marker: a marker is a second source of truth that can
disagree with the credentials, and the disagreement's failure mode is a
process that will not start and will not offer to be fixed.

CONFIGURED means all three, in this order:

1. every hard secret resolves -- the stored row, then the state file, then the
   environment (``config/schema.resolve_secret_values``), with an EMPTY value
   counting as absent on every layer. ``AUTOPOSTER_DATABASE_URL`` is the one
   name the stored layer cannot answer, and not by policy: reading the store
   needs it, so ``main``'s first resolve is the two lower layers and only its
   second has the store on top of them;
2. a config document is readable -- the ``AUTOPOSTER_CONFIG`` path when that
   file exists, the state directory's ``autoposter.yaml`` otherwise
   (``config/loader.config_document_path``);
3. that document names at least one media server, and every server it names
   has its own credential set (``config/schema.missing_server_setup``) -- a
   plex-only deployment's ``AUTOPOSTER_PLEX_TOKEN``, a jellyfin-only one's
   ``AUTOPOSTER_JELLYFIN_APIKEY``, both if both are configured.

The database is NOT part of the decision. It is read once, for the stored
secrets (``stored_secrets_for_boot`` below), and every failure of that read --
no database, no table yet, no key -- answers an empty map rather than stopping
anything. A deployment that has both halves boots exactly
as it did before this module existed, including when postgres is down: the
migration fails, the process exits non-zero, the orchestrator restarts it
until the database answers. Demoting that boot into setup mode instead would
take a production pod restarted during a postgres rollout, stop it being the
application, and put an unauthenticated first-start wizard on the port the
Service and Ingress already point at. The ``SELECT 1`` probe still exists, in
``db/base.database_answers``, as the setup wizard's database-step validation --
where a human is waiting for the answer and no traffic is being served.

There are four outcomes, and the wizard answers two of them:

* all three -- the resolved names are exported into this process's
  environment (so ``alembic/env.py`` and the exec'd application read a
  file-configured deployment exactly as they read an env-configured one),
  ``alembic upgrade head`` runs, and the real command is exec'd:
  byte-identical downstream to what the old shell line did;
* a hard secret missing -- no migration, no engine, no database session; the
  setup application is served instead;
* every hard secret present, a document readable, but no usable media server
  in it -- the same setup application, so an operator can add a server or set
  the credential it is missing;
* every hard secret present and no config document at all -- one line naming
  the two paths that were looked at, and a non-zero exit. A deployment that
  holds credentials was configured by somebody, so a missing document is that
  somebody's mistake -- a ConfigMap whose key was renamed is the reachable
  shape -- and not a first start. Serving the wizard there would put an
  unauthenticated credential-collecting form on the port the Service and
  Ingress already point at, over a typo; the restart loop is what the missing
  document caused before this module existed, now with a line saying which two
  paths were empty.
"""

import asyncio
import logging
import os
import subprocess
import sys

import uvicorn

from autoposter.config.loader import config_document_path, read_config_document
from autoposter.config.schema import (
    ENVIRONMENT_SECRET_NAMES_ENV,
    SECRET_NAMES,
    STATE_FILE_NAMES_ENV,
    STORED_SECRET_NAMES_ENV,
    missing_hard_secret_names,
    missing_server_setup,
    resolve_secret_values,
    state_file_secret_names,
)
from autoposter.config.state import state_config_path

logger = logging.getLogger(__name__)

# What is exec'd when the deployment is configured and argv names nothing. The
# image's CMD passes no arguments; docker-compose.yml's `api` service passes
# its uvicorn --reload line, and that is now the only difference between the
# two boots.
DEFAULT_COMMAND = (sys.executable, "-m", "autoposter.main")


def stored_secrets_for_boot(database_url: str) -> dict[str, str]:
    """The secrets table, read once, before anything else exists.

    Answers ``{}`` for every failure there is: no database, a database whose
    migrations have not run (the table does not exist on a first boot), an
    unreadable key, an unreachable host. None of them may stop a boot, because
    the layers beneath the store -- the state file and the environment -- are
    how every deployment that predates this one is configured, and a boot that
    refused over an empty table would break all of them at once.

    A private event loop, because this frame has none: ``main`` below is
    synchronous and runs before uvicorn. ``asyncio.run`` owns and closes the
    loop, so nothing is left behind for the ``os.execv`` that follows. The
    corollary is that this function MUST NOT be called from inside a running
    loop: ``asyncio.run`` raises there, the catch-all below turns that into
    ``{}``, and an in-loop caller would get a silently empty store rather than
    an error. There is no such caller -- ``resolve_secret_values`` takes the
    map as an argument precisely so that there need not be one.

    The whole read is bounded by ``PROBE_TIMEOUT_SECONDS``, the same five
    seconds ``db/base.database_answers`` chose and for the same reason it
    wrote down there: only asyncpg has an implicit connect bound (60 s,
    incidental rather than chosen), no dialect bounds the QUERY, and a host
    that accepts the connection and then stops answering -- a paused VM, a
    failing-over pgbouncer, a DROP rule applied after accept -- is precisely
    the case that would otherwise hang forever. This sits ahead of
    ``is_configured``, the migration and the exec, so "never blocks a boot"
    has to be a bound and not an intention.
    """
    if not database_url:
        return {}

    # Imported here rather than at module scope, like the engine below: a boot
    # with no database URL answers without importing the database layer at all.
    from autoposter.db.base import PROBE_TIMEOUT_SECONDS

    async def read() -> dict[str, str]:
        from autoposter.config.secret_store import load_stored_secrets
        from autoposter.db.base import make_engine, make_session_factory

        engine = make_engine(database_url)
        try:
            factory = make_session_factory(engine)
            async with factory() as session:
                return await load_stored_secrets(session)
        finally:
            await engine.dispose()

    try:
        return asyncio.run(asyncio.wait_for(read(), PROBE_TIMEOUT_SECONDS))
    except Exception as exc:
        # The CLASS NAME only, never the exception's own message: a connection
        # error's text carries the DSN -- host, user and password. A timeout
        # arrives here as `TimeoutError`, which is a class name like any other.
        # INFO, not WARNING: on a first boot this is the ordinary case.
        logger.info(
            "the stored secrets could not be read (%s); the state file and the "
            "environment answer instead",
            type(exc).__name__,
        )
        return {}


def is_configured(resolved: dict[str, str]) -> bool:
    """Whether this deployment has been told what it is.

    Credentials first and short-circuiting, so a deployment that was never
    told anything does not go looking for a config document either -- and so
    the second branch's message can say "has credentials", which is the fact
    that makes a missing document a mistake rather than a first start.

    False has three shapes and ``main`` treats them differently, which is why
    it re-derives rather than reading this bool alone: a missing hard
    credential is setup mode, a missing document is a non-zero exit, and a
    server-credential problem (no server configured, or a configured server's
    own credential unset) is setup mode too -- the wizard can fix that one.
    The re-derivation is cheap reads over dicts this function does not
    mutate; the alternative is two log sites for one decision.
    """
    missing = missing_hard_secret_names(resolved)
    if missing:
        # Names, never values: these are the variables an operator sets.
        logger.warning("credentials do not resolve; unset: %s", ", ".join(missing))
        return False
    path = config_document_path()
    if path is None:
        # Paths, never contents. The two candidates are named because "no
        # config document" is otherwise indistinguishable from "the wrong one",
        # and this is the line an operator has to read to fix the restart loop
        # the caller is about to enter.
        logger.error(
            "this deployment has credentials but no config document: "
            "nothing at %s and nothing at %s",
            os.environ.get("AUTOPOSTER_CONFIG") or "(AUTOPOSTER_CONFIG unset)",
            state_config_path(),
        )
        return False
    try:
        document = read_config_document(path)
    except Exception:
        # A document that exists but fails to parse is not this function's
        # failure to report -- this gate only ever asked "is there a
        # document", never "is it valid"; that question belongs to the real
        # config load, wherever this deployment's document is loaded as a
        # `Config`. Crashing the boot decision itself over it would be new
        # behaviour this row does not add.
        return True
    problems = missing_server_setup(document, resolved)
    if problems:
        logger.warning("no usable media server: %s", "; ".join(problems))
        return False
    return True


def _export(resolved: dict[str, str]) -> None:
    """Publish the resolved names into this process's environment.

    A plain assignment, not ``setdefault``: ``resolved`` already encodes the
    precedence rule (the stored row, then the state file, then the
    environment), so ``setdefault`` would re-implement it -- and get it wrong
    for the one case that matters, twice over now that the environment is the
    bottom layer rather than the top one. A name
    that is present but EMPTY (``AUTOPOSTER_DATABASE_URL=`` in a copied .env,
    a blanked GitOps secret) counts as ABSENT everywhere else in this row --
    ``resolve_secret_values``, ``missing_hard_secret_names``, ``Secrets.load``
    -- and ``setdefault`` would leave the empty string standing, so alembic
    would refuse to run with a credential this process had just decided it
    had.

    This is what makes the file-configured deployment indistinguishable from
    the env-configured one downstream: ``alembic/env.py`` reads
    ``AUTOPOSTER_DATABASE_URL`` from ``os.environ`` directly, and the exec
    below passes this environment to the new process image.
    """
    for name, value in resolved.items():
        os.environ[name] = value


def _migrate() -> None:
    """``alembic upgrade head``, as a subprocess, exactly as the shell line it
    replaces.

    In-process would import ``alembic/env.py``, which builds its own engine and
    runs its own event loop, and would leave both behind for the exec that
    follows.
    """
    result = subprocess.run(["alembic", "upgrade", "head"], check=False)
    if result.returncode != 0:
        raise SystemExit(result.returncode)


def main(argv: list[str] | None = None) -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    # The clamp main.build() already carries, hoisted ahead of the branch so it
    # covers the setup application too: httpx logs one INFO line per request
    # with the FULL url, and every body the setup application takes is a
    # credential.
    logging.getLogger("httpx").setLevel(logging.WARNING)

    command = list(argv if argv is not None else sys.argv[1:])
    # The database URL itself can never come from the store: reading the store
    # needs it. So the first resolve is the two lower layers, and the second
    # adds the store on top of them.
    resolved = resolve_secret_values()
    stored = stored_secrets_for_boot(resolved.get("AUTOPOSTER_DATABASE_URL", ""))
    if stored:
        resolved = resolve_secret_values(stored)

    # The markers, then the export, and both BEFORE the branch below rather
    # than only on the configured side of it.
    #
    # The markers first, because `_export` is what destroys the answer: it
    # publishes every winning value into `os.environ`, after which every name
    # looks like an environment name and nothing downstream can tell the
    # layers apart. `_export` iterates `resolved` only, so it leaves these
    # three names alone, and `os.execv` below carries the whole environment
    # into the new process image. All three are set unconditionally, including
    # to "": an env-configured boot publishes an explicit empty marker rather
    # than no marker, and each reads as "no name came from there" downstream.
    # Names, never values.
    #
    # The export ahead of the branch, because the WIZARD reads the same
    # resolver this did and has no database session of its own: the store is
    # where a deployment's admin password hash may live, and a wizard that
    # could not see it would treat a deployment that has a password as one
    # that has never had one and hand a setup token to whoever asked first.
    # Publishing here is what makes the wizard's view of this deployment the
    # same as the application's.
    #
    # The environment marker is read off `os.environ` while it is still the
    # environment: `_export` below overwrites the entry for every name a
    # higher layer won, and what it overwrites is exactly what an operator
    # would be sent to change if the running process later had to name the
    # bottom layer. It is the answer to "what did this deployment's manifest
    # set", and after the export nothing can reconstruct it.
    os.environ[STORED_SECRET_NAMES_ENV] = ",".join(
        name for name in SECRET_NAMES if stored.get(name)
    )
    os.environ[STATE_FILE_NAMES_ENV] = ",".join(state_file_secret_names(stored))
    os.environ[ENVIRONMENT_SECRET_NAMES_ENV] = ",".join(
        name for name in SECRET_NAMES if os.environ.get(name)
    )
    _export(resolved)

    if not is_configured(resolved):
        if not missing_hard_secret_names(resolved) and config_document_path() is None:
            # Credentials but no document. `is_configured` has already logged
            # the two paths; exiting non-zero is the restart loop this shape
            # produced before the wizard existed, and it is what keeps an
            # unauthenticated wizard off a configured deployment's port. A
            # server-credential problem (a document that is there, but names
            # no server or one whose credential is unset) is NOT this branch
            # -- the wizard can fix that one, so it falls through below.
            raise SystemExit(1)
        # Imported here rather than at module scope so that a configured boot
        # -- every boot that exists today -- never imports the setup surface
        # at all, and so that a module-level failure anywhere in the
        # application graph cannot take down the wizard that exists to fix it.
        from autoposter.api.setup import build_setup_app

        logger.warning(
            "this deployment is not configured yet; serving the first-start "
            "setup wizard instead of the application"
        )
        # The same reason the configured branch deletes it below, and it bites
        # harder here: uvicorn.run is called FROM this frame and does not return
        # for the whole life of the wizard, so a traceback renderer that prints
        # locals would dump every partially-resolved plaintext credential this
        # deployment does already have. `stored` holds the same plaintext one
        # layer up and goes with it.
        del resolved, stored
        uvicorn.run(
            build_setup_app(), host="0.0.0.0", port=8080, timeout_graceful_shutdown=10
        )
        return

    # The plaintext credentials leave this frame as soon as they are published:
    # any traceback renderer that prints locals (pytest --tb=long, an error
    # reporter added later) would otherwise dump all fourteen of them, and
    # `stored` holds the same plaintext one layer up.
    del resolved, stored
    _migrate()
    if command:
        os.execvp(command[0], command)
        return
    os.execv(DEFAULT_COMMAND[0], list(DEFAULT_COMMAND))


if __name__ == "__main__":
    main()
