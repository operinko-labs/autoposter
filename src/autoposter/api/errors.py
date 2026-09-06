"""Application-wide error handlers.

``validation_error_without_input`` lives here rather than in ``app.py`` so the
first-start setup application can install the same handler without importing
the application it stands in for -- see ``api/setup.py``.
"""

from fastapi.exceptions import RequestValidationError
from starlette.responses import JSONResponse


async def validation_error_without_input(_request, exc: RequestValidationError) -> JSONResponse:
    """FastAPI's request-body 422, minus the operator's own paste.

    The default handler serves ``jsonable_encoder(exc.errors())``, and pydantic
    puts the rejected value in every entry's ``input``. For the ``missing`` arm
    of ``OverridesBody`` that value is the ENTIRE body -- so a document sent
    bare (what a hand-written fetch produces, and the 2026-09-01 incident's own
    shape) came back carrying ``plex.url``'s token, ``notifications.url``'s path
    token and all three ``*.base_url`` values in one response. A self-echo to
    the session that sent it, but one that lands in reverse-proxy logs, a HAR
    export and the frontend's retained ``ApiError.detail``.

    Kept: ``type``, ``loc``, ``msg`` -- ``loc`` and ``msg`` are what
    ``fieldErrors`` (``frontend/src/api/overrides.ts``) renders, and ``type`` is
    what a client would branch on. Dropped: ``input`` (the paste) and ``url``
    (pydantic's docs link, which no caller uses). ``ctx`` is dropped too rather
    than filtered: it carries a raw value for some error types (``ctx.error``
    wraps a ValueError's message), and an allow-list of three keys is a rule
    that stays true as pydantic adds error types.

    Registered once, on the application object, so it covers every endpoint --
    including ones added after this -- rather than each request model
    separately. Logs nothing: the refusal is the operator's own mistake, and
    the value it carries is exactly what must not be written down.
    """
    return JSONResponse(
        status_code=422,
        content={
            "detail": [
                {
                    "type": error["type"],
                    "loc": list(error["loc"]),
                    "msg": error["msg"],
                }
                for error in exc.errors()
            ],
        },
    )
