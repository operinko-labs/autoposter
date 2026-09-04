"""The nine preset timeline playlists, and what ``playlists.presets`` means.

``collections/catalog.py`` is this module's precedent and this is the same
mechanism at a tenth of the size: a table of keys, a pure expansion into
definitions, and a validator one file away that refuses a key the table does
not hold. Two things are deliberately NOT copied from it. There is no
readiness column, because all nine of these sit on ``imdb_list`` and
``mdblist_list`` -- both shipped in roadmap row 95 -- so a readiness field
would carry one value forever and a validator would refuse a state that cannot
occur. And there is no provenance field, because provenance here would be a
URL and no URL reaches a served surface in this codebase; the citation is the
comment table below, which is read by a person, not projected by an endpoint.

**Where these came from.** Kometa's ``defaults/playlist.yml`` at 498b3af, all
nine of it, transcribed by id:

    Arrowverse (Timeline Order)                  imdb   ls566667558
    Marvel Cinematic Universe (Timeline Order)   imdb   ls539646485
    DC Animated Universe (Timeline Order)        imdb   ls566357882
    Pokémon (Timeline Order)                     mdb    46555
    Star Trek (Timeline Order)                   imdb   ls547463722
    Star Wars (Timeline Order)                   imdb   ls501373412
    Star Wars The Clone Wars (Timeline Order)    imdb   ls544963772
    X-Men (Timeline Order)                       imdb   ls567618635
    Dragon Ball (Timeline Order)                 mdb    46559

The two MDBList rows are cited upstream as
``mdblist.com/lists/k0meta/external/<id>``, a FOUR-segment path.
``source_urls._parse_mdblist`` reads segments 1 and 2 of a
``/lists/<user>/<slug>`` URL and would turn that into ``list: k0meta/external``
-- a list that does not exist. ``MdblistListParams`` accepts "either the
numeric list id or the user and list name from its URL", and
``facts/mdblist.py::list_items`` builds ``/lists/{reference}/items`` from
either, so the numeric id is what these rows carry. That is a considered
deviation from the URL as written upstream, not a transcription slip.

**The drift posture (adjudication A6).** These are third-party mutable lists
pinned by id: what they contain is somebody else's to change. The operator's
controls are therefore switch-on (list the key), switch-off (remove it), and
override (write a ``playlists.definitions`` entry with the same title). The
last one is the interesting case and it is resolved ONE way and REPORTED: the
operator's definition wins, the preset expands to nothing, and
``preset_conflicts`` names what was displaced so the pass, the API and the
panel can all say so. Letting both through is not an option -- a playlist's
title alone is its identity (``managed_playlists``' unique key), so two
definitions on one title would flap its members hash forever, which is what
``PlaylistsConfig._titles_must_not_collide`` exists to prevent.
"""
from dataclasses import dataclass

from autoposter.config.schema import PlaylistDefinition

__all__ = [
    "BY_KEY",
    "BY_TITLE",
    "PLAYLIST_PRESETS",
    "PlaylistPreset",
    "playlist_definitions",
    "preset_conflicts",
    "preset_definitions",
    "presets_needing_mdblist",
]


@dataclass(frozen=True)
class PlaylistPreset:
    """One preset: a key an operator writes, and the playlist it stands for.

    ``params`` is stored rather than a built ``PlaylistDefinition`` because
    building one runs ``PlaylistDefinition``'s validators, which import the
    builder registry -- and this table is a module-level constant that must
    import without dragging the registry in behind it.
    """

    key: str
    title: str
    builder: str
    params: dict

    def definition(self) -> PlaylistDefinition:
        """This preset as a definition. ``dict(self.params)`` so a caller that
        mutates the result cannot reach back into the table."""
        return PlaylistDefinition(
            title=self.title, builder=self.builder, params=dict(self.params)
        )


