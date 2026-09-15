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
2. a config document is readable -- the STORE's document when the store holds
   one (``stored_config_document`` below), and the file otherwise: the
   ``AUTOPOSTER_CONFIG`` path when that file exists, the state directory's
   ``autoposter.yaml`` when it does not
   (``config/loader.config_document_path``). The store first because the
   store is what the application actually runs
   (``config/overrides.load_effective_config``), so a server added from the
   Settings page is a server the next boot knows about and a deployment whose
   ConfigMap has been removed still boots;
3. that document names at least one media server, and every server it names
   has its own credential set (``config/schema.missing_server_setup``) -- a
   plex-only deployment's ``AUTOPOSTER_PLEX_TOKEN``, a jellyfin-only one's
   ``AUTOPOSTER_JELLYFIN_APIKEY``, both if both are configured.

The database's ANSWER is part of the decision; its availability is not. It is
read twice, for the stored secrets (``stored_secrets_for_boot`` below) and for
the stored document (``stored_config_document``), and every failure of either
read -- no database, no table yet, no key, an unreachable or silent host --
answers "nothing stored" rather than stopping anything BY ITSELF: the layers
beneath, the state file and the mounted document, are how every deployment
that predates the store is configured. Each read says which of the two it met,
and the one shape that turns on the difference is a deployment nothing else
configures: there a failed secrets read is an outage wearing a first start's
clothes, and ``main`` exits non-zero for the orchestrator to retry rather than
serving an unauthenticated wizard on the port the Service points at.

A deployment that has both halves on the
volume boots exactly as it did before this module existed, including when
postgres is down: the migration fails, the process exits non-zero, the
orchestrator restarts it until the database answers. Demoting that boot into
setup mode instead would take a production pod restarted during a postgres
rollout, stop it being the application, and put an unauthenticated first-start
wizard on the port the Service and Ingress already point at. The ``SELECT 1``
probe still exists, in ``db/base.database_answers``, as the setup wizard's
database-step validation -- where a human is waiting for the answer and no
traffic is being served.

There are five outcomes, and the wizard answers two of them:

* all three -- the resolved names are exported into this process's
  environment (so ``alembic/env.py`` and the exec'd application read a
  file-configured deployment exactly as they read an env-configured one),
  ``alembic upgrade head`` runs, and the real command is exec'd:
  byte-identical downstream to what the old shell line did;
* a hard secret missing and the store READ FAILED -- one line naming the
  exception class, and a non-zero exit. The credentials may be sitting in a
  table this boot could not reach, and the wizard is not something a passing
  outage may put on a serving port;
* a hard secret missing -- no migration, no engine, no database session; the
  setup application is served instead;
* every hard secret present, a document readable, but no usable media server
  in it -- the same setup application, so an operator can add a server or set
  the credential it is missing;
* every hard secret present and no config document at all -- nothing in the
  store and nothing at either path -- one line naming the two paths that were
  looked at, and a non-zero exit. A deployment that
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
from typing import NamedTuple

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

# PostgreSQL's SQLSTATE for "relation does not exist", and the one database
# answer below that is NOT a failed read: the table is created by the very
# migration this module runs after it has decided, so on a first boot its
# absence says "nothing is stored here" as plainly as an empty table does.
# Written out here rather than imported from `api/setup.py`, which reads the
# same SQLSTATE at the wizard's write path: that module is the setup SURFACE a
# configured boot must never import.
_UNDEFINED_TABLE = "42P01"


def _table_is_missing(exc: BaseException) -> bool:
    """Whether ``exc`` is the database saying the secrets table is not there.

    The driver's own SQLSTATE rather than the exception's text: the message
    names the relation, which is harmless, but matching on it would make this
    a string comparison against a server's locale and version.
    """
    return getattr(getattr(exc, "orig", None), "sqlstate", None) == _UNDEFINED_TABLE


class StoredSecrets(NamedTuple):
    """What one bounded read of the secrets table answered.

    ``values`` is what it holds, and is empty both for a table with no rows
    and for a read that never happened. ``failure`` is what separates those
    two: the exception's CLASS NAME when the read failed, and ``None`` when it
    succeeded. The pair exists because the two states are not the same fact
    about a deployment -- "this deployment stores nothing" is how every
    deployment that predates the store looks, and "this deployment's store
    could not be read" is an outage -- and a caller deciding whether to serve
    an unauthenticated first-start wizard must not confuse them.

    The class name and never the exception itself: a connection error's text
    carries the DSN, and every sentence built from this reaches a log.
    """

    values: dict[str, str]
    failure: str | None


