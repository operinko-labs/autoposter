"""The builder interface and its registry.

Every collection this service builds will come from a registered builder, so
three properties matter more than anything the builders themselves do:

- a builder is reachable by its ``type_name`` after nothing more than importing
  the package (the config validator depends on that -- see
  ``test_collection_config.py``);
- two builders can never claim the same ``type_name``, because the loser would
  be silently unreachable and the collections it owns would stop being built;
- ``build`` raises on bad input rather than returning an empty list. Empty is
  the reconciler's "make no changes" signal (``lists.reconcile_list_collection``)
  and containment belongs to the engine, not here.

``plex_id`` is the trivial builder: rating keys straight from the definition's
params, no network at all. It exercises the whole interface, which is why it
lives in ``base.py`` next to the protocol it implements.
"""
import pytest
from pydantic import ValidationError

from autoposter.collections.builders import (
    NAMESPACES,
    REGISTRY,
    Builder,
    BuilderContext,
    BuilderResult,
    register,
)
from autoposter.collections.builders.base import PlexIdBuilder


class _FakeBuilder:
    type_name = "fake_builder_for_tests"

    async def build(self, ctx: BuilderContext) -> BuilderResult:
        return BuilderResult(ids=[("imdb", "tt0000001")], summary="fake")


def _ctx(**params) -> BuilderContext:
    """A context for a builder that needs neither HTTP nor the cache."""
    return BuilderContext(library="Movies", library_type="Movie", config=params)


def test_register_round_trips_a_builder():
    builder = _FakeBuilder()
    register(builder)
    try:
        assert REGISTRY["fake_builder_for_tests"] is builder
    finally:
        del REGISTRY["fake_builder_for_tests"]


def test_a_second_builder_cannot_claim_a_taken_type_name():
    """The loser of a silent overwrite would stop building its collections."""
    with pytest.raises(ValueError, match="plex_id"):
        register(PlexIdBuilder())


def test_importing_the_package_is_enough_to_populate_the_registry():
    """The config validator resolves ``builder:`` through this dict at load
    time, so registration cannot depend on anyone importing a builder module
    first."""
    assert REGISTRY["plex_id"].type_name == "plex_id"


def test_every_registered_builder_satisfies_the_protocol():
    for type_name, builder in REGISTRY.items():
        assert isinstance(builder, Builder), type_name
        assert builder.type_name == type_name


async def test_the_plex_id_builder_returns_the_rating_keys_in_order():
    """Order is the builder's output contract: the resolver preserves it and
    the reconciler writes it as the collection's custom order. Repeats are
    allowed -- deduping is the resolver's job, not the builder's."""
    result = await REGISTRY["plex_id"].build(_ctx(ids=["9", "3", "3", "7"]))

    assert result.ids == [("plex", "9"), ("plex", "3"), ("plex", "3"), ("plex", "7")]
    assert result.summary is None
    assert all(namespace in NAMESPACES for namespace, _ in result.ids)


async def test_the_plex_id_builder_coerces_numeric_rating_keys_to_strings():
    """YAML happily produces ints for `ids: [12345]`; an external id value is
    a string in every namespace."""
    result = await REGISTRY["plex_id"].build(_ctx(ids=[12345]))

    assert result.ids == [("plex", "12345")]


async def test_the_plex_id_builder_refuses_an_empty_id_list():
    """Raising, not returning empty: empty means "make no changes" downstream,
    which would quietly mask a mis-typed definition."""
    with pytest.raises(ValidationError):
        await REGISTRY["plex_id"].build(_ctx(ids=[]))


async def test_the_plex_id_builder_refuses_params_it_does_not_understand():
    """A mis-spelled param must not fall through to a default -- the failure
    the example-config gate exists to catch, one level down."""
    with pytest.raises(ValidationError):
        await REGISTRY["plex_id"].build(_ctx(ids=["1"], id=["2"]))
