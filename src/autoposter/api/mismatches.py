"""Where Plex and Radarr/Sonarr disagree about what a folder holds.

A wrong match is the one library fault nothing else in this application can
report. Radarr keeps the right folder under the wrong TMDB id, or Plex's agent
matched the sequel; neither side complains, the arr sync sees no gap (the ids
never collide), and the item surfaces days later as a ``no_art`` failure or a
job parked on "no Plex item" -- symptoms that name everything except the cause.
This endpoint puts both sides of every pair on one screen so an operator can
fix the one that is wrong.

**Pairs are matched by path, never by id.** That is the whole design. Matching
a pair by tmdb -- what ``arr/sync.py`` compares -- would make every matched
pair agree about tmdb by construction, and the disagreement would be invisible.
The path is the independent fact: two records describing the same directory on
disk that name different films. It is exactly the pairing ``sync_section``'s
misassignment guard already uses, through the same ``source_path``/``map_path``
/``norm_path`` helpers, so this view and that guard cannot drift apart.

Three groups come out of it:

* ``mismatched`` -- one folder, both records, at least one id disagreeing.
* ``arr_only`` -- a folder the service manages and Plex has nothing for. At
  series level this is the unmatched-episode-family catcher: every episode
  under such a folder fails to resolve, one job at a time.
* ``plex_only`` -- a Plex item under the configured root that the service has
  never registered. (An item *outside* that root is not something the service
  manages at all, so it is counted in ``unmapped`` rather than reported.)

Everything is computed live, per request: two listings and one walk per Plex
type, which is seconds. There is deliberately no cache and no background job --
this is an on-demand answer to a question an operator asks a few times a year,
and a cached one would be a stale one.

Nothing from the arr listing is echoed wholesale. Each row carries the fields
named below and no others, so an unusual instance answering with credentials in
its payload cannot have them reflected back.
"""
import logging

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request

from autoposter.api.auth import require_session
from autoposter.arr.client import RADARR, SONARR, ArrClient, ArrKind
from autoposter.arr.paths import map_path
from autoposter.arr.sync import norm_path, source_path
from autoposter.db.models import Session as SessionModel
from autoposter.plex.client import SectionItem

logger = logging.getLogger(__name__)

router = APIRouter()

# How many rows the response may carry in total. A remount that changed every
# path makes every item in the library a finding at once, and serialising
# fifteen thousand rows would answer a diagnostic question with an outage. The
# counts are always complete; the lists are what is capped.
ROW_LIMIT = 500

# Which ids each service publishes, as (the key ``parse_guids`` uses on the
# Plex side, the field in the service's own listing). Ordered, so a row's
# ``differing`` list does not depend on dict iteration order. Radarr does not
# expose a tvdb id for a movie, and comparing one would always be vacuous.
ID_FIELDS: dict[str, tuple[tuple[str, str], ...]] = {
    "radarr": (("tmdb", "tmdbId"), ("imdb", "imdbId")),
    "sonarr": (("tvdb", "tvdbId"), ("tmdb", "tmdbId"), ("imdb", "imdbId")),
}

# The item kind each service is about, and the Plex section type holding it.
ITEM_KIND = {"radarr": "movie", "sonarr": "series"}
PLEX_TYPE = {"radarr": "movie", "sonarr": "show"}


def _normalise(agent: str, value) -> str | None:
    """One id, in the one form both sides can be compared in.

    The services answer with integers (``"tmdbId": 438631``) and Plex with the
    tail of a guid (``tmdb://438631``); IMDb ids are strings on both sides but
    not reliably the same case. Absent, empty and zero all mean "no id" -- a
    service's unmatched entry carries ``tmdbId: 0``, and treating that as an id
    would report every unmatched entry as a disagreement.
    """
    if value is None or value == "" or value == 0:
        return None
    text = str(value).strip()
    if not text:
        return None
    if agent == "imdb":
        return text.lower()
    return str(int(text)) if text.isdigit() else text


def _arr_ids(service: str, entry: dict) -> dict[str, str]:
    ids = {}
    for agent, field in ID_FIELDS[service]:
        value = _normalise(agent, entry.get(field))
        if value is not None:
            ids[agent] = value
    return ids


def _plex_ids(service: str, item: SectionItem) -> dict[str, str]:
    ids = {}
    for agent, _ in ID_FIELDS[service]:
        value = _normalise(agent, item.guids.get(agent))
        if value is not None:
            ids[agent] = value
    return ids


def _differing(service: str, arr_ids: dict[str, str], plex_ids: dict[str, str]) -> list[str]:
    """The ids the two records disagree about.

    Only ids *both* sides hold are compared. Plex's agent routinely carries no
    IMDb guid and a freshly added arr entry has no IMDb id yet; reporting that
    as a disagreement would bury the real mismatches under the whole library.
    """
    return [
        agent
        for agent, _ in ID_FIELDS[service]
        if agent in arr_ids and agent in plex_ids and arr_ids[agent] != plex_ids[agent]
    ]