class StoredDocument(NamedTuple):
    """What one bounded read of the configuration store answered.

    ``StoredSecrets`` above, for the other half of the boot decision, and the
    same two states told apart for the same reason: ``document`` is ``None``
    both for a store that holds none and for a read that failed, and
    ``failure`` is the exception's CLASS NAME when it was the second.
    """

    document: dict | None
    failure: str | None


def stored_secrets_for_boot(database_url: str) -> StoredSecrets:
    """The secrets table, read once, before anything else exists.

    Answers no values for every failure there is: no database, a database
    whose migrations have not run (the table does not exist on a first boot),
    an unreadable key, an unreachable host. None of them may stop a boot BY
    THEMSELVES, because the layers beneath the store -- the state file and the
    environment -- are how every deployment that predates this one is
    configured, and a boot that refused over an empty table would break all of
    them at once. What the failure DOES do is tell ``main`` that a deployment
    whose credentials resolve from nowhere else is looking at an outage rather
    than at a first start, which is the one shape that must not be handed the
    wizard.

    A private event loop, because this frame has none: ``main`` below is
    synchronous and runs before uvicorn. ``asyncio.run`` owns and closes the
    loop, so nothing is left behind for the ``os.execv`` that follows. The
    corollary is that this function MUST NOT be called from inside a running
    loop: ``asyncio.run`` raises there, the catch-all below turns that into a
    failed read, and an in-loop caller would get an outage's answer rather
    than an error. There is no such caller -- ``resolve_secret_values`` takes
    the map as an argument precisely so that there need not be one.

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
        # Not a failure: a deployment that names no database has no store to
        # be unable to read, and it is the ordinary first start. Answering
        # otherwise here would turn the shape the wizard exists for into an
        # exit.
        return StoredSecrets({}, None)

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
        values = asyncio.run(asyncio.wait_for(read(), PROBE_TIMEOUT_SECONDS))
    except Exception as exc:
        if _table_is_missing(exc):
            # The ordinary first boot: the database answers perfectly and the
            # migration that creates this table has not run, because it runs
            # after the decision below. Nothing is stored here and nothing
            # failed, so this deployment is still the wizard's to finish.
            logger.info(
                "the stored secrets table does not exist yet; the state file "
                "and the environment answer instead"
            )
            return StoredSecrets({}, None)
        # The CLASS NAME only, never the exception's own message: a connection
        # error's text carries the DSN -- host, user and password. A timeout
        # arrives here as `TimeoutError`, which is a class name like any other.
        # INFO, not WARNING: on a first boot this is the ordinary case.
        logger.info(
            "the stored secrets could not be read (%s); the state file and the "
            "environment answer instead",
            type(exc).__name__,
        )
        return StoredSecrets({}, type(exc).__name__)
    return StoredSecrets(values, None)


def stored_config_document(database_url: str) -> StoredDocument:
    """The configuration document the store holds, and why it holds none.

    No document for every failure and for an empty store, exactly as
    ``stored_secrets_for_boot`` answers no values: on a first boot the table
    does not exist yet, and a deployment configured before the store held the
    document has nothing in it. The caller falls back to the file, which is
    what every deployment that predates this does today -- and ``failure``
    is what lets a caller with nothing to fall back on say which of the two it
    met rather than telling an operator mid-outage that their deployment was
    never configured.

    ``None`` for a store that still holds a DELTA as well -- a row whose
    metadata does not carry the current format. Such a row is a partial
    document that means nothing without the file it was a delta of, so
    answering with it would tell a configured deployment it names no media
    server and demote it into the wizard. ``load_effective_config`` gates on
    the same fact and converts the delta there, in the session it has for it.

    The private loop and the bound are ``stored_secrets_for_boot``'s, for the
    reasons written down there: this frame has none, ``asyncio.run`` leaves
    none behind for the ``os.execv`` that follows, and this read sits ahead of
    the boot decision, the migration and the exec -- so "never blocks a boot"
    has to be a bound rather than an intention. The same corollary holds too:
    it MUST NOT be called from inside a running loop, where ``asyncio.run``
    raises into the catch-all below and an unreadable store would be reported
    as an outage that never happened. ``main.build`` is the one caller that
    may have a loop, and it calls this from a worker thread.
    """
    if not database_url:
        # No database named is no store to fail to read, the way
        # ``stored_secrets_for_boot`` answers the same shape.
        return StoredDocument(None, None)

    # Imported here rather than at module scope, like the engine below: a boot
    # with no database URL answers without importing the database layer at all.
    from autoposter.db.base import PROBE_TIMEOUT_SECONDS

    async def read() -> dict | None:
        from autoposter.config.overrides import STORE_FORMAT, load_store
        from autoposter.db.base import make_engine, make_session_factory

        engine = make_engine(database_url)
        try:
            factory = make_session_factory(engine)
            async with factory() as session:
                document, meta = await load_store(session)
                if not document or meta.get("format") != STORE_FORMAT:
                    return None
                return document
        finally:
            await engine.dispose()

    try:
        document = asyncio.run(asyncio.wait_for(read(), PROBE_TIMEOUT_SECONDS))
    except Exception as exc:
        # The CLASS NAME only, for the reason spelled out above: a connection
        # error's text carries the DSN. INFO, not WARNING: on a first boot
        # this is the ordinary case.
        logger.info(
            "the stored configuration could not be read (%s); the configuration "
            "file answers instead",
            type(exc).__name__,
        )
        return StoredDocument(None, type(exc).__name__)
    if document is None:
        # And the other half of that pair, so that the two states are always
        # told apart in the log. Every refusal downstream of this read -- the
        # boot decision's own error line, and `main.build`'s -- can then say
        # "no document came from the store" without having to guess which of
        # the two it was: the line above it has already said.
        logger.info(
            "the configuration store holds no document; the configuration file "
            "answers instead"
        )
    return StoredDocument(document, None)


def is_configured(resolved: dict[str, str], document: dict | None = None) -> bool:
    """Whether this deployment has been told what it is.

    ``document`` is the STORE's, when there is one. ``None`` means the store
    answered nothing and the file is asked instead, which is what every
    deployment that predates the store gets and what the default keeps for
    every caller that has no store to offer.

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
    if document is None:
        path = config_document_path()
        if path is None:
            # Paths, never contents. The two candidates are named because "no
            # config document" is otherwise indistinguishable from "the wrong
            # one", and this is the line an operator has to read to fix the
            # restart loop the caller is about to enter. The store is named
            # too, and without a DSN: with it asked first, "nothing at either
            # path" is no longer the whole of why there is no document. It is
            # named as "answered none" rather than as "empty", because from
            # here the two are the same fact -- the read's own INFO line, one
            # of the pair `stored_config_document` always logs, is what says
            # whether the store was unreadable or simply holds nothing, and an
            # operator told "empty" during an outage would go and reconfigure
            # a deployment that is already configured.
            logger.error(
                "this deployment has credentials but no config document: "
                "nothing at %s, nothing at %s, and the configuration store "
                "answered none",
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
    secrets_read = stored_secrets_for_boot(resolved.get("AUTOPOSTER_DATABASE_URL", ""))
    stored = secrets_read.values
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
    # into the new process image. Each is present afterwards, including as "":
    # an env-configured boot publishes an explicit empty marker rather than no
    # marker, and an empty one reads as "no name came from there" downstream
    # rather than as "nobody has looked". Names, never values.
    #
    # The export ahead of the branch, because the WIZARD reads the same
    # resolver this did and has no database session of its own: the store is
    # where a deployment's admin password hash may live, and a wizard that
    # could not see it would treat a deployment that has a password as one
    # that has never had one and hand a setup token to whoever asked first.
    # Publishing here is what makes the wizard's view of this deployment the
    # same as the application's.
    #
    # The environment marker is the one that is INHERITED rather than
    # recomputed, and it is computed at all only on a boot that finds none.
    # The store and the file can both be re-read by any later boot; the
    # environment cannot, because `_export` below overwrites its entry for
    # every name a higher layer won -- and `os.execv` hands that environment
    # to the next boot in the chain. The wizard's finish and the restart
    # button both re-enter here that way, so a boot that recomputed this would
    # publish "every name the previous boot resolved, from any layer" and call
    # it the deployment's manifest. The first boot of a container is the only
    # process that still sees the environment the deployment actually set;
    # after that, what it published is the answer, and it does not change
    # while the container lives.
    os.environ[STORED_SECRET_NAMES_ENV] = ",".join(
        name for name in SECRET_NAMES if stored.get(name)
    )
    os.environ[STATE_FILE_NAMES_ENV] = ",".join(state_file_secret_names(stored))
    if ENVIRONMENT_SECRET_NAMES_ENV not in os.environ:
        os.environ[ENVIRONMENT_SECRET_NAMES_ENV] = ",".join(
            name for name in SECRET_NAMES if os.environ.get(name)
        )
    _export(resolved)

    # The store's document, read with the same URL the secrets were: it is
    # what the application will actually run, so it is what "configured" is
    # asked of. No document -- an empty store or an unreadable one -- sends the
    # question back to the mounted file, which is where it has always gone.
    #
    # AHEAD of the credential check and not behind it, even though
    # `is_configured` takes the credentials first and answers on its own when
    # one is missing. That shape is exactly when the WIZARD is served, and the
    # wizard is handed this document below: it has no session of its own, and
    # without the document it would offer the config step to a deployment that
    # already has one and then collect a document the next boot ignores. So a
    # read only the configured path made would be a read the one caller that
    # needs it never gets.
    #
    # The cost of that is one bounded read on a boot that is heading for the
    # wizard anyway -- `PROBE_TIMEOUT_SECONDS`, the same bound the secrets read
    # above already pays, against a database this boot has to name before
    # either read happens at all.
    document = stored_config_document(resolved.get("AUTOPOSTER_DATABASE_URL", "")).document

    if not is_configured(resolved, document):
        if missing_hard_secret_names(resolved) and secrets_read.failure is not None:
            # A credential this deployment may well hold, behind a store this
            # boot could not read: a postgres rollout, a five-second hiccup, a
            # pod restarted mid-failover. The shape spec section 2 makes a
            # goal -- a database URL and a volume and nothing else -- has every
            # other hard name in that table, so an outage looks exactly like a
            # deployment that was never configured, and serving the wizard here
            # would put an unauthenticated credential-collecting form on the
            # port the Service already points at and leave it there: uvicorn.run
            # does not return, so nothing re-checks until a human restarts the
            # pod. Exiting non-zero is the restart loop this shape had before
            # the store existed, and the orchestrator is what retries it.
            #
            # The CLASS NAME, which is all the read is allowed to carry out of
            # a message that would otherwise hold the DSN, and no URL of any
            # kind. The deployment's own credentials are not named either: if
            # the file or the environment answers them this branch is not
            # reached at all, and the line says which it is.
            logger.error(
                "the stored secrets could not be read (%s) and neither the "
                "state file nor the environment supplies this deployment's "
                "credentials; exiting rather than serving the first-start "
                "wizard to a deployment that is only unreachable",
                secrets_read.failure,
            )
            raise SystemExit(1)
        if (
            not missing_hard_secret_names(resolved)
            and document is None
            and config_document_path() is None
        ):
            # Credentials but no document, in neither place. `is_configured`
            # has already logged both paths and the database; exiting non-zero
            # is the restart loop this shape
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
        # `secrets_read.values` is that same dict under a third name.
        del resolved, stored, secrets_read
        # The stored document goes with it, for the reason the export above
        # gives about the stored secrets: the wizard has no database session
        # of its own, and one that could not see the store would offer the
        # config step to a deployment that already has a document -- then
        # write a second one to the volume, which the next boot would not
        # read. It is configuration and not a credential, so it is handed over
        # as a value rather than published into the environment.
        uvicorn.run(
            build_setup_app(document),
            host="0.0.0.0",
            port=8080,
            timeout_graceful_shutdown=10,
        )
        return

    # The plaintext credentials leave this frame as soon as they are published:
    # any traceback renderer that prints locals (pytest --tb=long, an error
    # reporter added later) would otherwise dump all fourteen of them, and
    # `stored` holds the same plaintext one layer up.
    # `secrets_read.values` is that same dict under a third name.
    del resolved, stored, secrets_read
    _migrate()
    if command:
        os.execvp(command[0], command)
        return
    os.execv(DEFAULT_COMMAND[0], list(DEFAULT_COMMAND))


if __name__ == "__main__":
    main()
