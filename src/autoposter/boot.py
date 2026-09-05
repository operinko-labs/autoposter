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
   (``config/schema.resolve_secret_values``);
2. the database those credentials name answers ``SELECT 1``.

Configured: the resolved names are exported into this process's environment
(so ``alembic/env.py`` and the exec'd application read a file-configured
deployment exactly as they read an env-configured one), ``alembic upgrade
head`` runs, and the real command is exec'd -- byte-identical downstream to
what the old shell line did. Unconfigured: no migration, no engine, no
database session; the setup application is served instead.
"""

import asyncio
import logging
import os
import subprocess
import sys

import uvicorn

from autoposter.config.schema import missing_hard_secret_names, resolve_secret_values
from autoposter.db.base import database_answers

logger = logging.getLogger(__name__)

# What is exec'd when the deployment is configured and argv names nothing. The
# image's CMD passes no arguments; docker-compose.yml's `api` service passes
# its uvicorn --reload line, and that is now the only difference between the
# two boots.
DEFAULT_COMMAND = (sys.executable, "-m", "autoposter.main")


def is_configured(resolved: dict[str, str]) -> bool:
    """Whether this deployment can run the application at all.

    The credential check runs first and short-circuits, so an unconfigured
    deployment never opens a socket to a database it was never told about.
    """
    missing = missing_hard_secret_names(resolved)
    if missing:
        # Names, never values: these are the variables an operator sets.
        logger.info(
            "credentials do not resolve; unset: %s", ", ".join(missing)
        )
        return False
    answered, failure = asyncio.run(database_answers(resolved["AUTOPOSTER_DATABASE_URL"]))
    if not answered:
        # Class name only. A connection error's own text carries the DSN, and
        # this line goes to stdout, which api/logs.py's scrub does not cover.
        logger.warning("the configured database did not answer (%s)", failure)
    return answered


def _export(resolved: dict[str, str]) -> None:
    """Publish the resolved names into this process's environment.

    ``setdefault``, so a value that came from the environment is never
    rewritten by one from the file -- the precedence rule, restated where it
    would otherwise be possible to invert it by accident.

    This is what makes the file-configured deployment indistinguishable from
    the env-configured one downstream: ``alembic/env.py`` reads
    ``AUTOPOSTER_DATABASE_URL`` from ``os.environ`` directly, and the exec
    below passes this environment to the new process image.
    """
    for name, value in resolved.items():
        os.environ.setdefault(name, value)


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
        # Imported here rather than at module scope so that a configured boot
        # -- every boot that exists today -- never imports the setup surface
        # at all, and so that a module-level failure anywhere in the
        # application graph cannot take down the wizard that exists to fix it.
        from autoposter.api.setup import build_setup_app

        logger.warning(
            "no complete set of credentials resolved; serving the first-start "
            "setup wizard instead of the application"
        )
        uvicorn.run(
            build_setup_app(), host="0.0.0.0", port=8080, timeout_graceful_shutdown=10
        )
        return

    _export(resolved)
    _migrate()
    if command:
        os.execvp(command[0], command)
        return
    os.execv(DEFAULT_COMMAND[0], list(DEFAULT_COMMAND))


if __name__ == "__main__":
    main()
