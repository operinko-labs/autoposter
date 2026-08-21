"""Common Sense age-bucket derivation.

The filter for a bucket is not a fixed list. It is the bucket's own key --
included only when some library item literally carries that bare rating --
plus every candidate from the bucket's `addons` list that the library
actually carries. Candidates nothing carries are left out, so the same
bucket yields different filters on different libraries.

Verified against production: deriving this way reproduces the live filters
for Movies bucket 17, Shows bucket 14 and the Shows catch-all exactly.
"""
import json
from dataclasses import dataclass
from functools import lru_cache

from autoposter.assets import asset_path

SUMMARY = "%ss that are rated %s according to the Common Sense Rating System."
OTHER_SUMMARY = "%ss that are Unrated, Not Rated or any other uncommon Ratings."


@dataclass(frozen=True)
class Bucket:
    """One age bucket, resolved against a specific library."""

    key: str
    title: str
    summary: str
    values: tuple[str, ...]


@lru_cache(maxsize=1)
def load_table() -> dict:
    """The static bucket/candidate table, generated from Kometa's defaults."""
    with open(asset_path("collections", "content_rating_cs.json"), encoding="utf-8") as handle:
        return json.load(handle)


def derive_buckets(present: set[str], library_type: str) -> list[Bucket]:
    """Resolve every bucket against the ratings this library actually has.

    Buckets that match nothing are still returned with empty ``values``:
    production keeps such collections rather than deleting them, and the
    caller cannot make that decision without knowing they exist.
    """
    table = load_table()
    buckets: list[Bucket] = []
    claimed: set[str] = set()

    for key in table["include"]:
        values = [key] if key in present else []
        values += [a for a in table["addons"][key] if a in present and a != key]
        claimed.update(values)
        buckets.append(
            Bucket(
                key=key,
                title="Age %s+ %ss" % (key, library_type),
                summary=SUMMARY % (library_type, key),
                values=tuple(sorted(values)),
            )
        )

    buckets.append(
        Bucket(
            key="other",
            title="Not Rated %ss" % library_type,
            summary=OTHER_SUMMARY % library_type,
            values=tuple(sorted(present - claimed)),
        )
    )
    return buckets
