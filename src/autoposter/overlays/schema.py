"""The declarative overlay definition -- row 97's engine, replacing
`badges/spec.py`'s hardcoded table.

The attribute set is Kometa's, banked in `.superpowers/sdd/p-overlay-grammar-probe.md`
section 1.1 (independently corroborated by that document's section 5.5, which
is Kometa's own author-list of every key the `overlay:` block accepts). Every
validator below cites the probe SECTION that banks it. Attributes the probe
banks and this phase deliberately does not build are listed in the plan's
"Banked but deliberately deferred" table and are REFUSED here rather than
accepted-and-ignored: an operator who writes `queue:` has to be told it does
nothing, because a silently dropped attribute looks exactly like a working one.
"""
from typing import Literal

from pydantic import BaseModel, Field, model_validator

# Probe section 1.1, special overlay-name forms. Both are banked, neither is
# built this phase.
DEFERRED_NAMES = ("backdrop",)
DEFERRED_NAME_PREFIXES = ("blur",)

_COLOR_FIELDS = ("back_color", "back_line_color", "font_color", "stroke_color")


def _as_rgba(value: str) -> tuple[int, int, int, int]:
    """Probe section 1.1: every colour goes through Pillow's own parser.

    Imported here rather than at module scope. T3 Step 8 makes
    `config/schema.py` import `OverlayDefinition` to type
    `BadgesConfig.definitions`, and `actions/flags.py` imports
    `config.schema` on every Action Center queue/summary request --
    `flags.py`'s own module docstring exists precisely to keep heavy imports
    (plexapi, httpx, the badge compositor) off that path. A module-level
    `from PIL import ImageColor` here would undo that: Pillow would become a
    transitive import of every Action Center request. Deferred to call time,
    it is only paid when a colour is actually validated or rendered.
    """
    from PIL import ImageColor

    return ImageColor.getcolor(value, "RGBA")


