"""The IMDb chart collections, as a builder.

The chart itself is fetched by ``collections/charts.py``, which raises on any
failure rather than returning an empty list. That raise is the whole reason the
engine wraps ``build``: these collections use sync semantics, so an empty result
one layer down would mean "remove every member".

The titles and summaries are not ours to choose. They are Kometa's translation
strings, transcribed in ``docs/research/kometa-collections.md`` §5, and the
chart templates take the lower-cased library type ("movie"/"show").
"""
from pydantic import BaseModel, ConfigDict, field_validator

from autoposter.collections.builders.base import BuilderContext, BuilderResult
from autoposter.collections.charts import CHARTS, fetch_chart

# chart key -> (collection title, Kometa's summary template)
CHART_TITLES: dict[str, tuple[str, str]] = {
    "popular_movies": ("IMDb Popular", "List of IMDb Popular %ss."),
    "top_movies": ("IMDb Top 250", "List of IMDb Top 250 %ss."),
    "lowest_rated": ("IMDb Lowest Rated", "List of IMDb Lowest Rated %ss."),
    "popular_shows": ("IMDb Popular", "List of IMDb Popular %ss."),
    "top_shows": ("IMDb Top 250", "List of IMDb Top 250 %ss."),
}

# library type -> the charts built for it, in the order they are built. IMDb has
# no lowest-rated TV chart, so Show deliberately omits it.
CHARTS_FOR: dict[str, list[str]] = {
    "Movie": ["popular_movies", "top_movies", "lowest_rated"],
    "Show": ["popular_shows", "top_shows"],
}


class ImdbChartParams(BaseModel):
    """``imdb_chart``'s params: which chart.

    Validated against ``charts.CHARTS`` rather than against a copy: a chart key
    this module accepts but the fetcher does not know would be a ``KeyError``
    mid-pass, which the engine would contain and report as a dead source.
    """

    model_config = ConfigDict(extra="forbid")

    chart: str

    @field_validator("chart")
    @classmethod
    def _must_be_a_known_chart(cls, v: str) -> str:
        if v not in CHARTS:
            raise ValueError(
                f"unknown IMDb chart {v!r}: known charts are " + ", ".join(sorted(CHARTS))
            )
        return v


class ImdbChartBuilder:
    """One IMDb chart, in rank order."""

    type_name = "imdb_chart"
    params_model = ImdbChartParams

    async def build(self, ctx: BuilderContext) -> BuilderResult:
        params = ImdbChartParams.model_validate(ctx.config)
        ids = await fetch_chart(ctx.http, params.chart)
        title, summary = CHART_TITLES[params.chart]
        return BuilderResult(
            ids=[("imdb", imdb_id) for imdb_id in ids],
            summary=summary % ctx.library_type.lower(),
            poster_kind="chart",
            poster_key=title,
        )
