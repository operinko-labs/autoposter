"""What alembic revision c1d2e3f4a5b6 WILL merge, without changing anything."""
from dataclasses import dataclass

from autoposter.servers.identity import identity_key


@dataclass(frozen=True)
class Collision:
    key: str
    survivor_id: int
    merged_ids: list[int]


def show_provider_ids(row, by_id) -> tuple:
    """The provider ids revision c1d2e3f4a5b6 keys ``row`` on.

    A movie or a show is keyed on its own ids. A season or an episode is
    keyed on its SHOW's, reached through ``parent_id`` (episode -> season ->
    show, season -> show), because that is the only source the live rule
    ever sees: the Plex resolver rebinds its match container to the show for
    a season or episode intent (``plex/client.py``) and ``adopt/walk.py``
    mirrors it, so a stored row's own episode-level ids -- which adoption
    wrote before 2026-09-13 -- would key the migrated row differently from
    every pass that follows it, minting a second row for one episode.

    Falls back to the row's OWN ids only when the chain is broken (no
    parent_id, or a parent that is not in ``by_id``): there is nothing
    better to key on, and a broken chain is the pre-Phase-2 shape for a
    season or episode whose show was never adopted. A show that is reached
    and simply carries no ids at all is NOT a broken chain -- its Nones are
    what the live rule would use too, sending the key to the path branch.

    ``by_id`` maps id -> row for the whole table; the caller holds every row
    already, so this costs no query of its own.
    """
    if row["kind"] not in ("season", "episode"):
        return (row["tmdb_id"], row["tvdb_id"], row["imdb_id"])
    current = row
    for _ in range(2):  # episode -> season -> show is the longest chain
        parent = by_id.get(current.get("parent_id")) if current.get("parent_id") is not None else None
        if parent is None:
            break
        current = parent
        if current["kind"] == "show":
            return (current["tmdb_id"], current["tvdb_id"], current["imdb_id"])
    return (row["tmdb_id"], row["tvdb_id"], row["imdb_id"])


def preview(rows: list[dict]) -> list[Collision]:
    by_id = {row["id"]: row for row in rows}
    ordered = sorted(rows, key=lambda r: (r["updated_at"], r["id"]), reverse=True)
    survivors: dict[str, int] = {}
    merged: dict[str, list[int]] = {}
    for row in ordered:
        tmdb_id, tvdb_id, imdb_id = show_provider_ids(row, by_id)
        key = identity_key(
            row["kind"], tmdb_id=tmdb_id, tvdb_id=tvdb_id, imdb_id=imdb_id,
            season_number=row["season_number"], episode_number=row["episode_number"],
            file_path=row["file_path"], root_folder=row["root_folder"],
            # An empty rating_key would reach identity_key's raise; the
            # row's own id is a placeholder that always exists, and is what
            # the migration itself uses.
            legacy=row["rating_key"] or str(row["id"]),
        )
        if key in survivors:
            merged.setdefault(key, []).append(row["id"])
        else:
            survivors[key] = row["id"]
    return [Collision(key=k, survivor_id=survivors[k], merged_ids=sorted(v)) for k, v in merged.items()]
