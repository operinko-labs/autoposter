"""server-neutral identity: refs, identity_key, deliveries; drop rating_key

Revision ID: c1d2e3f4a5b6
Revises: b7c4e1a92f30
Create Date: 2026-09-12 00:00:00.000000

"""
import logging
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

from autoposter.migrate_preview import show_provider_ids
from autoposter.servers.identity import identity_key

revision: str = 'c1d2e3f4a5b6'
down_revision: Union[str, Sequence[str], None] = 'b7c4e1a92f30'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# Tables that hang off media_items.id and must follow a merged row. Each
# entry names the columns -- besides item_id -- that make up the table's
# actual unique key, so the dedupe-on-merge below can EXISTS-correlate on
# that real key instead of collapsing every row under item_id alone (which
# would silently drop every other row of a same-item multi-row child, e.g.
# item_metadata_overrides' many fields or item_credits' many people).
DEPENDENTS = (
    ('item_facts', ()),
    ('item_credits', ('kind', 'person')),
    ('item_metadata_overrides', ('field',)),
    ('action_dismissals', ('art_kind',)),
)


def upgrade() -> None:
    """Spec §4.7, in order; one transaction; a failure rolls the whole revision back."""
    conn = op.get_bind()
    # 1. refs, backfilled from the column about to go.
    op.create_table(
        'media_item_server_refs',
        sa.Column('id', sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column('item_id', sa.BigInteger(), sa.ForeignKey('media_items.id', ondelete='CASCADE'), nullable=False),
        sa.Column('server', sa.String(length=16), nullable=False),
        sa.Column('native_id', sa.String(length=128), nullable=False),
        sa.Column('library', sa.String(length=255), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('server', 'native_id', name='uq_server_ref'),
    )
    op.create_index('ix_media_item_server_refs_item_id', 'media_item_server_refs', ['item_id'])
    conn.execute(sa.text(
        "INSERT INTO media_item_server_refs (item_id, server, native_id, library) "
        "SELECT id, 'plex', rating_key, library FROM media_items"
    ))
    # 2. identity_key, computed in Python by the one rule; collisions merged
    #    deterministically (spec §6.5) -- boot runs this, so it never refuses.
    op.add_column('media_items', sa.Column('identity_key', sa.Text(), nullable=True))
    rows = conn.execute(sa.text(
        "SELECT id, parent_id, kind, tmdb_id, tvdb_id, imdb_id, season_number, episode_number, "
        "file_path, root_folder, rating_key FROM media_items ORDER BY updated_at DESC, id DESC"
    )).mappings().all()
    # The whole table is already in hand, so ``show_provider_ids``' walk up
    # parent_id is a dict lookup over THESE rows -- one extra column on the
    # SELECT above rather than a second query, and never a query per row.
    by_id = {row['id']: row for row in rows}
    survivors: dict[str, int] = {}
    updates: list[dict] = []
    for row in rows:
        tmdb_id, tvdb_id, imdb_id = show_provider_ids(row, by_id)
        key = identity_key(
            row['kind'], tmdb_id=tmdb_id, tvdb_id=tvdb_id, imdb_id=imdb_id,
            season_number=row['season_number'], episode_number=row['episode_number'],
            file_path=row['file_path'], root_folder=row['root_folder'],
            # M1: ``rating_key`` is NOT NULL at this revision, but an empty
            # string would reach identity_key's raise; the row's own id is a
            # placeholder that always exists.
            legacy=row['rating_key'] or str(row['id']),
        )
        if key in survivors:
            _merge_into(conn, stale_id=row['id'], survivor_id=survivors[key], key=key)
            continue
        survivors[key] = row['id']
        updates.append({'k': key, 'i': row['id']})
    # The merge decisions above are made row by row in Python and each one
    # touches the database immediately (a later row's decision can depend on
    # an earlier merge's effect). The survivors' own identity_key, by
    # contrast, is independent of every other row, so those writes batch into
    # one executemany rather than one round trip per row.
    if updates:
        conn.execute(sa.text("UPDATE media_items SET identity_key = :k WHERE id = :i"), updates)
    op.alter_column('media_items', 'identity_key', nullable=False)
    op.create_index('ix_media_items_identity_key', 'media_items', ['identity_key'], unique=True)
    # 3. deliveries, one plex row per render, from the roll-up column.
    op.create_table(
        'render_deliveries',
        sa.Column('id', sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column('render_id', sa.BigInteger(), sa.ForeignKey('renders.id', ondelete='CASCADE'), nullable=False),
        sa.Column('server', sa.String(length=16), nullable=False),
        sa.Column('status', sa.String(length=24), server_default='pending', nullable=False),
        sa.Column('attempted_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('uploaded_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('next_attempt_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('detail', sa.Text(), nullable=True),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('render_id', 'server', name='uq_delivery_render_server'),
    )
    op.create_index('ix_render_deliveries_render_id', 'render_deliveries', ['render_id'])
    op.create_index('ix_render_deliveries_next_attempt_at', 'render_deliveries', ['next_attempt_at'])
    conn.execute(sa.text(
        "INSERT INTO render_deliveries (render_id, server, status, uploaded_at) "
        "SELECT id, 'plex', upload_status, uploaded_at FROM renders"
    ))
    # 4. the old key goes.
    op.drop_index('ix_media_items_rating_key', table_name='media_items')
    op.drop_column('media_items', 'rating_key')


def _merge_into(conn, *, stale_id: int, survivor_id: int, key: str) -> None:
    logging.getLogger('alembic.runtime.migration').warning(
        'identity collision on %s: merging media_items %d into %d', key, stale_id, survivor_id
    )
    p = {'s': survivor_id, 'o': stale_id}
    conn.execute(sa.text("UPDATE media_item_server_refs SET item_id = :s WHERE item_id = :o"), p)
    conn.execute(sa.text(
        "DELETE FROM renders WHERE item_id = :o AND art_kind IN "
        "(SELECT art_kind FROM renders WHERE item_id = :s)"), p)
    conn.execute(sa.text("UPDATE renders SET item_id = :s WHERE item_id = :o"), p)
    for table, unique_cols in DEPENDENTS:
        key_match = ''.join(f" AND s.{col} = o.{col}" for col in unique_cols)
        conn.execute(sa.text(
            f"DELETE FROM {table} o WHERE o.item_id = :o AND EXISTS "
            f"(SELECT 1 FROM {table} s WHERE s.item_id = :s{key_match})"), p)
        conn.execute(sa.text(f"UPDATE {table} SET item_id = :s WHERE item_id = :o"), p)
    conn.execute(sa.text("UPDATE media_items SET parent_id = :s WHERE parent_id = :o"), p)
    conn.execute(sa.text("DELETE FROM media_items WHERE id = :o"), p)


def downgrade() -> None:
    """Back through the Plex ref rows. A Jellyfin-only item has no Plex id to
    go back to and gets a unique placeholder rather than failing the step."""
    conn = op.get_bind()
    op.add_column('media_items', sa.Column('rating_key', sa.String(length=64), nullable=True))
    conn.execute(sa.text(
        "UPDATE media_items m SET rating_key = r.native_id FROM ("
        "  SELECT DISTINCT ON (item_id) item_id, native_id FROM media_item_server_refs "
        "  WHERE server = 'plex' ORDER BY item_id, id) r WHERE r.item_id = m.id"))
    conn.execute(sa.text("UPDATE media_items SET rating_key = 'jf-' || id WHERE rating_key IS NULL"))
    op.alter_column('media_items', 'rating_key', nullable=False)
    op.create_index('ix_media_items_rating_key', 'media_items', ['rating_key'], unique=True)
    op.drop_index('ix_render_deliveries_next_attempt_at', table_name='render_deliveries')
    op.drop_index('ix_render_deliveries_render_id', table_name='render_deliveries')
    op.drop_table('render_deliveries')
    op.drop_index('ix_media_item_server_refs_item_id', table_name='media_item_server_refs')
    op.drop_table('media_item_server_refs')
    op.drop_index('ix_media_items_identity_key', table_name='media_items')
    op.drop_column('media_items', 'identity_key')
