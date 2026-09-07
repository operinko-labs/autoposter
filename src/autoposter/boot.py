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

CONFIGURED means both halves, in this order:

1. every hard secret resolves -- the environment first, the state file second
   (``config/schema.resolve_secret_values``), with an EMPTY value counting as
   absent on both sides;
2. a config document is readable -- the ``AUTOPOSTER_CONFIG`` path when that
   file exists, the state directory's ``autoposter.yaml`` otherwise
   (``config/loader.config_document_path``).

The database is NOT consulted. A deployment that has both halves boots exactly
as it did before this module existed, including when postgres is down: the
migration fails, the process exits non-zero, the orchestrator restarts it
until the database answers. Demoting that boot into setup mode instead would
take a production pod restarted during a postgres rollout, stop it being the
application, and put an unauthenticated first-start wizard on the port the
Service and Ingress already point at. The ``SELECT 1`` probe still exists, in
``db/base.database_answers``, as the setup wizard's database-step validation --
where a human is waiting for the answer and no traffic is being served.

There are three outcomes, and the wizard is only one of them:

* both halves -- the resolved names are exported into this process's
  environment (so ``alembic/env.py`` and the exec'd application read a
  file-configured deployment exactly as they read an env-configured one),
  ``alembic upgrade head`` runs, and the real command is exec'd:
  byte-identical downstream to what the old shell line did;
* a hard secret missing -- no migration, no engine, no database session; the
  setup application is served instead. This is the ONLY door into setup mode;
* every hard secret present and no config document -- one line naming the two
  paths that were looked at, and a non-zero exit. A deployment that holds
  credentials was configured by somebody, so a missing document is that
  somebody's mistake -- a ConfigMap whose key was renamed is the reachable
  shape -- and not a first start. Serving the wizard there would put an
  unauthenticated credential-collecting form on the port the Service and
  Ingress already point at, over a typo; the restart loop is what the missing
  document caused before this module existed, now with a line saying which two
  paths were empty.
"""

import logging
import os
import subprocess
import sys

import uvicorn

from autoposter.config.loader import config_document_path
from autoposter.config.schema import missing_hard_secret_names, resolve_secret_values
from autoposter.config.state import state_config_path

logger = logging.getLogger(__name__)

# What is exec'd when the deployment is configured and argv names nothing. The
# image's CMD passes no arguments; docker-compose.yml's `api` service passes
# its uvicorn --reload line, and that is now the only difference between the
# two boots.
DEFAULT_COMMAND = (sys.executable, "-m", "autoposter.main")


def is_configured(resolved: dict[str, str]) -> bool:
    """Whether this deployment has been told what it is.

    Credentials first and short-circuiting, so a deployment that was never
    told anything does not go looking for a config document either -- and so
    the second branch's message can say "has credentials", which is the fact
    that makes a missing document a mistake rather than a first start.

    False has two shapes and ``main`` treats them differently, which is why it
    asks ``missing_hard_secret_names`` again rather than reading this bool
    alone: a missing credential is setup mode, a missing document is a
    non-zero exit. The re-ask is a list comprehension over a dict this
    function does not mutate; the alternative is two log sites for one
    decision.
    """
    missing = missing_hard_secret_names(resolved)
    if missing:
        # Names, never values: these are the variables an operator sets.
        logger.warning("credentials do not resolve; unset: %s", ", ".join(missing))
        return False
    if config_document_path() is None:
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
    return True


def _export(resolved: dict[str, str]) -> None:
    """Publish the resolved names into this process's environment.

    A plain assignment, not ``setdefault``: ``resolved`` already encodes the
    precedence rule (environment first, file second), so ``setdefault`` would
    re-implement it -- and get it wrong for the one case that matters. A name
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
    resolved = resolve_secret_values()
    if not is_configured(resolved):
        if not missing_hard_secret_names(resolved):
            # Credentials but no document. `is_configured` has already logged
            # the two paths; exiting non-zero is the restart loop this shape
            # produced before the wizard existed, and it is what keeps an
            # unauthenticated wizard off a configured deployment's port.
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
        # deployment does already have.
        del resolved
        uvicorn.run(
            build_setup_app(), host="0.0.0.0", port=8080, timeout_graceful_shutdown=10
        )
        return

    _export(resolved)
    # The plaintext credentials leave this frame as soon as they are published:
    # any traceback renderer that prints locals (pytest --tb=long, an error
    # reporter added later) would otherwise dump all fourteen of them.
    del resolved
    _migrate()
    if command:
        os.execvp(command[0], command)
        return
    os.execv(DEFAULT_COMMAND[0], list(DEFAULT_COMMAND))


if __name__ == "__main__":
    main()
