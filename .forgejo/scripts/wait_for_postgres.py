"""Wait until PostgreSQL accepts a real connection, then export its URLs.

Why this is not `pg_isready`: that probes the unix socket inside the
container, while the test suite connects over TCP from the job. During
``initdb`` the official image runs a *temporary* server on the socket only,
so the socket reports ready while TCP is still refused. A CI run waited on
that signal, broke out of its loop on the strength of it, and the very next
check reported "rejecting connections".

So this waits on precisely what the tests use — an asyncpg connection over
TCP, from where the tests will run — and writes the resolved URLs to
``GITHUB_ENV``. Nothing is inferred from a proxy for the real thing.

The host is resolved rather than assumed, because it differs by runner: a
job running inside a container reaches the database by container name on a
shared network, while a job running directly on the host reaches it through
the published port on localhost.
"""

import asyncio
import os
import socket
import sys
import time

import asyncpg

USER = "autoposter"
PASSWORD = "autoposter"
PORT = 5432
# Per-run rather than fixed: the workflow suffixes every Docker resource with
# the run id so concurrent runs on the shared daemon cannot collide. The
# fallbacks keep the script runnable outside the workflow.
CONTAINER_HOST = os.environ.get("PG_CONTAINER", "ci-postgres")
# The published port is ephemeral for the same reason; the workflow resolves
# it with `docker port` and hands it over. Only the localhost route uses it.
HOST_PORT = int(os.environ.get("PG_HOST_PORT") or PORT)
TIMEOUT_SECONDS = 180
RETRY_SECONDS = 3


def candidate_hosts() -> list[tuple[str, int]]:
    """(host, port) pairs to try, most specific first.

    The container name only resolves when this job shares a network with the
    database, which is the containerised-runner case.
    """
    hosts = [("localhost", HOST_PORT)]
    try:
        socket.getaddrinfo(CONTAINER_HOST, PORT)
    except socket.gaierror:
        return hosts
    return [(CONTAINER_HOST, PORT), *hosts]


async def _connect(host: str, port: int) -> str:
    connection = await asyncpg.connect(
        user=USER, password=PASSWORD, database="postgres",
        host=host, port=port, timeout=5,
    )
    try:
        return await connection.fetchval("select version()")
    finally:
        await connection.close()


def export(host: str, port: int) -> None:
    credentials = f"{USER}:{PASSWORD}@{host}:{port}"
    lines = [
        f"AUTOPOSTER_TEST_DATABASE_URL=postgresql+asyncpg://{credentials}/autoposter",
        f"AUTOPOSTER_DATABASE_URL=postgresql+asyncpg://{credentials}/autoposter",
        f"AUTOPOSTER_MAINTENANCE_DATABASE_URL=postgresql://{credentials}/postgres",
    ]
    path = os.environ.get("GITHUB_ENV")
    if not path:
        print("GITHUB_ENV is unset; would have exported:")
        for line in lines:
            print("  " + line)
        return
    with open(path, "a", encoding="utf-8") as handle:
        for line in lines:
            print(line, file=handle)


def main() -> int:
    deadline = time.monotonic() + TIMEOUT_SECONDS
    attempt = 0
    last_error = "no attempt was made"

    while time.monotonic() < deadline:
        attempt += 1
        for host, port in candidate_hosts():
            try:
                version = asyncio.run(_connect(host, port))
            except Exception as error:  # noqa: BLE001 - any failure means "not yet"
                last_error = f"{host}: {type(error).__name__}: {error}"
                continue
            print(f"connected to {host}:{port} on attempt {attempt}")
            print(f"  {version.split(',')[0]}")
            export(host, port)
            return 0
        print(f"attempt {attempt}: not ready ({last_error})")
        time.sleep(RETRY_SECONDS)

    print(f"PostgreSQL never accepted a TCP connection: {last_error}", file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