class OverlayDefinition(BaseModel):
    """One overlay, as an operator writes it."""

    model_config = {"extra": "forbid", "frozen": True}

    name: str = Field(
        description="This overlay's identity, used for grouping, suppression and the name-keyed image fallback.",
    )
    group: str | None = Field(
        default=None,
        description="Overlays sharing a group name compete per item; only the highest weight is drawn.",
    )
    weight: int | None = Field(
        default=None, ge=0,
        description="Rank within this overlay's group; higher wins. Required whenever group is set.",
    )
    suppress_overlays: list[str] = Field(
        default_factory=list,
        description="Names of other overlays dropped from any item this overlay also matches.",
    )
    queue: str | None = Field(
        default=None,
        description="Not supported by this service; see roadmap row 97. Present only so writing it is refused with a real message instead of a generic extra-inputs error.",
    )

    horizontal_offset: int | str | None = Field(
        default=None,
        description="Horizontal distance from the edge horizontal_align names, in pixels or as a percentage of the canvas width.",
    )
    horizontal_align: Literal["left", "center", "right"] | None = Field(
        default=None,
        description="Which horizontal edge horizontal_offset is measured from; center measures from the centreline.",
    )
    vertical_offset: int | str | None = Field(
        default=None,
        description="Vertical distance from the edge vertical_align names, in pixels or as a percentage of the canvas height.",
    )
    vertical_align: Literal["top", "center", "bottom"] | None = Field(
        default=None,
        description="Which vertical edge vertical_offset is measured from; center measures from the centreline.",
    )

    scale_width: int | None = Field(
        default=None,
        description="Resize this overlay's image to this width in pixels before it is drawn.",
    )
    scale_height: int | None = Field(
        default=None,
        description="Resize this overlay's image to this height in pixels before it is drawn.",
    )

    back_color: str | None = Field(
        default=None,
        description="Fill colour of the backdrop drawn behind this overlay's content, as a CSS colour with optional alpha.",
    )
    back_line_color: str | None = Field(
        default=None,
        description="Outline colour of the backdrop, as a CSS colour with optional alpha.",
    )
    back_line_width: int | None = Field(
        default=None, ge=0,
        description="Outline thickness of the backdrop in pixels; defaults to 1 whenever back_line_color is set.",
    )
    back_radius: int = Field(
        default=0, ge=0,
        description="Corner radius of the backdrop in pixels; 0 draws square corners.",
    )
    back_padding: int = Field(
        default=0, ge=0,
        description="Pixels the backdrop extends beyond its box on all four sides.",
    )
    back_align: Literal["left", "right", "center", "top", "bottom"] = Field(
        default="center",
        description="Where this overlay's content sits inside its backdrop box.",
    )
    back_width: int = Field(
        default=-1,
        description="Backdrop width in pixels; -1 sizes the backdrop to its own content.",
    )
    back_height: int = Field(
        default=-1,
        description="Backdrop height in pixels; -1 sizes the backdrop to its own content.",
    )

    file: str | None = Field(
        default=None,
        description="A path beneath overlays_root naming this overlay's image.",
    )
    builtin: str | None = Field(
        default=None,
        description="A path beneath this service's bundled overlay image tree, without the .png suffix.",
    )
    url: str | None = Field(
        default=None,
        description="An http or https address this overlay's PNG is downloaded from and cached by URL.",
    )

    # Kometa itself has no `text` attribute (probe section 2.1): the literal
    # lives in the overlay's `name`, as `text(LITERAL)`, and
    # `variables.literal_of` parses it out of `name`. This field is this
    # schema's own addition and is not yet reconciled against that form --
    # both are currently accepted with no validator relating them. T2, which
    # builds the text renderer, must pick one as authoritative.
    text: str | None = Field(
        default=None,
        description="The literal this overlay draws, in which <<variable>> tokens are replaced by the item's own values.",
    )
    font: str | None = Field(
        default=None,
        description="A path beneath fonts_root naming the TrueType face this overlay's text is drawn in.",
    )
    font_size: int = Field(
        default=36, gt=0,
        description="Point size this overlay's text is drawn at.",
    )
    font_color: str = Field(
        default="#FFFFFF",
        description="Fill colour of this overlay's text, as a CSS colour with optional alpha.",
    )
    stroke_width: int = Field(
        default=0, ge=0,
        description="Outline thickness in pixels drawn around each glyph of this overlay's text.",
    )
    stroke_color: str | None = Field(
        default=None,
        description="Outline colour drawn around each glyph, as a CSS colour with optional alpha.",
    )
    addon_offset: int = Field(
        default=0, ge=0,
        description="Gap in pixels between this overlay's image and its text when it carries both.",
    )
    addon_position: Literal["left", "right", "top", "bottom"] = Field(
        default="left",
        description="Which side of this overlay's text its image sits on when it carries both.",
    )

    @property
    def has_back(self) -> bool:
        """Probe section 1.1: `has_back = bool(back_color or back_line_color)`.

        Derived rather than authored, so an overlay cannot claim a backdrop it
        gave no colour for -- or lose one it did.
        """
        return bool(self.back_color or self.back_line_color)

    def rgba(self, field: str) -> tuple[int, int, int, int] | None:
        """One colour field as RGBA, or None when it was not set."""
        value = getattr(self, field)
        return _as_rgba(value) if value else None

    @model_validator(mode="after")
    def _validate(self) -> "OverlayDefinition":
        name = self.name.strip()
        if not name:
            raise ValueError("an overlay must have a non-blank 'name'")
        if "|" in name:
            raise ValueError("'|' is a reserved separator and cannot appear in an overlay name")
        # Probe section 1.1, special name forms. Deferred, so refused loudly.
        if name in DEFERRED_NAMES or name.startswith(DEFERRED_NAME_PREFIXES):
            raise ValueError(
                f"the {name!r} overlay form is not supported by this service; "
                "see roadmap row 97"
            )

        # Probe section 1.1: queue is banked and deliberately not built this
        # phase -- refused with a real message rather than the generic
        # extra-inputs error `extra="forbid"` alone would give an unlisted key.
        if self.queue is not None:
            raise ValueError(
                "the 'queue' attribute is not supported by this service; "
                "see roadmap row 97"
            )

        if self.group and self.weight is None:
            raise ValueError("an overlay with a 'group' must also have a 'weight'")

        # Probe section 1.1, positioning: the offset pair is all-or-nothing.
        offsets = (self.horizontal_offset, self.vertical_offset)
        if (offsets[0] is None) != (offsets[1] is None):
            raise ValueError(
                "'horizontal_offset' and 'vertical_offset' must be given together"
            )

        for axis, offset, align in (
            ("horizontal", self.horizontal_offset, self.horizontal_align),
            ("vertical", self.vertical_offset, self.vertical_align),
        ):
            _validate_offset(axis, offset, align)

        for field in _COLOR_FIELDS:
            value = getattr(self, field)
            if value:
                try:
                    _as_rgba(value)
                except ValueError as exc:
                    raise ValueError(f"{field} {value!r} is not a valid colour") from exc

        # Probe section 1.1: a backdrop with no coordinates is refused.
        if self.has_back and self.horizontal_offset is None:
            raise ValueError(
                "an overlay with a backdrop must also have coordinates"
            )

        # Probe section 1.1: back_line_width defaults to 1 when a line colour
        # is set and no width was given.
        if self.back_line_color and self.back_line_width is None:
            object.__setattr__(self, "back_line_width", 1)

        # Probe section 1.1: back_align is only legal when back_width is also
        # set. Both fields have non-None defaults, so "set" is judged by
        # `model_fields_set` (was the key actually written) rather than by
        # comparing against a sentinel value.
        if "back_align" in self.model_fields_set and "back_width" not in self.model_fields_set:
            raise ValueError("'back_align' requires 'back_width' to also be set")

        named = [f for f in ("file", "builtin", "url") if getattr(self, f)]
        if len(named) > 1:
            raise ValueError(
                "name at most one image source: " + ", ".join(named) + " were all given"
            )
        return self


def _validate_offset(axis: str, offset: int | str | None, align: str | None) -> None:
    """Probe section 3.2's range rules.

    A non-center alignment requires a non-negative offset; center allows a
    signed one, bounded to -50%..50% for the percentage form. There is no
    stated pixel bound under center, and none is invented here.
    """
    if offset is None:
        return
    if isinstance(offset, str):
        if not offset.endswith("%"):
            raise ValueError(f"{axis}_offset {offset!r} is neither an integer nor a percentage")
        try:
            percent = int(offset[:-1])
        except ValueError as exc:
            raise ValueError(f"{axis}_offset {offset!r} is not a valid percentage") from exc
        low, high = (-50, 50) if align == "center" else (0, 100)
        if not low <= percent <= high:
            raise ValueError(
                f"{axis}_offset {offset!r} is outside {low}% to {high}% for "
                f"{align or 'left/top'} alignment"
            )
        return
    if align != "center" and offset < 0:
        raise ValueError(
            f"{axis}_offset {offset} must be >= 0 for {align or 'left/top'} alignment"
        )
