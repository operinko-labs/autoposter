"""What alembic revision c1d2e3f4a5b6 WILL merge, without changing anything."""
from dataclasses import dataclass

from autoposter.servers.identity import identity_key


@dataclass(frozen=True)
class Collision:
    key: str
    survivor_id: int
    merged_ids: list[int]


def preview(rows: list[dict]) -> list[Collision]:
    ordered = sorted(rows, key=lambda r: (r["updated_at"], r["id"]), reverse=True)
    survivors: dict[str, int] = {}
    merged: dict[str, list[int]] = {}
    for row in ordered:
        key = identity_key(
            row["kind"], tmdb_id=row["tmdb_id"], tvdb_id=row["tvdb_id"], imdb_id=row["imdb_id"],
            season_number=row["season_number"], episode_number=row["episode_number"],
            file_path=row["file_path"], root_folder=row["root_folder"], legacy=row["rating_key"],
        )
        if key in survivors:
            merged.setdefault(key, []).append(row["id"])
        else:
            survivors[key] = row["id"]
    return [Collision(key=k, survivor_id=survivors[k], merged_ids=sorted(v)) for k, v in merged.items()]
