"""Kometa's overlay coordinate maths.

Transcribed from ``modules/overlay.py::Overlay.get_coordinates`` in the pinned
v2.4.8 image. Kept as pure functions with no Pillow dependency because this is
where parity bugs hide: a wrong number here is invisible in a rendered image
but obvious in a unit test.
"""


def get_cord(value: int | str, image_value: int, over_value: int, align: str) -> int:
    """Resolve one axis to a pixel coordinate.

    ``image_value`` is the canvas dimension, ``over_value`` the box's own
    dimension on that axis, and ``value`` the configured offset. A percentage
    string resolves against the canvas.
    """
    if isinstance(value, str) and value.endswith("%"):
        value = int(image_value * 0.01 * int(value[:-1]))
    if align in ("right", "bottom"):
        return image_value - over_value - value
    if align == "center":
        return int(image_value / 2) - int(over_value / 2) + value
    return value


def backdrop_box(
    canvas: tuple[int, int],
    box: tuple[int, int],
    h_align: str,
    h_offset: int,
    v_align: str,
    v_offset: int,
    padding: int = 0,
) -> tuple[int, int, int, int]:
    """The badge's backdrop rectangle as ``(x0, y0, x1, y1)``.

    ``padding`` expands the rectangle outwards on all four sides, which is how
    the ratings badge turns its 160x160 content box into a 190x190 backdrop.
    """
    x0 = get_cord(h_offset, canvas[0], box[0], h_align)
    y0 = get_cord(v_offset, canvas[1], box[1], v_align)
    return (x0 - padding, y0 - padding, x0 + box[0] + padding, y0 + box[1] + padding)
