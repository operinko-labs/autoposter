"""The database overrides layer: config deltas deep-merged over the YAML.

The mounted ``autoposter.yaml`` is delivered by Flux from git and is read-only
in the pod, so the UI's edits cannot go back into it. They live in the
single-row ``config_overrides`` table instead (``db/models.py``) and are merged
over the file every time a ``Config`` is built. The file keeps owning the
defaults; the database owns the deltas.
"""
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from autoposter.config.loader import build_config, read_config_document
from autoposter.config.schema import Config
from autoposter.db.models import ConfigOverride

# The single-document table's only row.
OVERRIDES_ROW_ID = 1


def _reject_secrets(document: dict, path: str = "") -> None:
    """A ``secrets`` key anywhere in the overrides is a hard error.

    Secrets come from the environment (``Secrets.from_env``) and are never part
    of the config document. Storing one here would write a token into a
    database row and into every config API response that echoes the overrides,
    so this refuses rather than merging it and relying on a redactor further
    down the line to keep it out of sight.
    """
    for key, value in document.items():
        where = f"{path}.{key}" if path else str(key)
        if key == "secrets":
            raise ValueError(
                f"config overrides must not contain secrets (found at {where}); "
                "secrets come from the environment only"
            )
        if isinstance(value, dict):
            _reject_secrets(value, where)


def merge_overrides(base: dict, overrides: dict) -> dict:
    """Deep-merge ``overrides`` over ``base``, returning a new dict.

    Nested mappings merge key by key; everything else -- scalars and lists
    alike -- is replaced wholesale. Lists are replaced rather than concatenated
    because every list in this config is a complete statement of intent
    (``providers.order``, ``plex.excluded_libraries``,
    ``artwork.poster.language_order``): an operator removing a provider from
    the order must get a shorter list, not the same one back.

    Neither argument is mutated.
    """
    _reject_secrets(overrides)
    return _merge(base, overrides)


def _merge(base: dict, overrides: dict) -> dict:
    merged = dict(base)
    for key, value in overrides.items():
        existing = merged.get(key)
        if isinstance(existing, dict) and isinstance(value, dict):
            merged[key] = _merge(existing, value)
        else:
            merged[key] = value
    return merged


async def load_overrides_document(session: AsyncSession) -> dict:
    """The stored overrides document, or ``{}`` when there is no row.

    An empty document is the pre-edit state of every deployment, and merging
    ``{}`` is the identity, so "no overrides yet" costs nothing beyond one
    primary-key lookup and behaves exactly like the file alone.
    """
    row = await session.scalar(
        select(ConfigOverride).where(ConfigOverride.id == OVERRIDES_ROW_ID)
    )
    if row is None or not row.document:
        return {}
    return row.document


async def load_effective_config(path: Path, session: AsyncSession) -> Config:
    """The YAML config with the stored overrides merged over it.

    Validated whole and versioned through the same ``build_config`` as
    ``load_config``, so an overridden artwork setting moves ``config.version``
    exactly as editing the file would, and an overridden scheduler setting
    leaves it alone.
    """
    base = read_config_document(path)
    overrides = await load_overrides_document(session)
    if not overrides:
        return build_config(base)
    return build_config(merge_overrides(base, overrides))
