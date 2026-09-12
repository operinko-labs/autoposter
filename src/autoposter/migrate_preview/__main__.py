import asyncio
import os
import sys

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import create_async_engine

from autoposter.migrate_preview import preview


async def main() -> int:
    url = os.environ.get("AUTOPOSTER_DATABASE_URL")
    if not url:
        print("AUTOPOSTER_DATABASE_URL is not set; point it at the database the migration will run against",
              file=sys.stderr)
        return 2
    engine = create_async_engine(url)
    try:
        async with engine.connect() as conn:
            has_old = (await conn.execute(sa.text(
                "SELECT 1 FROM information_schema.columns WHERE table_name='media_items' AND column_name='rating_key'"
            ))).first() is not None
            if not has_old:
                print("media_items.rating_key is already gone; the identity migration has run")
                return 0
            rows = (await conn.execute(sa.text(
                "SELECT id, parent_id, kind, tmdb_id, tvdb_id, imdb_id, season_number, "
                "episode_number, file_path, root_folder, rating_key, title, updated_at "
                "FROM media_items"))).mappings().all()
    finally:
        await engine.dispose()
    collisions = preview([dict(r) for r in rows])
    if not collisions:
        print("no identity collisions")
        return 0
    titles = {r["id"]: r["title"] for r in rows}
    for c in collisions:
        print(f'{c.key}  survivor={c.survivor_id} "{titles[c.survivor_id]}"  merges={",".join(map(str, c.merged_ids))}')
    print(f"{len(collisions)} collision(s); each merges into its survivor at boot")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
