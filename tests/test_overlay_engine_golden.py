"""The byte-identity gate for the row-97 engine swap.

`badges/compose.py` renders with Pillow, not ImageMagick, so this phase's
parity oracle is the composed image itself rather than an argv list (see the
plan's adjudication A1). Both hashes below were MEASURED on the pre-swap
code at Task 1 and are never recomputed by a later task: re-deriving the
expected value from the code under test is how a byte-identity gate becomes
a tautology.

The hash is taken over the DECODED RGB pixels, not over the encoded WebP
bytes, so a libwebp container-level difference (metadata ordering, say)
cannot fail a run that renders identical pixels.

If this fails after a Pillow or libwebp bump rather than after a code
change, STOP and report -- do not re-record the constants. A font
rasterisation change is a real rendering change and the operator has to
know about it.
"""
import hashlib
import io
from pathlib import Path

import numpy as np
from PIL import Image

from autoposter.badges.compose import BadgeInputs, compose
from autoposter.badges.values import MediaInfo

ORACLE = Path("tests/fixtures/oracle")

# The same two input sets tests/test_badge_parity.py uses, repeated here
# rather than imported: this file must keep measuring what it measured at
# Task 1 even if that file's fixtures are ever re-tuned.
ALL_SOULS = BadgeInputs(
    media=MediaInfo(("1080",), ("English (EAC3 5.1)",), 6, 4845912, ("en",),
                   frozenset(), None, None),
    critic_rating=4.9, audience_rating=6.3, content_rating="17", video_format="WEB",
)
EPISODE = BadgeInputs(
    media=MediaInfo(("480",), ("English (AAC Stereo)",), 2, 1380000, ("en",),
                   frozenset(), 1, 1),
    critic_rating=None, audience_rating=10.0, content_rating=None, video_format="SDTV",
)

# MEASURED at Task 1 Step 4 on the pre-swap code. Do not recompute.
POSTER_PIXELS_SHA = "fc8793c7a67610e47afe6b70d46dfa556e2480f3c6f44739acec8e920e65ce92"
TITLE_CARD_PIXELS_SHA = "e786e74c935fb449505c9a8e00390bb5d1ce2a96a7ced2cfa36e83a84eb9ef81"


def pixels_sha(base: Path, art_kind: str, inputs: BadgeInputs) -> str:
    data = compose(base, art_kind, inputs)
    array = np.asarray(Image.open(io.BytesIO(data)).convert("RGB"), dtype=np.uint8)
    return hashlib.sha256(array.tobytes()).hexdigest()


def test_the_poster_composite_is_byte_identical_to_the_pre_swap_baseline():
    assert pixels_sha(
        ORACLE / "All_Souls_base_no_overlay.jpg", "poster", ALL_SOULS
    ) == POSTER_PIXELS_SHA


def test_the_title_card_composite_is_byte_identical_to_the_pre_swap_baseline():
    assert pixels_sha(
        ORACLE / "8OO10C_S01E01_base_no_overlay.jpg", "title_card", EPISODE
    ) == TITLE_CARD_PIXELS_SHA