PLAYLIST_PRESETS: tuple[PlaylistPreset, ...] = (
    PlaylistPreset(
        key="arrowverse_timeline",
        title="Arrowverse (Timeline Order)",
        builder="imdb_list",
        params={"list": "ls566667558"},
    ),
    PlaylistPreset(
        key="mcu_timeline",
        title="Marvel Cinematic Universe (Timeline Order)",
        builder="imdb_list",
        params={"list": "ls539646485"},
    ),
    PlaylistPreset(
        key="dc_animated_timeline",
        title="DC Animated Universe (Timeline Order)",
        builder="imdb_list",
        params={"list": "ls566357882"},
    ),
    PlaylistPreset(
        key="pokemon_timeline",
        # Kometa's own spelling, accent and all. A title IS a playlist's
        # identity here (``managed_playlists``' unique key), so an ASCII-fied
        # one would build a SECOND playlist beside a Kometa-made "Pokémon
        # (Timeline Order)" rather than standing where it stands.
        title="Pokémon (Timeline Order)",
        builder="mdblist_list",
        params={"list": "46555"},
    ),
    PlaylistPreset(
        key="star_trek_timeline",
        title="Star Trek (Timeline Order)",
        builder="imdb_list",
        params={"list": "ls547463722"},
    ),
    PlaylistPreset(
        key="star_wars_timeline",
        title="Star Wars (Timeline Order)",
        builder="imdb_list",
        params={"list": "ls501373412"},
    ),
    PlaylistPreset(
        key="clone_wars_timeline",
        title="Star Wars The Clone Wars (Timeline Order)",
        builder="imdb_list",
        params={"list": "ls544963772"},
    ),
    PlaylistPreset(
        key="x_men_timeline",
        title="X-Men (Timeline Order)",
        builder="imdb_list",
        params={"list": "ls567618635"},
    ),
    PlaylistPreset(
        key="dragon_ball_timeline",
        title="Dragon Ball (Timeline Order)",
        builder="mdblist_list",
        params={"list": "46559"},
    ),
)

BY_KEY: dict[str, PlaylistPreset] = {p.key: p for p in PLAYLIST_PRESETS}
BY_TITLE: dict[str, PlaylistPreset] = {p.title: p for p in PLAYLIST_PRESETS}


def _shadowed(config) -> set[str]:
    """The titles an operator definition already builds."""
    return {definition.title for definition in config.playlists.definitions}


def preset_definitions(config) -> list[PlaylistDefinition]:
    """The definitions ``playlists.presets`` stands for. Pure; no I/O.

    In TABLE order, not in the order the operator listed the keys: definition
    order is the order a pass runs them in, and re-ordering two lines of YAML
    is not a change to what a deployment builds.

    A scan of the table rather than a lookup of the operator's list, and that
    is ``catalog.preset_definitions``' reason rather than a style choice: this
    runs during validation of the very config that carries the keys, so a
    lookup that could raise would turn a mis-typed preset into a 500 on a
    settings save. Written this way it cannot -- an unknown key matches no row
    and expands to nothing -- and the refusal that makes it an error at all
    lives in ``PlaylistsConfig._presets_must_be_known``, where it can name the
    key and list the table.
    """
    wanted = set(config.playlists.presets)
    taken = _shadowed(config)
    return [
        preset.definition()
        for preset in PLAYLIST_PRESETS
        if preset.key in wanted and preset.title not in taken
    ]


def preset_conflicts(config) -> list[tuple[str, str]]:
    """``(key, title)`` for every switched-on preset an operator definition
    displaces. Pure; no I/O.

    The report half of A6, and the reason ``preset_definitions`` can drop a row
    without that being a silent overwrite: everything that shows a definition
    can show this list beside it.
    """
    wanted = set(config.playlists.presets)
    taken = _shadowed(config)
    return [
        (preset.key, preset.title)
        for preset in PLAYLIST_PRESETS
        if preset.key in wanted and preset.title in taken
    ]


def presets_needing_mdblist(config) -> list[str]:
    """The switched-on preset keys that cannot build without an MDBList key.

    Two of the nine sit on ``mdblist_list``, and that builder raises
    ``MdblistBuilderRefused`` when the deployment configured no ``mdblist``
    API key -- which the playlists pass contains and reports as "source
    returned no items", a SOURCE diagnosis for a CONFIGURATION mistake. The
    pass uses this list to name the KEY instead. Shadowed presets are left out:
    a preset an operator definition displaced is not built for a different
    reason, and ``preset_conflicts`` already says so.

    Pure; no I/O, and it does NOT ask whether a key is configured -- that is a
    runtime fact about ``sources.mdblist``, which this module has no business
    reading.
    """
    wanted = set(config.playlists.presets)
    taken = _shadowed(config)
    return [
        preset.key
        for preset in PLAYLIST_PRESETS
        if preset.key in wanted
        and preset.title not in taken
        and preset.builder == "mdblist_list"
    ]


def playlist_definitions(config) -> list[PlaylistDefinition]:
    """Every playlist a pass builds: the presets, then the operator's own.

    ``service.library_definitions``' shape, and for its reason -- the
    operator's definitions are APPENDED rather than merged, so an empty
    ``presets:`` list leaves exactly what was configured.

    Every caller that enumerates "what this config builds" must go through
    here, the reconcile loop and the delete sweep alike. A sweep reading
    ``config.playlists.definitions`` directly would call every preset's
    playlist an orphan on the pass after it created it.
    """
    return [*preset_definitions(config), *config.playlists.definitions]