def _row(
    service: str,
    path: str,
    *,
    item: SectionItem | None,
    entry: dict | None,
    arr_ids: dict[str, str],
    plex_ids: dict[str, str],
    differing: list[str],
) -> dict:
    """One row, in the single shape all three groups share.

    The unmatched groups leave the other side's fields null rather than
    carrying a different shape: the page renders one table body for all three,
    and a missing side is exactly what the row is reporting.
    """
    return {
        "service": service,
        "kind": ITEM_KIND[service],
        "path": path,
        "library": item.library if item is not None else None,
        "rating_key": item.rating_key if item is not None else None,
        "plex_title": item.title if item is not None else None,
        "arr_title": entry.get("title") if entry is not None else None,
        "year": (item.year if item is not None else None)
        or (entry.get("year") if entry is not None else None),
        "arr_ids": arr_ids,
        "plex_ids": plex_ids,
        "differing": differing,
    }


async def _compare(service: str, kind: ArrKind, service_cfg, api_key: str, http, plex) -> dict:
    """One service against its Plex sections."""
    client = ArrClient(http, service_cfg.base_url, api_key, kind)
    try:
        entries = await client.listing()
    except httpx.HTTPError as exc:
        # Contained and named: an operator has to be able to tell "Radarr is
        # unreachable" from "this endpoint is broken", and the raw exception
        # would reach the client as a bare 500.
        logger.warning("id-mismatches: %s did not answer", service, exc_info=True)
        raise HTTPException(status_code=502, detail=f"{service} did not answer: {exc}") from exc

    items = await plex.list_items(PLEX_TYPE[service])

    by_path: dict[str, dict] = {}
    for entry in entries:
        path = entry.get("path")
        if path:
            by_path[norm_path(path)] = entry

    mismatched: list[dict] = []
    plex_only: list[dict] = []
    matched: set[str] = set()
    unmapped = 0

    for item in items:
        mapped = map_path(source_path(item, kind), service_cfg.plex_path, service_cfg.arr_path)
        if mapped is None:
            # Under some other root entirely -- another mount, another library.
            # The service does not manage it, so its absence there is not a
            # finding. Counted rather than dropped, so the totals stay honest.
            unmapped += 1
            continue
        mapped = norm_path(mapped)

        entry = by_path.get(mapped)
        if entry is None:
            plex_only.append(
                _row(
                    service, mapped, item=item, entry=None,
                    arr_ids={}, plex_ids=_plex_ids(service, item), differing=[],
                )
            )
            continue

        matched.add(mapped)
        arr_ids = _arr_ids(service, entry)
        plex_ids = _plex_ids(service, item)
        differing = _differing(service, arr_ids, plex_ids)
        if differing:
            mismatched.append(
                _row(
                    service, mapped, item=item, entry=entry,
                    arr_ids=arr_ids, plex_ids=plex_ids, differing=differing,
                )
            )

    arr_only = [
        _row(
            service, path, item=None, entry=entry,
            arr_ids=_arr_ids(service, entry), plex_ids={}, differing=[],
        )
        for path, entry in by_path.items()
        if path not in matched
    ]

    return {
        "mismatched": mismatched,
        "arr_only": arr_only,
        "plex_only": plex_only,
        "unmapped": unmapped,
    }


@router.get("/id-mismatches")
async def id_mismatches(request: Request, _: SessionModel = Depends(require_session)) -> dict:
    """Scan both services against Plex and report every disagreement.

    Slow by nature -- two arr listings and a walk of every configured movie and
    show section, several seconds against a real library. That is why the page
    scans on a click rather than polling.

    A service that is disabled, has no ``base_url`` or has no api key is named
    in ``skipped`` rather than guessed at: an empty listing from a service that
    was never asked would report the entire library as ``plex_only``.
    """
    plex = request.app.state.plex
    http = request.app.state.http
    if plex is None or http is None:
        raise HTTPException(status_code=503, detail="Plex is not connected")

    config = request.app.state.config_holder.current
    secrets = request.app.state.secrets

    groups: dict[str, list[dict]] = {"mismatched": [], "arr_only": [], "plex_only": []}
    skipped: list[str] = []
    unmapped = 0

    for kind, service_cfg, api_key in (
        (RADARR, config.radarr, secrets.radarr_apikey),
        (SONARR, config.sonarr, secrets.sonarr_apikey),
    ):
        if not service_cfg.enabled or not service_cfg.base_url or not api_key:
            skipped.append(kind.name)
            continue
        result = await _compare(kind.name, kind, service_cfg, api_key, http, plex)
        for group in groups:
            groups[group].extend(result[group])
        unmapped += result["unmapped"]

    counts = {group: len(rows) for group, rows in groups.items()}
    total = sum(counts.values())

    # Filled in the order the groups are listed, so a library-sized
    # ``plex_only`` cannot push the handful of real mismatches out of the
    # response -- those are the rows the page exists for.
    remaining = ROW_LIMIT
    capped = {}
    for group in ("mismatched", "arr_only", "plex_only"):
        capped[group] = groups[group][:remaining]
        remaining -= len(capped[group])

    return {
        **capped,
        "counts": counts,
        "total": total,
        "limit": ROW_LIMIT,
        "skipped": skipped,
        "unmapped": unmapped,
    }
