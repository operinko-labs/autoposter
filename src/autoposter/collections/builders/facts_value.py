"""``facts_value``: the items whose stored TMDb facts carry a given value.

The narrowest possible use of the enumeration seam, and the membership half of
every ``origin_country``/``original_language`` family: one collection, one
field, one or more values. An operator who wants exactly "the Finnish films"
writes this directly; a family writes one of these per key.

**Why a LIST builder and not a smart one.** A smart collection's membership is
a Plex filter Plex evaluates live, and Plex has no ``origin_country`` field at
all -- that absence IS roadmap row 189. So there is no query to store on the
collection, and the membership has to be resolved here and applied as a list.
That is the whole of the "third family shape": ``cs_bucket`` is a smart family,
``dynamic`` is a smart family, and this is a list family.

**What that costs, stated rather than discovered.** A list collection's
membership is refreshed when a pass runs, not continuously -- so a film whose
facts arrive after this pass joins its collection on the next one. The drift
sweep is what makes that converge (``scheduler.drift_days``, 500 items a week
by default), and the pack descriptions say so in the operator's own words.
"""
from pydantic import BaseModel, ConfigDict, Field, field_validator

from autoposter.collections.builders.base import (
    BuilderContext,
    BuilderResult,
    require_library_type,
)
from autoposter.collections.facts_enumeration import FACTS_FIELDS, items_with_values

__all__ = ["FactsValueBuilder", "FactsValueParams"]


class FactsValueParams(BaseModel):
    """Which stored field, and which of its values.

    ``extra="forbid"`` so ``value:`` for ``values:`` is an error rather than a
    silently-ignored key, and ``coerce_numbers_to_str`` because a TMDb
    collection id is written as a YAML integer and every value here is matched
    as a string.
    """

    model_config = ConfigDict(extra="forbid", coerce_numbers_to_str=True)

    field: str
    values: list[str] = Field(min_length=1)

    @field_validator("field")
    @classmethod
    def _a_field_this_service_enumerates(cls, value: str) -> str:
        if value not in FACTS_FIELDS:
            raise ValueError(
                "%r is not a facts field this service enumerates. Options: %s. "
                "These are the TMDb fields the facts pipeline stores; a Plex "
                "tag is a `smart_filter` or a `dynamic` family instead -- see "
                "`collections/facts_enumeration.py`'s module docstring"
                % (value, ", ".join(sorted(FACTS_FIELDS)))
            )
        return value


class FactsValueBuilder:
    """One collection, from one facts column."""

    type_name = "facts_value"
    params_model = FactsValueParams

    async def build(self, ctx: BuilderContext) -> BuilderResult:
        params = FactsValueParams.model_validate(ctx.config)
        field = FACTS_FIELDS[params.field]
        require_library_type(
            "the 'facts_value' builder's %r field" % field.name,
            ctx.library_type, field.kinds,
        )
        if ctx.session is None:
            # Raise, never return empty: an empty membership is read one layer
            # down as "make no changes" (``builders/base.py``'s first rule), so
            # a context with no database would silently look like a library
            # with no matching items.
            raise ValueError(
                "the 'facts_value' builder reads this service's own database "
                "and was given no session. Every engine path supplies one; a "
                "direct caller has to pass `session=` on the BuilderContext"
            )
        keys = await items_with_values(
            ctx.session, field, params.values,
            library=ctx.library, library_type=ctx.library_type,
        )
        return BuilderResult(ids=[("plex", key) for key in keys])
