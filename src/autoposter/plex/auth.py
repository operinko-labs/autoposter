"""Plex PIN-based OAuth token acquisition.

Implements the same flow as Posterizarr's WebUI
(``webui/frontend/src/utils/plexAuth.js``): create a PIN, send the user to
authorise it in their browser, then poll until plex.tv attaches a token to
that PIN.

This module never logs or persists the token — it only ever returns it to
the caller. The CLI entrypoint (``python -m autoposter.plex.auth``) is the
sole place that prints it, deliberately, exactly once. A later phase's Web
UI drives this same class over HTTP, so it stays free of CLI concerns (no
``print``, ``input`` or ``sys.exit`` here).
"""

import argparse
import asyncio
import platform
import sys
import urllib.parse
import uuid
from dataclasses import dataclass
from importlib.metadata import PackageNotFoundError
from importlib.metadata import version as _pkg_version

import httpx

_PINS_URL = "https://plex.tv/api/v2/pins"


def _version() -> str:
    """Package version for the X-Plex-Version header.

    Falls back rather than raising: this module is also run as a CLI straight
    from a checkout, where the distribution metadata may not be installed, and a
    cosmetic header is never worth failing token acquisition over.
    """
    try:
        return _pkg_version("autoposter")
    except PackageNotFoundError:
        return "0.0.0+local"



@dataclass(frozen=True)
class PlexPin:
    id: int
    code: str


class PlexAuthError(Exception):
    """plex.tv returned a non-2xx response while creating or polling a PIN."""


class PlexPinAuth:
    """Drives the Plex PIN OAuth flow for one client identifier.

    The identifier is generated once (or supplied by the caller) and stored
    on the instance, then reused for every request this instance makes —
    both ``create_pin`` and ``poll_for_token`` send the identical
    ``X-Plex-Client-Identifier`` header. If the two calls ever used
    different identifiers, plex.tv would return a well-formed response with
    ``authToken`` permanently null, which looks like a hang rather than an
    error — using one object for both requests makes that impossible.
    """

    def __init__(
        self,
        http: httpx.AsyncClient,
        client_identifier: str | None = None,
        device_name: str = "autoposter",
    ):
        self._http = http
        self.client_identifier = client_identifier or str(uuid.uuid4())
        self._device_name = device_name

    def _headers(self) -> dict[str, str]:
        platform_name = platform.system() or "Linux"
        return {
            "Accept": "application/json",
            "X-Plex-Product": "autoposter",
            "X-Plex-Version": _version(),
            "X-Plex-Client-Identifier": self.client_identifier,
            "X-Plex-Device": platform_name,
            "X-Plex-Platform": platform_name,
            "X-Plex-Device-Name": self._device_name,
        }

    async def create_pin(self) -> PlexPin:
        response = await self._http.post(
            f"{_PINS_URL}?strong=true", headers=self._headers()
        )
        if not response.is_success:
            raise PlexAuthError(
                f"plex.tv rejected PIN creation: status {response.status_code}"
            )
        data = response.json()
        return PlexPin(id=data["id"], code=data["code"])

    def auth_url(self, code: str) -> str:
        params = {
            "clientID": self.client_identifier,
            "code": code,
            "context[device][product]": "autoposter",
            "context[device][deviceName]": self._device_name,
        }
        return f"https://app.plex.tv/auth#?{urllib.parse.urlencode(params)}"

    async def poll_for_token(
        self,
        pin_id: int,
        timeout_seconds: float = 300,
        interval_seconds: float = 2,
    ) -> str | None:
        """Poll plex.tv until the PIN carries a token, or time out.

        Returns the token once ``authToken`` is non-null, or ``None`` if
        ``timeout_seconds`` elapses first. A non-2xx response always raises
        ``PlexAuthError`` rather than being treated as "not yet authorised" —
        a rejection and a timeout are different failures and must be
        distinguishable.
        """
        loop = asyncio.get_event_loop()
        deadline = loop.time() + timeout_seconds
        while True:
            response = await self._http.get(
                f"{_PINS_URL}/{pin_id}", headers=self._headers()
            )
            if not response.is_success:
                raise PlexAuthError(
                    f"plex.tv rejected PIN poll: status {response.status_code}"
                )
            token = response.json().get("authToken")
            if token:
                return token
            if loop.time() >= deadline:
                return None
            await asyncio.sleep(interval_seconds)


async def _run_cli() -> int:
    parser = argparse.ArgumentParser(
        description="Obtain a Plex account token via the PIN OAuth flow, for "
        "AUTOPOSTER_PLEX_TOKEN."
    )
    parser.add_argument(
        "--client-identifier",
        help="Reuse an existing client identifier so this run refreshes the "
        "same 'Authorized Devices' entry instead of creating a new one.",
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=300,
        help="Seconds to wait for the browser authorisation step (default: 300).",
    )
    args = parser.parse_args()

    async with httpx.AsyncClient() as http:
        auth = PlexPinAuth(http, client_identifier=args.client_identifier)
        pin = await auth.create_pin()

        print(f"Client identifier: {auth.client_identifier}")
        print()
        print("Open this URL in a browser and sign in to authorise autoposter:")
        print()
        print(f"  {auth.auth_url(pin.code)}")
        print()

        status_interval = 5.0
        elapsed = 0.0
        token = None
        while elapsed < args.timeout:
            step = min(status_interval, args.timeout - elapsed)
            token = await auth.poll_for_token(pin.id, timeout_seconds=step)
            elapsed += step
            if token:
                break
            print(f"Waiting for authorisation... {int(args.timeout - elapsed)}s remaining")

    if not token:
        print("Timed out waiting for authorisation.")
        return 1

    # The token is printed exactly once, here, deliberately. Never log it
    # anywhere else -- not at debug level, not in an error path.
    print()
    print("Token acquired. Store it as AUTOPOSTER_PLEX_TOKEN in your secret manager:")
    print()
    print(f"  {token}")
    print()
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(_run_cli()))
