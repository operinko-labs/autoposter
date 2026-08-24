"""Pins the plexapi clearlogo surface the logo modes depend on.

Not a test of our code. The logo target is net-new API surface for this project
-- nothing here existed before roadmap rows 71/67 -- and no live Plex backs the
suite, so ``tests/test_artwork_logo.py`` drives a hand-written ``FakeItem``. A
fake encodes the author's *belief* about plexapi, and this project has already
paid three times for the difference between a belief and the library (see
``test_plexapi_contract.py``'s module docstring). These assertions touch the
real plexapi classes, offline, so a rename or a signature change upstream fails
here rather than against the operator's server.

``LogoMixin`` arrived in plexapi 4.16.0 ("Add image tags and movie/show logos",
PR #1462), which is why ``pyproject.toml`` floors plexapi at 4.16 rather than
the 4.15 the rest of the surface needed.
"""

import inspect

import pytest
from plexapi.media import BaseResource, Logo
from plexapi.mixins import LogoMixin
from plexapi.video import Episode, Movie, Season, Show


def test_the_kinds_the_logo_modes_write_to_have_the_logo_surface():
    """The updater and the revert only ever act on movies and shows -- the same
    restriction ``api/candidates.py`` makes -- but seasons and episodes are
    asserted too, because they are what a mis-set ``type`` filter would reach if
    the restriction were ever dropped."""
    for cls in (Movie, Show, Season, Episode):
        assert issubclass(cls, LogoMixin), f"plexapi {cls.__name__} lost LogoMixin"


@pytest.mark.parametrize(
    "name,required",
    [
        # The upload target itself. ``filepath`` is the parameter we pass: the
        # bytes are written to a temporary file exactly as ``upload_artwork``
        # does, because plexapi takes a path, not bytes.
        ("uploadLogo", ["url", "filepath"]),
        # The listing the revert marker is checked against, and the two halves
        # of the unlock/clear path.
        ("logos", []),
        ("lockLogo", []),
        ("unlockLogo", []),
        ("deleteLogo", []),
    ],
)
def test_logo_methods_take_the_parameters_we_pass(name, required):
    method = inspect.getattr_static(LogoMixin, name)
    assert inspect.isfunction(method), f"LogoMixin.{name} is no longer a method"
    params = inspect.signature(method).parameters
    for parameter in required:
        assert parameter in params, f"{name} lost its {parameter!r} parameter"


def test_logo_is_a_property_returning_the_current_clearlogo_path():
    """``has_clearlogo`` reads ``plex_item.logo`` to decide whether an item is
    missing one. It is a *property* computed from the item's ``images``, not a
    parsed attribute like ``thumb``/``art``, so accessing it can trigger
    plexapi's blocking ``_reload()`` -- which is why the read is offloaded to a
    thread. If it ever became a method, ``bool(item.logo)`` would silently be
    True for every item and the updater would upload nothing."""
    attribute = inspect.getattr_static(LogoMixin, "logo")
    assert isinstance(attribute, property), "LogoMixin.logo is no longer a property"
    assert "clearLogo" in inspect.getsource(LogoMixin.logo.fget), (
        "LogoMixin.logo no longer resolves the clearLogo image"
    )


def test_a_logo_listing_entry_carries_a_rating_key_and_a_selected_flag():
    """The revert marker is the ``upload://`` rating key of the logo Plex has
    *selected*, so both attributes are load-bearing: without ``selected`` the
    mode cannot tell which of several logos is showing, and without
    ``ratingKey`` it cannot tell ours from an operator's.

    Read off ``BaseResource`` by name rather than ``Logo.__mro__[1]``: an
    inserted base class upstream would silently move that index onto some other
    class whose ``_loadData`` these assertions might happen to pass against."""
    assert issubclass(Logo, BaseResource), "plexapi Logo no longer derives from BaseResource"
    source = inspect.getsource(BaseResource._loadData)
    assert "self.ratingKey" in source, "plexapi Logo entries lost ratingKey"
    assert "self.selected" in source, "plexapi Logo entries lost selected"


def test_the_lock_pair_edits_the_clearlogo_field_and_nothing_else():
    """``upload_logo`` locks and ``clear_logo`` unlocks, and the only reason
    either is safe is that it edits ``clearLogo.locked`` -- a rename upstream to
    another field's lock would have this project locking the wrong field on
    every item it touches, silently."""
    for name, value in (("lockLogo", "1"), ("unlockLogo", "0")):
        source = inspect.getsource(getattr(LogoMixin, name))
        assert "'clearLogo.locked'" in source, f"plexapi {name} no longer edits clearLogo.locked"
        assert value in source, f"plexapi {name} no longer sets clearLogo.locked to {value}"


def test_delete_targets_the_clearlogo_endpoint():
    """``clear_logo`` really removes the image, unlike the poster reset, and
    that rests on ``deleteLogo`` issuing a DELETE against the item's
    ``/clearLogo`` -- not against its poster or the whole item."""
    source = inspect.getsource(LogoMixin.deleteLogo)
    assert "/clearLogo" in source, "plexapi deleteLogo no longer targets /clearLogo"
    assert "delete" in source, "plexapi deleteLogo no longer issues a DELETE"


def test_an_uploaded_logo_is_keyed_by_the_upload_prefix():
    """``UPLOADED_ARTWORK_PREFIX`` is what separates an image somebody pushed to
    the server from one an agent supplied, and the logo marker relies on the
    same convention the reset mode already does."""
    from autoposter.plex.artwork import UPLOADED_ARTWORK_PREFIX

    assert "upload://" in inspect.getsource(BaseResource.resourceFilepath.fget)
    assert UPLOADED_ARTWORK_PREFIX == "upload://"
