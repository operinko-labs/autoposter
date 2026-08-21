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
CONTAINER_HOST = "ci-postgres"
TIMEOUT_SECONDS = 180
RETRY_SECONDS = 3


def candidate_hosts() -> list[str]:
    """Hosts to try, most specific first.

    The container name only resolves when this job shares a network with the
    database, which is the containerised-runner case.
    """
    hosts = ["localhost"]
    try:
        socket.getaddrinfo(CONTAINER_HOST, PORT)
    except socket.gaierror:
        return hosts
    return [CONTAINER_HOST, *hosts]


async def _connect(host: str) -> str:
    connection = await asyncpg.connect(
        user=USER, password=PASSWORD, database="postgres",
        host=host, port=PORT, timeout=5,
    )
    try:
        return await connection.fetchval("select version()")
    finally:
        await connection.close()


def export(host: str) -> None:
    credentials = f"{USER}:{PASSWORD}@{host}:{PORT}"
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
        for host in candidate_hosts():
            try:
                version = asyncio.run(_connect(host))
            except Exception as error:  # noqa: BLE001 - any failure means "not yet"
                last_error = f"{host}: {type(error).__name__}: {error}"
                continue
            print(f"connected to {host}:{PORT} on attempt {attempt}")
            print(f"  {version.split(',')[0]}")
            export(host)
            return 0
        print(f"attempt {attempt}: not ready ({last_error})")
        time.sleep(RETRY_SECONDS)

    print(f"PostgreSQL never accepted a TCP connection: {last_error}", file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
