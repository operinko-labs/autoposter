"""The collections this service builds when nobody has configured anything.

``default_definitions`` is the whole inventory -- the Common Sense age buckets,
the IMDb charts, the Oscars collections -- expressed as builder definitions
rather than as three hardcoded loops. An operator's own ``definitions:`` list is
*additional*: an empty one leaves exactly what shipped, which is what the golden
port fixture pins (``tests/test_builder_port_golden.py``).

Two things the definitions deliberately do not decide:

- **Which years the Oscars collections cover.** The ``imdb_award_years``
  definition carries no year; the builder expands it against the dataset on each
  pass, so the definition list stays still while the collections follow the
  ceremonies.
- **Which library each definition suits.** Charts differ by library type and the
  awards are movies-only, and library *type* is not known until the section is
  in hand -- so it is a parameter here rather than a field on the definitions.
"""
from autoposter.collections.builders.imdb_award import EVENTS
from autoposter.collections.builders.imdb_chart import CHART_TITLES, CHARTS_FOR
from autoposter.config.schema import CollectionDefinition

# The chart inventory as ``{library type: [(collection title, chart key), ...]}``
# -- a view of the builder's own table, so the two cannot drift.
CHART_COLLECTIONS: dict[str, list[tuple[str, str]]] = {
    library_type: [(CHART_TITLES[chart][0], chart) for chart in charts]
    for library_type, charts in CHARTS_FOR.items()
}

# The placeholder title of the definition that expands into the year
# collections. It is never a collection: ``engine.definition_titles`` reads the
# builder's title pattern instead.
AWARD_YEARS_TITLE = "Oscars Winners (recent ceremonies)"


def chart_and_award_definitions(config, library_type: str) -> list[CollectionDefinition]:
    """The list-collection half of the inventory, in the order it is built."""
    definitions: list[CollectionDefinition] = []

    if config.collections.charts:
        for chart in CHARTS_FOR.get(library_type, []):
            definitions.append(
                CollectionDefinition(
                    title=CHART_TITLES[chart][0],
                    builder="imdb_chart",
                    params={"chart": chart},
                )
            )

    if config.collections.awards and library_type == "Movie":
        # Oscars only, deliberately: the other ceremonies the builder now knows
        # are opt-in operator definitions. Presets are opinions, and this one
        # shipped -- adding a ceremony here would create collections in every
        # deployment that had never asked for them.
        oscars = EVENTS["oscars"]
        for award, (title, _, _, _) in oscars.awards.items():
            definitions.append(
                CollectionDefinition(
                    title=title, builder="imdb_award", params={"award": award}
                )
            )
        definitions.append(
            CollectionDefinition(
                title=AWARD_YEARS_TITLE, builder=oscars.years_builder
            )
        )

    return definitions


def default_definitions(config, library_type: str) -> list[CollectionDefinition]:
    """Everything this service builds for one library, before operator config.

    Order is the order a pass runs them in, and it is the order the shipped code
    ran: the Common Sense family first, then the charts, then the awards.
    """
    return [
        CollectionDefinition(title="Common Sense age ratings", builder="cs_bucket"),
        *chart_and_award_definitions(config, library_type),
    ]
