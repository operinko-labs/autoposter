# Phase 2b: Badge Overlays and Plex Artwork Upload — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Composite Kometa-parity badge overlays onto the Phase 1 base artwork and upload the result to Plex, re-doing the work only when an input actually changes.

**Architecture:** Phase 1's ImageMagick pipeline keeps producing the full-size base (2000x3000 posters, 3840x2160 title cards) and writing it to `/assets`. Phase 2b adds a second, independent stage in Pillow: downscale that base to Kometa's badge canvas (1000x1500 for posters, 1920x1080 for episodes), paste badge layers onto it, encode WebP, and upload to Plex. A `badge_fingerprint` distinct from the existing base `fingerprint` gates the whole stage, so a rating changing from 8.6 to 8.7 re-badges without re-fetching or re-compositing the base.

**Tech Stack:** Python 3.13, Pillow, plexapi 4.18.2, async SQLAlchemy 2.0 + asyncpg, PostgreSQL 18, Alembic.

## Global Constraints

- **The specification for every number in this plan is `docs/research/kometa-overlays.md`.** It was derived from the pinned production Kometa image (`kometateam/kometa` v2.4.8, digest `sha256:c58f6d4a...`) and verified against two real oracle pairs. Where this plan and that document disagree, the document wins — but they should not disagree; report it if they do.
- **Visual parity, not byte parity.** Phase 1 had to write `/assets` files byte-identical to Posterizarr's. This phase composites a new image from our own base and uploads it, so there is no byte target. Do not attempt byte-identical WebP.
- **Canvas sizes are exact:** posters, season posters and show posters are `1000x1500`; episode title cards are `1920x1080`. From `modules/overlay.py`: `portrait_dim = (1000, 1500)`, `landscape_dim = (1920, 1080)`.
- **Resize is `Image.Resampling.LANCZOS` after `.convert("RGB")`**, matching Kometa exactly.
- **Compositing is `Image.paste(layer, (0, 0), layer)`** — a full-canvas RGBA layer using its own alpha as the mask. It is **not** `Image.alpha_composite`.
- **Badge images are pasted at native size and never scaled.** Some are larger than their backdrop box; centre them and do not clip.
- **Encode:** WebP, `quality=90`, with EXIF tag `0x04BC` set to the string `"overlay"`.
- **Never call `.refresh()` on a Plex object** — it triggers a metadata pull that can overwrite our artwork. A guard test over `src/autoposter/plex/` enforces this. `.reload()` is a different method and is safe.
- **All database timestamps come from `func.now()`**, never `datetime.now()`. Host and container clocks differ by about 10 seconds here.
- **Run tests with `rtk proxy python -m pytest tests/ -v`** — a bare `python -m pytest` is mangled by a shell hook.
- **Commit with `git commit --no-gpg-sign`** — GPG signing times out in this environment.
- **Never `docker compose down -v`.** PostgreSQL 18 runs on `localhost:5433`.
- Baseline on branch start: 387 passed, 5 skipped, `ruff check src tests` clean. Both must stay green.
- The Postgres container clock steps backwards by up to 10 seconds between transactions, so time-sensitive tests flake at roughly 5%. Re-run before concluding a failure is real.
- Badge assets are vendored at `assets/badges/` (512 files, `MANIFEST.sha256` pins the bytes). Do not re-download them.

---

## File Structure

| File | Responsibility |
|---|---|
| `src/autoposter/badges/geometry.py` | Kometa's coordinate formula and the backdrop box maths. Pure functions, no I/O. |
| `src/autoposter/badges/spec.py` | The eight badge definitions as data: alignment, offsets, box sizes, fonts, colours. |
| `src/autoposter/badges/values.py` | Turns a Plex item plus `ItemFacts` into displayed badge values, applying suppression. No drawing. |
| `src/autoposter/badges/draw.py` | Pillow primitives: build a backdrop layer, draw text, paste an image. |
| `src/autoposter/badges/compose.py` | Orchestration: base file to encoded WebP bytes. |
| `src/autoposter/plex/artwork.py` | Upload encoded bytes to Plex and lock the field. |
| `src/autoposter/db/models.py` | `Render` gains `badge_fingerprint`, `uploaded_at`, `upload_status`. |
| `src/autoposter/config/schema.py` | The `badges` config section. |
| `src/autoposter/render/pipeline.py` | Calls the badge stage after the base render. |

Splitting `geometry` / `spec` / `values` / `draw` / `compose` rather than writing one `badges.py` is deliberate: the geometry and value rules are where parity bugs live, and they are far easier to test — and to review — as pure functions with no Pillow or Plex dependency.

---

## Task 1: Coordinate geometry

**Files:**
- Create: `src/autoposter/badges/__init__.py` (empty)
- Create: `src/autoposter/badges/geometry.py`
- Test: `tests/test_badge_geometry.py`

**Interfaces:**
- Produces: `get_cord(value: int | str, image_value: int, over_value: int, align: str) -> int` and `backdrop_box(canvas: tuple[int, int], box: tuple[int, int], h_align: str, h_offset: int, v_align: str, v_offset: int, padding: int = 0) -> tuple[int, int, int, int]` returning `(x0, y0, x1, y1)`.

- [ ] **Step 1: Write the failing test**

Create `tests/test_badge_geometry.py`:

```python
"""Kometa's coordinate formula, checked against measured production output.

Every expected value here was measured from a real overlaid image in
tests/fixtures/oracle/, not derived from the same formula under test.
"""
import pytest

from autoposter.badges.geometry import backdrop_box, get_cord

POSTER = (1000, 1500)
EPISODE = (1920, 1080)


@pytest.mark.parametrize(
    "value,image_value,over_value,align,expected",
    [
        (15, 1000, 305, "left", 15),
        (15, 1500, 105, "top", 15),
        (15, 1920, 600, "right", 1305),
        (30, 1500, 105, "bottom", 1365),
        (0, 1000, 305, "center", 348),
        (0, 1920, 305, "center", 808),
        (-105, 1500, 190, "center", 550),
        (105, 1500, 190, "center", 760),
    ],
)
def test_get_cord_matches_kometa(value, image_value, over_value, align, expected):
    assert get_cord(value, image_value, over_value, align) == expected


def test_percentage_offsets_resolve_against_the_canvas():
    assert get_cord("10%", 1000, 305, "left") == 100


@pytest.mark.parametrize(
    "canvas,box,h_align,h_off,v_align,v_off,padding,expected",
    [
        (POSTER, (305, 105), "left", 15, "top", 15, 0, (15, 15, 320, 120)),
        (POSTER, (305, 105), "left", 15, "bottom", 270, 0, (15, 1125, 320, 1230)),
        (POSTER, (305, 105), "left", 15, "bottom", 30, 0, (15, 1365, 320, 1470)),
        (POSTER, (600, 105), "right", 15, "bottom", 30, 0, (385, 1365, 985, 1470)),
        (POSTER, (160, 160), "right", 30, "center", -105, 15, (795, 550, 985, 740)),
        (POSTER, (160, 160), "right", 30, "center", 105, 15, (795, 760, 985, 950)),
        (EPISODE, (305, 105), "left", 15, "top", 15, 0, (15, 15, 320, 120)),
        (EPISODE, (305, 105), "center", 0, "top", 15, 0, (808, 15, 1113, 120)),
        (EPISODE, (305, 105), "right", 15, "bottom", 150, 0, (1600, 825, 1905, 930)),
        (EPISODE, (305, 105), "left", 15, "bottom", 30, 0, (15, 945, 320, 1050)),
        (EPISODE, (600, 105), "right", 15, "bottom", 30, 0, (1305, 945, 1905, 1050)),
    ],
)
def test_backdrop_box_matches_measured_production_output(
    canvas, box, h_align, h_off, v_align, v_off, padding, expected
):
    assert backdrop_box(canvas, box, h_align, h_off, v_align, v_off, padding) == expected


def test_padding_expands_the_box_on_every_side():
    unpadded = backdrop_box(POSTER, (160, 160), "right", 30, "center", -105, 0)
    padded = backdrop_box(POSTER, (160, 160), "right", 30, "center", -105, 15)
    assert padded == (unpadded[0] - 15, unpadded[1] - 15, unpadded[2] + 15, unpadded[3] + 15)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `rtk proxy python -m pytest tests/test_badge_geometry.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'autoposter.badges'`

- [ ] **Step 3: Write the implementation**

Create `src/autoposter/badges/__init__.py` as an empty file, and `src/autoposter/badges/geometry.py`:

```python
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
```

- [ ] **Step 4: Run test to verify it passes**

Run: `rtk proxy python -m pytest tests/test_badge_geometry.py -v`
Expected: PASS — 21 passed

- [ ] **Step 5: Run ruff and commit**

```bash
rtk proxy ruff check src tests
git add src/autoposter/badges tests/test_badge_geometry.py
git commit --no-gpg-sign -m "Add Kometa badge coordinate geometry"
```

---

## Task 2: Text badge values

Everything a badge displays as text, derived and formatted. Pure functions — no Pillow, no Plex, no database. Suppression is expressed by returning `None`.

**Files:**
- Create: `src/autoposter/badges/values.py`
- Test: `tests/test_badge_values.py`

**Interfaces:**
- Produces: `runtime_text(duration_ms: int | None) -> str | None`, `episode_text(season: int | None, episode: int | None) -> str | None`, `commonsense_text(content_rating: str | None) -> str | None`, `critic_text(rating: float | None) -> str | None`, `audience_text(rating: float | None) -> str | None`.

**Why these exact formats** (traced in `docs/research/kometa-overlays.md` §3.3–3.4):

- Runtime is `"Runtime: {H}h {M}m"` with no zero padding, `H = int((ms/60000)//60)`, `M = int((ms/60000)%60)`.
- Episode is `f"S{season:02}E{episode:02}"`.
- Common Sense appends `"+"` to the bare rating, except `NR` which stays `"NR"`.
- **Critic rating is emitted completely unformatted.** Kometa applies no rounding to this field at all; the familiar one-decimal look comes from Plex storing it that way. Passing `9.0` must print `9.0`, and passing `8.65` must print `8.65` — do not "tidy" it.
- Audience rating is `int(float(v) * 10)` then a literal `%`, so `6.3` becomes `"63%"`. Note this **truncates**: `8.65` becomes `"86%"`, not `"87%"`.

- [ ] **Step 1: Write the failing test**

Create `tests/test_badge_values.py`:

```python
"""Displayed badge strings.

The formats are Kometa's, not ours. Where a format looks wrong (an unrounded
critic rating, a truncating audience percentage) it is deliberate parity --
see docs/research/kometa-overlays.md section 3.3.
"""
import pytest

from autoposter.badges.values import (
    audience_text,
    commonsense_text,
    critic_text,
    episode_text,
    runtime_text,
)


@pytest.mark.parametrize(
    "ms,expected",
    [
        (4845912, "Runtime: 1h 20m"),
        (3600000, "Runtime: 1h 0m"),
        (5400000, "Runtime: 1h 30m"),
        (600000, "Runtime: 0h 10m"),
        (9000000, "Runtime: 2h 30m"),
    ],
)
def test_runtime_text(ms, expected):
    assert runtime_text(ms) == expected


@pytest.mark.parametrize("missing", [None, 0])
def test_runtime_is_suppressed_without_a_duration(missing):
    assert runtime_text(missing) is None


def test_episode_text_zero_pads_both_numbers():
    assert episode_text(1, 1) == "S01E01"
    assert episode_text(12, 7) == "S12E07"
    assert episode_text(2, 145) == "S02E145"


@pytest.mark.parametrize("season,episode", [(None, 1), (1, None), (None, None)])
def test_episode_text_is_suppressed_when_either_number_is_missing(season, episode):
    assert episode_text(season, episode) is None


@pytest.mark.parametrize(
    "rating,expected",
    [("17", "17+"), ("10", "10+"), ("1", "1+"), ("NR", "NR"), ("nr", "NR")],
)
def test_commonsense_text(rating, expected):
    assert commonsense_text(rating) == expected


@pytest.mark.parametrize("bad", [None, "", "   "])
def test_commonsense_is_suppressed_without_a_rating(bad):
    assert commonsense_text(bad) is None


def test_commonsense_rejects_non_numeric_junk():
    """Ten production shows carry the literal string 'tmdb' in contentRating,
    mass-written by an earlier misconfiguration. Rendering a badge reading
    'tmdb+' would be worse than rendering nothing."""
    assert commonsense_text("tmdb") is None
    assert commonsense_text("TV-MA") is None


def test_critic_text_is_completely_unformatted():
    assert critic_text(9.0) == "9.0"
    assert critic_text(4.9) == "4.9"
    assert critic_text(8.65) == "8.65"


def test_audience_text_multiplies_by_ten_and_truncates():
    assert audience_text(6.3) == "63%"
    assert audience_text(8.65) == "86%"
    assert audience_text(10.0) == "100%"


@pytest.mark.parametrize("fn", [critic_text, audience_text])
def test_ratings_are_suppressed_when_absent(fn):
    assert fn(None) is None


@pytest.mark.parametrize("fn", [critic_text, audience_text])
def test_ratings_are_suppressed_when_zero(fn):
    """Plex reports 0.0 for 'no rating', and a badge reading 0.0 or 0% is
    wrong rather than merely ugly."""
    assert fn(0.0) is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `rtk proxy python -m pytest tests/test_badge_values.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'autoposter.badges.values'`

- [ ] **Step 3: Write the implementation**

Create `src/autoposter/badges/values.py`:

```python
"""The strings badges display.

Formats are Kometa's, transcribed from its overlay definitions -- including
the parts that look like bugs. The critic rating really is emitted with no
rounding, and the audience percentage really does truncate rather than round.
Reproducing those exactly is the whole point; "fixing" them here would show up
as every affected badge differing from the tool being replaced.
"""


def runtime_text(duration_ms: int | None) -> str | None:
    """``"Runtime: 1h 20m"`` -- neither number is zero-padded."""
    if not duration_ms:
        return None
    minutes = duration_ms / 60000
    return "Runtime: %dh %dm" % (int(minutes // 60), int(minutes % 60))


def episode_text(season: int | None, episode: int | None) -> str | None:
    """``"S01E01"`` -- both numbers zero-padded to at least two digits."""
    if season is None or episode is None:
        return None
    return "S%02dE%02d" % (season, episode)


def commonsense_text(content_rating: str | None) -> str | None:
    """``"17"`` -> ``"17+"``; ``"NR"`` stays ``"NR"``.

    Anything non-numeric that is not ``NR`` is rejected. That is not
    defensiveness for its own sake: ten shows in this library carry the literal
    string ``"tmdb"`` in ``contentRating``, mass-written by an earlier
    misconfiguration, and a badge reading ``"tmdb+"`` is worse than no badge.
    """
    if not content_rating or not content_rating.strip():
        return None
    value = content_rating.strip()
    if value.upper() == "NR":
        return "NR"
    if not value.isdigit():
        return None
    return value + "+"


def critic_text(rating: float | None) -> str | None:
    """The critic rating, unformatted.

    Kometa applies no rounding to this field, so neither do we -- the usual
    one-decimal appearance comes from Plex storing it that way.
    """
    if not rating:
        return None
    return str(rating)


def audience_text(rating: float | None) -> str | None:
    """The audience rating as a percentage: ``6.3`` -> ``"63%"``.

    Multiply by ten and truncate, which is what Kometa's ``%`` modifier does --
    ``8.65`` becomes ``"86%"``, not ``"87%"``.
    """
    if not rating:
        return None
    return "%d%%" % int(float(rating) * 10)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `rtk proxy python -m pytest tests/test_badge_values.py -v`
Expected: PASS — 27 passed

- [ ] **Step 5: Run ruff and commit**

```bash
rtk proxy ruff check src tests
git add src/autoposter/badges/values.py tests/test_badge_values.py
git commit --no-gpg-sign -m "Add text badge value formatting"
```

---

## Task 3: Media info and image badge selection

Reads the media attributes badges depend on off a Plex item, and picks the PNG filename for the image-based badges.

**Files:**
- Modify: `src/autoposter/badges/values.py`
- Test: `tests/test_badge_media.py`

**Interfaces:**
- Consumes: nothing from earlier tasks.
- Produces: the frozen dataclass `MediaInfo(video_resolution: str | None, audio_codec: str | None, audio_channels: int | None, duration_ms: int | None, audio_languages: tuple[str, ...], hdr_flags: frozenset[str], season_number: int | None, episode_number: int | None)`; `media_info_from_plex(item) -> MediaInfo`; `resolution_image(info: MediaInfo) -> str | None`; `audio_codec_image(info: MediaInfo) -> str | None`; `language_slots(info: MediaInfo, limit: int = 3) -> list[tuple[str, str]]` returning `(flag_filename, label)` pairs.

**Notes for the implementer:**

- `media_info_from_plex` must call `item.reload()` when `item.media` is empty, because stream detail is not present on a search result. **`reload()` is not `refresh()`** — `reload()` re-fetches the object from the API, `refresh()` asks Plex to re-scan metadata from its agents and can overwrite our artwork. A guard test forbids the latter; do not confuse them.
- Resolution filenames combine a base with HDR variants: `{4k,1080p,720p,576p,480p}` plus optional `dv`, `hdr`, `hlg`, `plus` suffixes in that order, e.g. `1080pdvhdr.png`. Plex reports `videoResolution` as `"4k"`, `"1080"`, `"720"`, `"576"`, `"480"`; every value except `4k` gains a `p`.
- HDR detection comes from the video stream: `colorTrc == "smpte2084"` means HDR, `"arib-std-b67"` means HLG, and `DOVIPresent` means Dolby Vision.
- Audio codec filenames live under `images/audio_codec/standard/`. Map Plex's `audioCodec` values: `eac3` -> `plus`, `ac3` -> `digital`, `truehd` -> `truehd`, `dca` -> `dts`, `dca-ma` -> `ma`, `aac` -> `aac`, `flac` -> `flac`, `mp3` -> `mp3`, `opus` -> `opus`, `pcm` -> `pcm`. An unmapped codec returns `None` (badge suppressed) rather than guessing.
- `language_slots` reads `assets/badges/languages.json`, orders by descending `weight`, and returns at most `limit` entries. Load that file once at module import, not per call.

- [ ] **Step 1: Write the failing test**

Create `tests/test_badge_media.py`:

```python
"""Media attributes and image-badge filename selection.

Fakes here mirror the shape of a real plexapi Video object closely enough to
exercise the extraction, but the mapping assertions are what matter: a wrong
filename silently renders the wrong badge.
"""
import pytest

from autoposter.badges.values import (
    MediaInfo,
    audio_codec_image,
    language_slots,
    media_info_from_plex,
    resolution_image,
)


class FakeStream:
    def __init__(self, stream_type, **kw):
        self.streamType = stream_type
        for k, v in kw.items():
            setattr(self, k, v)


class FakePart:
    def __init__(self, streams):
        self.streams = streams


class FakeMedia:
    def __init__(self, parts, **kw):
        self.parts = parts
        for k, v in kw.items():
            setattr(self, k, v)


class FakeItem:
    def __init__(self, media, duration=None, season=None, episode=None):
        self.media = media
        self.duration = duration
        self.seasonNumber = season
        self.episodeNumber = episode
        self.reloaded = False

    def reload(self):
        self.reloaded = True


def _item():
    video = FakeStream(1, codec="hevc", colorTrc="bt709")
    audio = FakeStream(2, codec="eac3", language="English", languageCode="eng", channels=6)
    sub = FakeStream(3, codec="srt", language="Finnish", languageCode="fin")
    media = FakeMedia([FakePart([video, audio, sub])], videoResolution="1080",
                      audioCodec="eac3", audioChannels=6)
    return FakeItem([media], duration=4845912, season=1, episode=1)


def test_media_info_reads_the_attributes_badges_need():
    info = media_info_from_plex(_item())
    assert info.video_resolution == "1080"
    assert info.audio_codec == "eac3"
    assert info.audio_channels == 6
    assert info.duration_ms == 4845912
    assert info.season_number == 1
    assert info.episode_number == 1
    assert info.audio_languages == ("en",)


def test_media_info_reloads_when_media_is_absent():
    """A search result carries no stream detail until reloaded."""
    item = FakeItem([])
    media_info_from_plex(item)
    assert item.reloaded is True


def test_media_info_on_an_item_with_no_media_is_all_empty():
    info = media_info_from_plex(FakeItem([]))
    assert info.video_resolution is None
    assert info.audio_languages == ()


@pytest.mark.parametrize(
    "resolution,flags,expected",
    [
        ("1080", frozenset(), "1080p"),
        ("1080", frozenset({"hdr"}), "1080phdr"),
        ("1080", frozenset({"dv"}), "1080pdv"),
        ("1080", frozenset({"dv", "hdr"}), "1080pdvhdr"),
        ("1080", frozenset({"hlg"}), "1080phlg"),
        ("4k", frozenset(), "4k"),
        ("4k", frozenset({"hdr"}), "4khdr"),
        ("720", frozenset(), "720p"),
        ("480", frozenset(), "480p"),
        ("576", frozenset(), "576p"),
    ],
)
def test_resolution_image_names(resolution, flags, expected):
    info = MediaInfo(resolution, None, None, None, (), flags, None, None)
    assert resolution_image(info) == expected


def test_resolution_image_is_suppressed_for_an_unknown_resolution():
    info = MediaInfo("240", None, None, None, (), frozenset(), None, None)
    assert resolution_image(info) is None


@pytest.mark.parametrize(
    "codec,expected",
    [("eac3", "plus"), ("ac3", "digital"), ("truehd", "truehd"), ("dca", "dts"),
     ("aac", "aac"), ("flac", "flac"), ("opus", "opus")],
)
def test_audio_codec_image_names(codec, expected):
    info = MediaInfo(None, codec, None, None, (), frozenset(), None, None)
    assert audio_codec_image(info) == expected


def test_audio_codec_image_is_suppressed_for_an_unmapped_codec():
    info = MediaInfo(None, "sometkingelse", None, None, (), frozenset(), None, None)
    assert audio_codec_image(info) is None


def test_language_slots_map_to_flags_and_labels():
    info = MediaInfo(None, None, None, None, ("en", "ja"), frozenset(), None, None)
    assert language_slots(info) == [("us", "EN"), ("jp", "JA")]


def test_language_slots_order_by_kometa_weight_not_input_order():
    """English (weight 610) outranks Finnish (280) regardless of track order."""
    info = MediaInfo(None, None, None, None, ("fi", "en"), frozenset(), None, None)
    assert language_slots(info) == [("us", "EN"), ("fi", "FI")]


def test_language_slots_are_capped():
    info = MediaInfo(None, None, None, None, ("en", "de", "fr", "es"), frozenset(), None, None)
    assert len(language_slots(info, limit=3)) == 3


def test_unknown_languages_are_dropped_not_guessed():
    info = MediaInfo(None, None, None, None, ("en", "zzz"), frozenset(), None, None)
    assert language_slots(info) == [("us", "EN")]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `rtk proxy python -m pytest tests/test_badge_media.py -v`
Expected: FAIL with `ImportError: cannot import name 'MediaInfo'`

- [ ] **Step 3: Write the implementation**

Append to `src/autoposter/badges/values.py`:

```python
import json
from dataclasses import dataclass
from pathlib import Path

_ASSETS = Path(__file__).resolve().parents[3] / "assets" / "badges"

with open(_ASSETS / "languages.json", encoding="utf-8") as handle:
    LANGUAGES: dict[str, dict] = json.load(handle)

RESOLUTIONS = {"4k": "4k", "1080": "1080p", "720": "720p", "576": "576p", "480": "480p"}

# Plex's codec identifiers on the left, Kometa's image filenames on the right.
AUDIO_CODECS = {
    "eac3": "plus", "ac3": "digital", "truehd": "truehd", "dca": "dts",
    "dca-ma": "ma", "aac": "aac", "flac": "flac", "mp3": "mp3",
    "opus": "opus", "pcm": "pcm",
}


@dataclass(frozen=True)
class MediaInfo:
    """The media attributes badges are derived from."""

    video_resolution: str | None
    audio_codec: str | None
    audio_channels: int | None
    duration_ms: int | None
    audio_languages: tuple[str, ...]
    hdr_flags: frozenset[str]
    season_number: int | None
    episode_number: int | None


def media_info_from_plex(item) -> MediaInfo:
    """Read badge inputs off a Plex item.

    Calls ``reload()`` when media is absent, because a search result carries no
    stream detail. That is ``reload()``, not ``refresh()`` -- the latter asks
    Plex to re-scan from its metadata agents, which can overwrite the artwork
    this project just uploaded.
    """
    if not getattr(item, "media", None):
        item.reload()
    media = (getattr(item, "media", None) or [None])[0]
    if media is None:
        return MediaInfo(None, None, None, getattr(item, "duration", None), (),
                         frozenset(), getattr(item, "seasonNumber", None),
                         getattr(item, "episodeNumber", None))

    languages: list[str] = []
    flags: set[str] = set()
    for part in getattr(media, "parts", []) or []:
        for stream in getattr(part, "streams", []) or []:
            if stream.streamType == 1:
                if getattr(stream, "DOVIPresent", None):
                    flags.add("dv")
                trc = getattr(stream, "colorTrc", None)
                if trc == "smpte2084":
                    flags.add("hdr")
                elif trc == "arib-std-b67":
                    flags.add("hlg")
            elif stream.streamType == 2:
                code = (getattr(stream, "languageCode", None) or "")[:2].lower()
                if code and code not in languages:
                    languages.append(code)

    return MediaInfo(
        video_resolution=getattr(media, "videoResolution", None),
        audio_codec=getattr(media, "audioCodec", None),
        audio_channels=getattr(media, "audioChannels", None),
        duration_ms=getattr(item, "duration", None),
        audio_languages=tuple(languages),
        hdr_flags=frozenset(flags),
        season_number=getattr(item, "seasonNumber", None),
        episode_number=getattr(item, "episodeNumber", None),
    )


def resolution_image(info: MediaInfo) -> str | None:
    """Filename stem under ``images/resolution/``, e.g. ``1080pdvhdr``.

    Suffixes append in a fixed order; an unknown resolution suppresses the
    badge rather than guessing at a filename that may not exist.
    """
    base = RESOLUTIONS.get((info.video_resolution or "").lower())
    if base is None:
        return None
    for flag in ("dv", "hdr", "hlg", "plus"):
        if flag in info.hdr_flags:
            base += flag
    return base


def audio_codec_image(info: MediaInfo) -> str | None:
    """Filename stem under ``images/audio_codec/standard/``."""
    return AUDIO_CODECS.get((info.audio_codec or "").lower())


def language_slots(info: MediaInfo, limit: int = 3) -> list[tuple[str, str]]:
    """``(flag_stem, label)`` pairs, highest Kometa weight first.

    The flag is a country code, not a language code -- English shows the US
    flag -- so it comes from the mapping table rather than the language itself.
    """
    known = [(code, LANGUAGES[code]) for code in info.audio_languages if code in LANGUAGES]
    known.sort(key=lambda pair: pair[1]["weight"], reverse=True)
    return [(entry["country"], entry["text"]) for _, entry in known[:limit]]
```

Add `import json`, `from dataclasses import dataclass` and `from pathlib import Path` at the top of the file rather than mid-file — the block above shows them for completeness, but the finished module must have a single import section.

- [ ] **Step 4: Run test to verify it passes**

Run: `rtk proxy python -m pytest tests/test_badge_media.py -v`
Expected: PASS — 30 passed

- [ ] **Step 5: Run ruff and commit**

```bash
rtk proxy ruff check src tests
git add src/autoposter/badges/values.py tests/test_badge_media.py
git commit --no-gpg-sign -m "Derive media attributes and image badge filenames"
```

---

## Task 4: Badge definitions

The eight badges as data. No logic here — this file exists so that every parity-critical number sits in one reviewable place instead of being scattered through drawing code.

**Files:**
- Create: `src/autoposter/badges/spec.py`
- Test: `tests/test_badge_spec.py`

**Interfaces:**
- Consumes: nothing.
- Produces: the frozen dataclass `BadgeSpec(name, h_align, h_offset, v_align, v_offset, box, padding, radius, font, font_size, has_back)`; the mapping `BADGES: dict[str, BadgeSpec]`; the constants `POSTER_CANVAS = (1000, 1500)`, `EPISODE_CANVAS = (1920, 1080)`, `BACK_COLOR = (0, 0, 0, 153)`, `FONT_COLOR = (255, 255, 255, 255)`; and `canvas_for(art_kind: str) -> tuple[int, int]`.

**The numbers, and why they are what they are** — all from `docs/research/kometa-overlays.md`:

| Badge | h_align | h_off | v_align | v_off | box | padding | font | size |
|---|---|---|---|---|---|---|---|---|
| `resolution` | left | 15 | top | 15 | 305x105 | 0 | — | — |
| `audio_codec` | center | 0 | top | 15 | 305x105 | 0 | — | — |
| `critic` | right | 30 | center | -105 | 160x160 | 15 | Inter-Bold | 63 |
| `audience` | right | 30 | center | 105 | 160x160 | 15 | Inter-Bold | 63 |
| `commonsense` | left | 15 | bottom | 270 | 305x105 | 0 | Inter-Medium | 55 |
| `video_format` | left | 15 | bottom | 30 | 305x105 | 0 | Inter-Medium | 55 |
| `runtimes` | right | 15 | bottom | 30 | 600x105 | 0 | Inter-Medium | 55 |
| `episode_info` | right | 15 | bottom | 150 | 305x105 | 0 | Inter-Medium | 55 |
| `languages` | left | 15 | top | 223 | 190x105 | 0 | Inter-Bold | 50 |

Two of those vertical offsets look wrong and are not: `commonsense` uses **270**, not 30, and `episode_info` uses **150**, not 30. Both come from a `vertical_align.exists: false` branch in Kometa's YAML that fires despite the file setting `vertical_align: bottom`. Both were confirmed against measured pixels — `episode_info` lands at y=825, exactly `1080 - 105 - 150`. Do not "correct" them.

`languages` has **`has_back = False`**, unlike every other badge. Its YAML sets a visible backdrop, but neither oracle image shows one — the production config applies the `languages` overlay twice, the second time with fully transparent colours, and the observable result is a flag and label with no backdrop. We reproduce what is observably there.

- [ ] **Step 1: Write the failing test**

Create `tests/test_badge_spec.py`:

```python
"""The badge definition table.

These assertions look tautological -- they restate the table. That is the
point: these numbers are the parity contract, and a silent edit to one of them
would otherwise only surface as a subtly misplaced badge in a rendered image.
"""
import pytest

from autoposter.badges.geometry import backdrop_box
from autoposter.badges.spec import (
    BACK_COLOR,
    BADGES,
    EPISODE_CANVAS,
    POSTER_CANVAS,
    canvas_for,
)


def test_every_expected_badge_is_defined():
    assert set(BADGES) == {
        "resolution", "audio_codec", "critic", "audience", "commonsense",
        "video_format", "runtimes", "episode_info", "languages",
    }


def test_canvas_sizes():
    assert POSTER_CANVAS == (1000, 1500)
    assert EPISODE_CANVAS == (1920, 1080)


@pytest.mark.parametrize(
    "art_kind,expected",
    [("poster", (1000, 1500)), ("season_poster", (1000, 1500)),
     ("title_card", (1920, 1080)), ("background", (1920, 1080))],
)
def test_canvas_for(art_kind, expected):
    assert canvas_for(art_kind) == expected


def test_backdrop_is_sixty_percent_black():
    assert BACK_COLOR == (0, 0, 0, 153)


@pytest.mark.parametrize(
    "name,canvas,expected",
    [
        ("resolution", POSTER_CANVAS, (15, 15, 320, 120)),
        ("commonsense", POSTER_CANVAS, (15, 1125, 320, 1230)),
        ("video_format", POSTER_CANVAS, (15, 1365, 320, 1470)),
        ("runtimes", POSTER_CANVAS, (385, 1365, 985, 1470)),
        ("critic", POSTER_CANVAS, (795, 550, 985, 740)),
        ("audience", POSTER_CANVAS, (795, 760, 985, 950)),
        ("audio_codec", EPISODE_CANVAS, (808, 15, 1113, 120)),
        ("episode_info", EPISODE_CANVAS, (1600, 825, 1905, 930)),
        ("runtimes", EPISODE_CANVAS, (1305, 945, 1905, 1050)),
    ],
)
def test_specs_produce_the_measured_boxes(name, canvas, expected):
    spec = BADGES[name]
    assert backdrop_box(canvas, spec.box, spec.h_align, spec.h_offset,
                        spec.v_align, spec.v_offset, spec.padding) == expected


def test_commonsense_and_episode_info_keep_their_surprising_offsets():
    """Both come from a YAML branch that fires unexpectedly; both were
    confirmed against pixels. A "tidy-up" to 30 would be a regression."""
    assert BADGES["commonsense"].v_offset == 270
    assert BADGES["episode_info"].v_offset == 150


def test_languages_has_no_backdrop():
    """Every other badge draws one; neither oracle image shows one here."""
    assert BADGES["languages"].has_back is False
    assert all(BADGES[n].has_back for n in BADGES if n != "languages")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `rtk proxy python -m pytest tests/test_badge_spec.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'autoposter.badges.spec'`

- [ ] **Step 3: Write the implementation**

Create `src/autoposter/badges/spec.py`:

```python
"""Badge definitions.

Every parity-critical constant lives here so it can be reviewed in one place.
Values come from Kometa v2.4.8's overlay defaults, cross-checked against
measured pixels from two production oracle images -- see
docs/research/kometa-overlays.md.
"""
from dataclasses import dataclass
from pathlib import Path

ASSETS = Path(__file__).resolve().parents[3] / "assets" / "badges"
FONTS = ASSETS / "fonts"
IMAGES = ASSETS / "images"

POSTER_CANVAS = (1000, 1500)
EPISODE_CANVAS = (1920, 1080)

# "#00000099" -- 60% opacity black.
BACK_COLOR = (0, 0, 0, 153)
FONT_COLOR = (255, 255, 255, 255)

INTER_BOLD = FONTS / "Inter-Bold.ttf"
INTER_MEDIUM = FONTS / "Inter-Medium.ttf"


@dataclass(frozen=True)
class BadgeSpec:
    name: str
    h_align: str
    h_offset: int
    v_align: str
    v_offset: int
    box: tuple[int, int]
    padding: int = 0
    radius: int = 30
    font: Path | None = None
    font_size: int = 55
    has_back: bool = True


BADGES: dict[str, BadgeSpec] = {
    "resolution": BadgeSpec("resolution", "left", 15, "top", 15, (305, 105)),
    "audio_codec": BadgeSpec("audio_codec", "center", 0, "top", 15, (305, 105)),
    "critic": BadgeSpec(
        "critic", "right", 30, "center", -105, (160, 160),
        padding=15, font=INTER_BOLD, font_size=63,
    ),
    "audience": BadgeSpec(
        "audience", "right", 30, "center", 105, (160, 160),
        padding=15, font=INTER_BOLD, font_size=63,
    ),
    # 270, not 30: Kometa's `vertical_align.exists: false` branch fires here
    # even though the file sets `vertical_align: bottom`. Confirmed in pixels.
    "commonsense": BadgeSpec(
        "commonsense", "left", 15, "bottom", 270, (305, 105), font=INTER_MEDIUM,
    ),
    "video_format": BadgeSpec(
        "video_format", "left", 15, "bottom", 30, (305, 105), font=INTER_MEDIUM,
    ),
    "runtimes": BadgeSpec(
        "runtimes", "right", 15, "bottom", 30, (600, 105), font=INTER_MEDIUM,
    ),
    # 150 for the same reason as commonsense's 270; measured at y=825 on a
    # 1080-high canvas, which is exactly 1080 - 105 - 150.
    "episode_info": BadgeSpec(
        "episode_info", "right", 15, "bottom", 150, (305, 105), font=INTER_MEDIUM,
    ),
    # The only badge without a backdrop: the production config applies the
    # languages overlay twice, the second time fully transparent, and neither
    # oracle image shows a backdrop behind the flag.
    "languages": BadgeSpec(
        "languages", "left", 15, "top", 223, (190, 105),
        radius=26, font=INTER_BOLD, font_size=50, has_back=False,
    ),
}


def canvas_for(art_kind: str) -> tuple[int, int]:
    """Kometa sizes by item type: episodes are landscape, everything else portrait."""
    return EPISODE_CANVAS if art_kind in ("title_card", "background") else POSTER_CANVAS
```

- [ ] **Step 4: Run test to verify it passes**

Run: `rtk proxy python -m pytest tests/test_badge_spec.py -v`
Expected: PASS — 19 passed

- [ ] **Step 5: Run ruff and commit**

```bash
rtk proxy ruff check src tests
git add src/autoposter/badges/spec.py tests/test_badge_spec.py
git commit --no-gpg-sign -m "Add badge definitions with measured geometry"
```

---

## Task 5: Drawing primitives

**Files:**
- Create: `src/autoposter/badges/draw.py`
- Test: `tests/test_badge_draw.py`

**Interfaces:**
- Consumes: `BadgeSpec`, `BACK_COLOR`, `FONT_COLOR` from `spec.py`; `backdrop_box` from `geometry.py`.
- Produces: `new_layer(canvas) -> Image.Image`; `draw_backdrop(layer, spec, canvas) -> tuple[int, int, int, int]`; `paste_centered(layer, image, box) -> None`; `draw_text_centered(layer, text, font, box, color=FONT_COLOR) -> None`; `composite(base: Image.Image, layer: Image.Image) -> None`.

**Notes for the implementer:**

- A layer is a full-canvas `RGBA` image with `(0, 0, 0, 0)` background. Composite it with `base.paste(layer, (0, 0), layer)` — using the layer's own alpha as the mask. Do **not** use `Image.alpha_composite`; Kometa uses `paste`, and the two differ where layers overlap.
- `paste_centered` must **not** resize. Some badge images are larger than their box (two `audio_codec` images are 133–135px tall against a 105px box). Centre them on the box centre and let them overflow.
- Text is centred on the box using the font's bounding box, drawn with `anchor="lt"` at the computed top-left, matching Kometa.

- [ ] **Step 1: Write the failing test**

Create `tests/test_badge_draw.py`:

```python
"""Pillow drawing primitives.

Assertions are on pixels rather than on calls, because the failure mode that
matters is "the badge is drawn in the wrong place", which mocking cannot catch.
"""
from PIL import Image, ImageFont

from autoposter.badges.draw import (
    composite,
    draw_backdrop,
    draw_text_centered,
    new_layer,
    paste_centered,
)
from autoposter.badges.spec import BADGES, INTER_MEDIUM, POSTER_CANVAS


def test_new_layer_is_a_fully_transparent_canvas():
    layer = new_layer(POSTER_CANVAS)
    assert layer.size == POSTER_CANVAS
    assert layer.mode == "RGBA"
    assert layer.getpixel((500, 750)) == (0, 0, 0, 0)


def test_draw_backdrop_fills_its_box_and_nothing_else():
    layer = new_layer(POSTER_CANVAS)
    box = draw_backdrop(layer, BADGES["resolution"], POSTER_CANVAS)
    assert box == (15, 15, 320, 120)
    # Solidly inside the box.
    assert layer.getpixel((160, 67))[3] == 153
    # Outside it, untouched.
    assert layer.getpixel((500, 500))[3] == 0


def test_backdrop_corners_are_rounded():
    """A square corner would mean radius was ignored."""
    layer = new_layer(POSTER_CANVAS)
    draw_backdrop(layer, BADGES["resolution"], POSTER_CANVAS)
    assert layer.getpixel((16, 16))[3] == 0


def test_paste_centered_does_not_resize_a_large_image():
    layer = new_layer(POSTER_CANVAS)
    tall = Image.new("RGBA", (200, 135), (255, 0, 0, 255))
    paste_centered(layer, tall, (15, 15, 320, 120))
    # 135 tall centred on a 105-tall box overflows by 15px each way.
    assert layer.getpixel((167, 10))[3] == 255
    assert layer.getpixel((167, 124))[3] == 255


def test_paste_centered_centres_a_small_image():
    layer = new_layer(POSTER_CANVAS)
    small = Image.new("RGBA", (100, 20), (0, 255, 0, 255))
    paste_centered(layer, small, (15, 15, 320, 120))
    cx, cy = (15 + 320) // 2, (15 + 120) // 2
    assert layer.getpixel((cx, cy)) == (0, 255, 0, 255)
    assert layer.getpixel((cx - 60, cy))[3] == 0


def test_draw_text_centered_marks_pixels_inside_the_box():
    layer = new_layer(POSTER_CANVAS)
    font = ImageFont.truetype(str(INTER_MEDIUM), 55)
    draw_text_centered(layer, "WEB", font, (15, 1365, 320, 1470))
    region = layer.crop((15, 1365, 320, 1470))
    assert any(px[3] > 0 for px in region.getdata())
    outside = layer.crop((400, 1365, 700, 1470))
    assert all(px[3] == 0 for px in outside.getdata())


def test_composite_uses_the_layer_alpha_as_a_mask():
    base = Image.new("RGB", (10, 10), (255, 255, 255))
    layer = Image.new("RGBA", (10, 10), (0, 0, 0, 0))
    layer.putpixel((5, 5), (255, 0, 0, 255))
    composite(base, layer)
    assert base.getpixel((5, 5)) == (255, 0, 0)
    assert base.getpixel((0, 0)) == (255, 255, 255)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `rtk proxy python -m pytest tests/test_badge_draw.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'autoposter.badges.draw'`

- [ ] **Step 3: Write the implementation**

Create `src/autoposter/badges/draw.py`:

```python
"""Pillow primitives for badge rendering.

Each badge becomes a full-canvas RGBA layer that is pasted onto the poster
using its own alpha as the mask -- the same call Kometa makes. Using
``Image.alpha_composite`` instead would give different results where layers
overlap, so it is deliberately not used here.
"""
from PIL import Image, ImageDraw, ImageFont

from autoposter.badges.geometry import backdrop_box
from autoposter.badges.spec import BACK_COLOR, FONT_COLOR, BadgeSpec


def new_layer(canvas: tuple[int, int]) -> Image.Image:
    """A transparent full-canvas layer to draw one badge onto."""
    return Image.new("RGBA", canvas, (0, 0, 0, 0))


def draw_backdrop(
    layer: Image.Image, spec: BadgeSpec, canvas: tuple[int, int]
) -> tuple[int, int, int, int]:
    """Draw the badge's rounded backdrop and return its box.

    The box is returned even when ``has_back`` is false, because callers need
    it to place content regardless of whether anything was drawn behind it.
    """
    box = backdrop_box(
        canvas, spec.box, spec.h_align, spec.h_offset,
        spec.v_align, spec.v_offset, spec.padding,
    )
    if spec.has_back:
        ImageDraw.Draw(layer).rounded_rectangle(box, radius=spec.radius, fill=BACK_COLOR)
    return box


def paste_centered(layer: Image.Image, image: Image.Image, box: tuple[int, int, int, int]) -> None:
    """Centre an image on a box at its native size.

    Never resizes. Several badge images are taller than their backdrop -- two
    audio codec images are 135px against a 105px box -- and Kometa lets them
    overflow rather than shrinking them.
    """
    cx = (box[0] + box[2]) // 2
    cy = (box[1] + box[3]) // 2
    layer.paste(image, (cx - image.width // 2, cy - image.height // 2), image)


def draw_text_centered(
    layer: Image.Image,
    text: str,
    font: ImageFont.FreeTypeFont,
    box: tuple[int, int, int, int],
    color: tuple[int, int, int, int] = FONT_COLOR,
) -> None:
    """Draw text centred on a box, anchored top-left like Kometa does."""
    drawing = ImageDraw.Draw(layer)
    left, top, right, bottom = drawing.textbbox((0, 0), text, font=font)
    x = (box[0] + box[2]) // 2 - (right - left) // 2 - left
    y = (box[1] + box[3]) // 2 - (bottom - top) // 2 - top
    drawing.text((x, y), text, font=font, fill=color, anchor="lt")


def composite(base: Image.Image, layer: Image.Image) -> None:
    """Paste a badge layer onto the base using the layer's alpha as the mask."""
    base.paste(layer, (0, 0), layer)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `rtk proxy python -m pytest tests/test_badge_draw.py -v`
Expected: PASS — 7 passed

- [ ] **Step 5: Run ruff and commit**

```bash
rtk proxy ruff check src tests
git add src/autoposter/badges/draw.py tests/test_badge_draw.py
git commit --no-gpg-sign -m "Add Pillow drawing primitives for badges"
```

---

## Task 6: The composer and oracle parity

Turns a base file plus badge inputs into encoded WebP bytes, and proves the result matches production output.

**Files:**
- Create: `src/autoposter/badges/compose.py`
- Test: `tests/test_badge_compose.py`
- Test: `tests/test_badge_parity.py`

**Interfaces:**
- Consumes: everything from Tasks 1–5.
- Produces: the frozen dataclass `BadgeInputs(media: MediaInfo, critic_rating: float | None, audience_rating: float | None, content_rating: str | None, video_format: str | None)`; `compose(base_path: Path, art_kind: str, inputs: BadgeInputs) -> bytes`; `badge_values(art_kind: str, inputs: BadgeInputs) -> dict[str, str]`; `badge_fingerprint(base_sha256: str, art_kind: str, values: dict[str, str], asset_manifest_sha: str) -> str`.

**Notes for the implementer:**

- `badge_values` returns only the badges that will actually be drawn, keyed by badge name, with the displayed string (or the image stem, for image badges) as the value. It is the input to the fingerprint, so it must contain everything that affects the pixels and nothing that does not.
- `episode_info` is only ever produced for `art_kind == "title_card"`. `resolution` and `audio_codec` are image badges: their value is the image stem, e.g. `"1080p"`.
- Encode with `image.save(buffer, format="WEBP", quality=90, exif=exif)` where `exif` is an `Image.Exif()` with `exif[0x04BC] = "overlay"`.
- The badge canvas must be built by opening the base, `.convert("RGB")`, then `.resize(canvas, Image.Resampling.LANCZOS)`.
- `asset_manifest_sha` is the SHA-256 of `assets/badges/MANIFEST.sha256`, so replacing a badge PNG changes every fingerprint. Compute it once at module import.

- [ ] **Step 1: Write the failing test for composition**

Create `tests/test_badge_compose.py`:

```python
"""Composition and fingerprinting."""
import io
from pathlib import Path

import pytest
from PIL import Image

from autoposter.badges.compose import BadgeInputs, badge_fingerprint, badge_values, compose
from autoposter.badges.values import MediaInfo

ORACLE = Path("tests/fixtures/oracle")


def _inputs(**over):
    base = dict(
        media=MediaInfo("1080", "eac3", 6, 4845912, ("en",), frozenset(), 1, 1),
        critic_rating=4.9, audience_rating=6.3, content_rating="17", video_format="WEB",
    )
    base.update(over)
    return BadgeInputs(**base)


def test_badge_values_for_a_poster():
    values = badge_values("poster", _inputs())
    assert values["resolution"] == "1080p"
    assert values["audio_codec"] == "plus"
    assert values["critic"] == "4.9"
    assert values["audience"] == "63%"
    assert values["commonsense"] == "17+"
    assert values["video_format"] == "WEB"
    assert values["runtimes"] == "Runtime: 1h 20m"


def test_episode_info_appears_only_on_title_cards():
    assert "episode_info" not in badge_values("poster", _inputs())
    assert badge_values("title_card", _inputs())["episode_info"] == "S01E01"


def test_absent_values_are_omitted_entirely():
    values = badge_values("poster", _inputs(critic_rating=None, content_rating=None))
    assert "critic" not in values
    assert "commonsense" not in values
    assert "audience" in values


def test_fingerprint_changes_when_a_displayed_value_changes():
    a = badge_fingerprint("abc", "poster", {"critic": "8.6"}, "m")
    b = badge_fingerprint("abc", "poster", {"critic": "8.7"}, "m")
    assert a != b


def test_fingerprint_changes_when_the_base_changes():
    a = badge_fingerprint("abc", "poster", {"critic": "8.6"}, "m")
    b = badge_fingerprint("xyz", "poster", {"critic": "8.6"}, "m")
    assert a != b


def test_fingerprint_changes_when_a_badge_asset_changes():
    """Replacing a badge PNG must invalidate every render that uses it."""
    a = badge_fingerprint("abc", "poster", {"critic": "8.6"}, "manifest-1")
    b = badge_fingerprint("abc", "poster", {"critic": "8.6"}, "manifest-2")
    assert a != b


def test_fingerprint_is_stable_across_key_ordering():
    a = badge_fingerprint("abc", "poster", {"critic": "8.6", "audience": "63%"}, "m")
    b = badge_fingerprint("abc", "poster", {"audience": "63%", "critic": "8.6"}, "m")
    assert a == b


def test_compose_produces_a_webp_at_the_poster_canvas():
    data = compose(ORACLE / "All_Souls_base_no_overlay.jpg", "poster", _inputs())
    image = Image.open(io.BytesIO(data))
    assert image.format == "WEBP"
    assert image.size == (1000, 1500)


def test_compose_produces_a_landscape_webp_for_a_title_card():
    data = compose(ORACLE / "8OO10C_S01E01_base_no_overlay.jpg", "title_card", _inputs())
    image = Image.open(io.BytesIO(data))
    assert image.size == (1920, 1080)


def test_compose_stamps_the_overlay_exif_marker():
    """Kometa writes this tag and reads it back to detect already-overlaid
    images; anything consuming our output should see the same marker."""
    data = compose(ORACLE / "All_Souls_base_no_overlay.jpg", "poster", _inputs())
    assert Image.open(io.BytesIO(data)).getexif().get(0x04BC) == "overlay"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `rtk proxy python -m pytest tests/test_badge_compose.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'autoposter.badges.compose'`

- [ ] **Step 3: Write the implementation**

Create `src/autoposter/badges/compose.py`:

```python
"""Base artwork plus badge inputs, out to encoded WebP bytes.

Mirrors Kometa's own sequence: open, convert to RGB, LANCZOS-resize to the
badge canvas, paste one full-canvas RGBA layer per badge, save as WebP at
quality 90 with the overlay EXIF marker.
"""
import hashlib
import io
from dataclasses import dataclass
from pathlib import Path

from PIL import Image, ImageFont

from autoposter.badges import values as badge_values_module
from autoposter.badges.draw import (
    composite,
    draw_backdrop,
    draw_text_centered,
    new_layer,
    paste_centered,
)
from autoposter.badges.spec import ASSETS, BADGES, IMAGES, canvas_for
from autoposter.badges.values import (
    MediaInfo,
    audience_text,
    audio_codec_image,
    commonsense_text,
    critic_text,
    episode_text,
    language_slots,
    resolution_image,
    runtime_text,
)

WEBP_QUALITY = 90
EXIF_OVERLAY_TAG = 0x04BC

MANIFEST_SHA = hashlib.sha256((ASSETS / "MANIFEST.sha256").read_bytes()).hexdigest()

# Badges whose value is an image stem rather than a string to draw.
IMAGE_BADGES = {
    "resolution": IMAGES / "resolution",
    "audio_codec": IMAGES / "audio_codec" / "standard",
}
# Badges that pair a logo above or beside their text.
BADGE_ICONS = {
    "critic": IMAGES / "rating" / "IMDb.png",
    "audience": IMAGES / "rating" / "TMDb.png",
    "commonsense": IMAGES / "Commonsense.png",
}


@dataclass(frozen=True)
class BadgeInputs:
    """Everything the badge layer needs, gathered from Plex and ItemFacts."""

    media: MediaInfo
    critic_rating: float | None = None
    audience_rating: float | None = None
    content_rating: str | None = None
    video_format: str | None = None


def badge_values(art_kind: str, inputs: BadgeInputs) -> dict[str, str]:
    """The badges that will actually be drawn, and what each will show.

    Absent values are omitted rather than mapped to a placeholder -- an item
    with no critic rating gets no critic badge, which is what Kometa does and
    what the episode oracle shows.
    """
    result: dict[str, str] = {}
    candidates = {
        "resolution": resolution_image(inputs.media),
        "audio_codec": audio_codec_image(inputs.media),
        "critic": critic_text(inputs.critic_rating),
        "audience": audience_text(inputs.audience_rating),
        "commonsense": commonsense_text(inputs.content_rating),
        "video_format": inputs.video_format,
        "runtimes": runtime_text(inputs.media.duration_ms),
    }
    if art_kind == "title_card":
        candidates["episode_info"] = episode_text(
            inputs.media.season_number, inputs.media.episode_number
        )
    for name, value in candidates.items():
        if value:
            result[name] = value

    slots = language_slots(inputs.media)
    if slots:
        result["languages"] = ",".join("%s:%s" % pair for pair in slots)
    return result


def badge_fingerprint(
    base_sha256: str, art_kind: str, values: dict[str, str], asset_manifest_sha: str
) -> str:
    """Hash everything that affects the badged image.

    Deliberately separate from the base ``fingerprint``: a rating changing must
    re-badge and re-upload without re-fetching or re-compositing the base.
    """
    parts = [base_sha256, art_kind, asset_manifest_sha]
    parts += ["%s=%s" % (k, values[k]) for k in sorted(values)]
    return hashlib.sha256("\x1f".join(parts).encode("utf-8")).hexdigest()


def _load(path: Path) -> Image.Image:
    return Image.open(path).convert("RGBA")


def compose(base_path: Path, art_kind: str, inputs: BadgeInputs) -> bytes:
    """Badge the base artwork and return encoded WebP bytes."""
    canvas = canvas_for(art_kind)
    poster = Image.open(base_path).convert("RGB").resize(canvas, Image.Resampling.LANCZOS)

    values = badge_values(art_kind, inputs)
    for name, value in values.items():
        if name == "languages":
            continue
        spec = BADGES[name]
        layer = new_layer(canvas)
        box = draw_backdrop(layer, spec, canvas)

        if name in IMAGE_BADGES:
            image_path = IMAGE_BADGES[name] / ("%s.png" % value)
            if not image_path.exists():
                continue
            paste_centered(layer, _load(image_path), box)
        else:
            icon_path = BADGE_ICONS.get(name)
            font = ImageFont.truetype(str(spec.font), spec.font_size)
            if icon_path is not None and icon_path.exists():
                icon = _load(icon_path)
                if name == "commonsense":
                    # Icon to the left of the text, 15px gap.
                    icon_box = (box[0] + 15, box[1], box[0] + 15 + icon.width, box[3])
                    paste_centered(layer, icon, icon_box)
                    text_box = (icon_box[2] + 15, box[1], box[2], box[3])
                else:
                    # Rating logos sit above the number, 15px gap.
                    icon_box = (box[0], box[1] + 15, box[2], box[1] + 15 + icon.height)
                    paste_centered(layer, icon, icon_box)
                    text_box = (box[0], icon_box[3] + 15, box[2], box[3])
                draw_text_centered(layer, value, font, text_box)
            else:
                draw_text_centered(layer, value, font, box)
        composite(poster, layer)

    _draw_languages(poster, canvas, inputs)

    exif = Image.Exif()
    exif[EXIF_OVERLAY_TAG] = "overlay"
    buffer = io.BytesIO()
    poster.save(buffer, format="WEBP", quality=WEBP_QUALITY, exif=exif)
    return buffer.getvalue()


def _draw_languages(poster: Image.Image, canvas: tuple[int, int], inputs: BadgeInputs) -> None:
    """Flag plus label per audio language, stacked 61px apart.

    Applied after every other badge because Kometa runs queue-based overlays
    last. No backdrop -- see the note in spec.py.
    """
    spec = BADGES["languages"]
    font = ImageFont.truetype(str(spec.font), spec.font_size)
    for index, (country, label) in enumerate(language_slots(inputs.media)):
        flag_path = IMAGES / "flag" / "round" / ("%s.png" % country)
        if not flag_path.exists():
            continue
        flag = _load(flag_path)
        top = spec.v_offset + index * 61
        layer = new_layer(canvas)
        layer.paste(flag, (spec.h_offset, top), flag)
        draw_text_centered(
            layer, label, font,
            (spec.h_offset + flag.width + 14, top, spec.h_offset + flag.width + 90, top + flag.height),
        )
        composite(poster, layer)


__all__ = ["BadgeInputs", "badge_fingerprint", "badge_values", "compose", "MANIFEST_SHA",
           "badge_values_module"]
```

Remove `badge_values_module` from the imports and `__all__` if it is unused after implementation — it is listed here only to make the module's public surface explicit; do not leave an unused import behind for ruff to flag.

- [ ] **Step 4: Run test to verify it passes**

Run: `rtk proxy python -m pytest tests/test_badge_compose.py -v`
Expected: PASS — 11 passed

- [ ] **Step 5: Write the parity test**

Create `tests/test_badge_parity.py`. This is the task's real deliverable — everything else is scaffolding for it.

```python
"""Parity against real production output.

Both oracle pairs were pulled from the live Plex server: a base that our own
Phase 1 pipeline produced, and the badged image the tool being replaced
uploaded for the same item. We cannot be byte-identical -- we encode our own
WebP from our own base -- so the assertion is that badging the base moves it
substantially *towards* the production output inside each badge region, and
leaves it alone everywhere else.
"""
from pathlib import Path

import numpy as np
import pytest
from PIL import Image

from autoposter.badges.compose import BadgeInputs, compose
from autoposter.badges.geometry import backdrop_box
from autoposter.badges.spec import BADGES
from autoposter.badges.values import MediaInfo

ORACLE = Path("tests/fixtures/oracle")


def _as_array(image: Image.Image) -> np.ndarray:
    return np.asarray(image.convert("RGB"), dtype=np.int16)


def _region_error(a: np.ndarray, b: np.ndarray, box) -> float:
    x0, y0, x1, y1 = box
    return float(np.abs(a[y0:y1, x0:x1] - b[y0:y1, x0:x1]).mean())


ALL_SOULS = BadgeInputs(
    media=MediaInfo("1080", "eac3", 6, 4845912, ("en",), frozenset(), None, None),
    critic_rating=4.9, audience_rating=6.3, content_rating="17", video_format="WEB",
)


@pytest.mark.parametrize(
    "badge", ["resolution", "audio_codec", "critic", "audience", "commonsense",
              "video_format", "runtimes"],
)
def test_each_poster_badge_moves_towards_production_output(badge):
    canvas = (1000, 1500)
    base_path = ORACLE / "All_Souls_base_no_overlay.jpg"
    oracle = _as_array(Image.open(ORACLE / "All_Souls_plex_overlaid.jpg"))
    bare = _as_array(Image.open(base_path).convert("RGB").resize(canvas, Image.Resampling.LANCZOS))
    ours = _as_array(Image.open(__import__("io").BytesIO(compose(base_path, "poster", ALL_SOULS))))

    spec = BADGES[badge]
    box = backdrop_box(canvas, spec.box, spec.h_align, spec.h_offset,
                       spec.v_align, spec.v_offset, spec.padding)
    before = _region_error(bare, oracle, box)
    after = _region_error(ours, oracle, box)
    assert after < before, (
        "badging made region %r worse: %.1f -> %.1f" % (badge, before, after)
    )


def test_non_badge_area_is_untouched():
    """The centre of the poster carries no badge; badging must not disturb it."""
    canvas = (1000, 1500)
    base_path = ORACLE / "All_Souls_base_no_overlay.jpg"
    bare = _as_array(Image.open(base_path).convert("RGB").resize(canvas, Image.Resampling.LANCZOS))
    ours = _as_array(Image.open(__import__("io").BytesIO(compose(base_path, "poster", ALL_SOULS))))
    assert _region_error(bare, ours, (350, 400, 700, 500)) < 6.0


EPISODE = BadgeInputs(
    media=MediaInfo("1080", "eac3", 6, 2700000, ("en",), frozenset(), 1, 1),
    critic_rating=None, audience_rating=6.3, content_rating=None, video_format="WEB",
)


@pytest.mark.parametrize("badge", ["resolution", "audio_codec", "episode_info", "runtimes"])
def test_each_episode_badge_moves_towards_production_output(badge):
    canvas = (1920, 1080)
    base_path = ORACLE / "8OO10C_S01E01_base_no_overlay.jpg"
    oracle = _as_array(Image.open(ORACLE / "8OO10C_S01E01_plex_overlaid.jpg"))
    bare = _as_array(Image.open(base_path).convert("RGB").resize(canvas, Image.Resampling.LANCZOS))
    ours = _as_array(
        Image.open(__import__("io").BytesIO(compose(base_path, "title_card", EPISODE)))
    )

    spec = BADGES[badge]
    box = backdrop_box(canvas, spec.box, spec.h_align, spec.h_offset,
                       spec.v_align, spec.v_offset, spec.padding)
    before = _region_error(bare, oracle, box)
    after = _region_error(ours, oracle, box)
    assert after < before, (
        "badging made region %r worse: %.1f -> %.1f" % (badge, before, after)
    )


def test_the_suppressed_critic_badge_region_stays_bare():
    """This episode has no IMDb rating, and production drew no critic badge
    there. Drawing one would be a parity failure that no other test catches."""
    canvas = (1920, 1080)
    base_path = ORACLE / "8OO10C_S01E01_base_no_overlay.jpg"
    bare = _as_array(Image.open(base_path).convert("RGB").resize(canvas, Image.Resampling.LANCZOS))
    ours = _as_array(
        Image.open(__import__("io").BytesIO(compose(base_path, "title_card", EPISODE)))
    )
    spec = BADGES["critic"]
    box = backdrop_box(canvas, spec.box, spec.h_align, spec.h_offset,
                       spec.v_align, spec.v_offset, spec.padding)
    assert _region_error(bare, ours, box) < 6.0
```

Replace the `__import__("io").BytesIO` calls with a normal `import io` at the top of the file; they are written inline above only to keep each example self-contained.

- [ ] **Step 6: Run the parity test**

Run: `rtk proxy python -m pytest tests/test_badge_parity.py -v`
Expected: PASS — 13 passed

If a badge region fails, the badge is misplaced or its content is wrong. Diagnose by saving the composed image and diffing it against the oracle rather than by loosening the threshold. `numpy` and `scipy` are both already available.

- [ ] **Step 7: Run ruff and commit**

```bash
rtk proxy ruff check src tests
git add src/autoposter/badges/compose.py tests/test_badge_compose.py tests/test_badge_parity.py
git commit --no-gpg-sign -m "Compose badges onto base artwork and verify against production output"
```

---

## Task 7: Badge fingerprint columns

**Files:**
- Modify: `src/autoposter/db/models.py`
- Create: one Alembic migration under `alembic/versions/`
- Test: `tests/test_badge_render_state.py`

**Interfaces:**
- Produces: `Render.badge_fingerprint: str | None`, `Render.uploaded_at: datetime | None`, `Render.upload_status: str`.

**Critical process note.** `tests/conftest.py` calls `create_all` against the same database, so autogenerating a migration *after* running the suite produces an empty no-op that silently creates nothing on a real deployment. This has already happened once on this project. Reset first, replay existing migrations, and only then autogenerate:

```bash
docker compose down && docker compose up -d postgres && sleep 8
export AUTOPOSTER_DATABASE_URL=postgresql+asyncpg://autoposter:autoposter@localhost:5433/autoposter
rtk proxy python -m alembic upgrade head
rtk proxy python -m alembic revision --autogenerate -m "badge fingerprint and upload state"
```

Note `docker compose down` **without** `-v`. Open the generated file and confirm `upgrade()` contains real `op.add_column` calls for all three columns. A body of `pass` means the diff was taken against a database that already had them — start over from the reset. `tests/test_migrations.py` guards this and must pass.

- [ ] **Step 1: Write the failing test**

Create `tests/test_badge_render_state.py`:

```python
"""Badge upload state on the render row."""
from sqlalchemy import select

from autoposter.db.models import MediaItem, Render


async def _item(session):
    item = MediaItem(kind="movie", rating_key="1", title="X")
    session.add(item)
    await session.flush()
    return item


async def test_a_new_render_has_no_badge_state(session):
    item = await _item(session)
    render = Render(item_id=item.id, art_kind="poster", asset_path="/x.jpg")
    session.add(render)
    await session.flush()
    assert render.badge_fingerprint is None
    assert render.uploaded_at is None
    assert render.upload_status == "pending"


async def test_badge_state_round_trips(session):
    item = await _item(session)
    render = Render(item_id=item.id, art_kind="poster", asset_path="/x.jpg",
                    badge_fingerprint="a" * 64, upload_status="uploaded")
    session.add(render)
    await session.flush()
    loaded = (await session.execute(select(Render).where(Render.id == render.id))).scalar_one()
    assert loaded.badge_fingerprint == "a" * 64
    assert loaded.upload_status == "uploaded"


async def test_badge_fingerprint_is_independent_of_the_base_fingerprint(session):
    """The whole point of a second fingerprint: a rating change re-badges
    without invalidating the base render."""
    item = await _item(session)
    render = Render(item_id=item.id, art_kind="poster", asset_path="/x.jpg",
                    fingerprint="b" * 64, badge_fingerprint="a" * 64)
    session.add(render)
    await session.flush()
    render.badge_fingerprint = "c" * 64
    await session.flush()
    assert render.fingerprint == "b" * 64
```

- [ ] **Step 2: Run test to verify it fails**

Run: `rtk proxy python -m pytest tests/test_badge_render_state.py -v`
Expected: FAIL with `TypeError: 'badge_fingerprint' is an invalid keyword argument for Render`

- [ ] **Step 3: Add the columns**

In `src/autoposter/db/models.py`, inside `class Render`, after the existing `fingerprint` column:

```python
    # Separate from `fingerprint`, which covers the base image only. A rating
    # changing must re-badge and re-upload without re-fetching or
    # re-compositing the base.
    badge_fingerprint: Mapped[str | None] = mapped_column(String(64), index=True)
    uploaded_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # pending | uploaded | skipped | failed
    upload_status: Mapped[str] = mapped_column(String(24), default="pending")
```

- [ ] **Step 4: Generate the migration against a clean database**

Follow the reset-then-autogenerate sequence in the process note above, then:

```bash
rtk proxy python -m alembic upgrade head
rtk proxy python -m pytest tests/test_migrations.py -v
```

Expected: PASS, with no drift reported.

- [ ] **Step 5: Run the tests and commit**

Run: `rtk proxy python -m pytest tests/test_badge_render_state.py tests/test_migrations.py -v`
Expected: PASS — 3 passed plus the migration guard

```bash
rtk proxy ruff check src tests
git add src/autoposter/db/models.py alembic tests/test_badge_render_state.py
git commit --no-gpg-sign -m "Track badge fingerprint and upload state on renders"
```

---

## Task 8: Plex artwork upload

**Files:**
- Create: `src/autoposter/plex/artwork.py`
- Test: `tests/test_plex_artwork.py`

**Interfaces:**
- Consumes: nothing from earlier tasks.
- Produces: `upload_artwork(plex_item, data: bytes, art_kind: str, lock: bool = True) -> None`.

**Notes for the implementer:**

- `plexapi` 4.18.2 exposes `uploadPoster(url=None, filepath=None)` and `uploadArt(url=None, filepath=None)` on `Movie`, `Show`, `Season` and `Episode`, verified against the installed package. There is no bytes-accepting overload, so write to a `NamedTemporaryFile` and pass `filepath`, cleaning up afterwards on both the success and failure paths.
- `title_card` and `background` are different things despite sharing a canvas size: an episode's badged image is its **poster** (`uploadPoster`), because that is what Plex shows as the episode thumbnail. Only a show or movie *background* uses `uploadArt`. Route on `art_kind`: `background` uses `uploadArt`, everything else uses `uploadPoster`.
- Lock after uploading — `lockPoster()` / `lockArt()`. Production items already show `thumb` and `art` locked, and without the lock Plex's agent can reclaim the field.
- **Do not call `.refresh()`.** A guard test over `src/autoposter/plex/` forbids it.

- [ ] **Step 1: Write the failing test**

Create `tests/test_plex_artwork.py`:

```python
"""Uploading badged artwork to Plex."""
import pytest

from autoposter.plex.artwork import upload_artwork


class FakePlexItem:
    def __init__(self):
        self.uploaded_poster = None
        self.uploaded_art = None
        self.poster_locked = False
        self.art_locked = False
        self.seen_bytes = None

    def _capture(self, filepath):
        with open(filepath, "rb") as handle:
            self.seen_bytes = handle.read()

    def uploadPoster(self, url=None, filepath=None):
        self.uploaded_poster = filepath
        self._capture(filepath)

    def uploadArt(self, url=None, filepath=None):
        self.uploaded_art = filepath
        self._capture(filepath)

    def lockPoster(self):
        self.poster_locked = True

    def lockArt(self):
        self.art_locked = True


@pytest.mark.parametrize("art_kind", ["poster", "season_poster", "title_card"])
def test_posters_and_title_cards_upload_as_posters(art_kind):
    item = FakePlexItem()
    upload_artwork(item, b"webp-bytes", art_kind)
    assert item.uploaded_poster is not None
    assert item.uploaded_art is None
    assert item.seen_bytes == b"webp-bytes"


def test_backgrounds_upload_as_art():
    item = FakePlexItem()
    upload_artwork(item, b"webp-bytes", "background")
    assert item.uploaded_art is not None
    assert item.uploaded_poster is None


def test_upload_locks_the_field_so_plex_cannot_reclaim_it():
    item = FakePlexItem()
    upload_artwork(item, b"x", "poster")
    assert item.poster_locked is True

    art_item = FakePlexItem()
    upload_artwork(art_item, b"x", "background")
    assert art_item.art_locked is True


def test_locking_can_be_disabled():
    item = FakePlexItem()
    upload_artwork(item, b"x", "poster", lock=False)
    assert item.poster_locked is False


def test_the_temporary_file_is_removed_afterwards():
    item = FakePlexItem()
    upload_artwork(item, b"x", "poster")
    assert not __import__("os").path.exists(item.uploaded_poster)


def test_the_temporary_file_is_removed_when_the_upload_fails():
    class Failing(FakePlexItem):
        def uploadPoster(self, url=None, filepath=None):
            self.uploaded_poster = filepath
            raise RuntimeError("plex said no")

    item = Failing()
    with pytest.raises(RuntimeError):
        upload_artwork(item, b"x", "poster")
    assert not __import__("os").path.exists(item.uploaded_poster)
```

Replace the `__import__("os")` calls with a normal `import os` at the top of the file.

- [ ] **Step 2: Run test to verify it fails**

Run: `rtk proxy python -m pytest tests/test_plex_artwork.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'autoposter.plex.artwork'`

- [ ] **Step 3: Write the implementation**

Create `src/autoposter/plex/artwork.py`:

```python
"""Upload badged artwork to Plex.

plexapi's upload methods take a filepath rather than bytes, so the encoded
image goes to a temporary file that is removed on both the success and the
failure path.
"""
import logging
import os
import tempfile

logger = logging.getLogger(__name__)


def upload_artwork(plex_item, data: bytes, art_kind: str, lock: bool = True) -> None:
    """Upload one badged image and lock the field.

    An episode's badged image is its *poster*, not its art -- that is what Plex
    displays as the episode thumbnail -- so only ``background`` routes to
    ``uploadArt``.

    Locking matters: without it Plex's metadata agent can reclaim the field and
    replace what we just uploaded.
    """
    is_background = art_kind == "background"
    handle = tempfile.NamedTemporaryFile(suffix=".webp", delete=False)
    try:
        handle.write(data)
        handle.close()
        if is_background:
            plex_item.uploadArt(filepath=handle.name)
            if lock:
                plex_item.lockArt()
        else:
            plex_item.uploadPoster(filepath=handle.name)
            if lock:
                plex_item.lockPoster()
    finally:
        try:
            os.unlink(handle.name)
        except OSError:
            logger.warning("could not remove temporary upload file %s", handle.name)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `rtk proxy python -m pytest tests/test_plex_artwork.py -v`
Expected: PASS — 8 passed

- [ ] **Step 5: Confirm the refresh guard still passes, run ruff, commit**

```bash
rtk proxy python -m pytest tests/ -k refresh -v
rtk proxy ruff check src tests
git add src/autoposter/plex/artwork.py tests/test_plex_artwork.py
git commit --no-gpg-sign -m "Upload badged artwork to Plex and lock the field"
```

---

## Task 9: Pipeline and configuration

Wires the badge stage into the per-item pipeline behind config, gated on the badge fingerprint.

**Files:**
- Modify: `src/autoposter/config/schema.py`
- Modify: `config/autoposter.example.yaml`
- Modify: `src/autoposter/render/pipeline.py`
- Modify: `deploy/README.md`
- Test: `tests/test_badge_pipeline.py`

**Interfaces:**
- Consumes: `compose`, `badge_values`, `badge_fingerprint`, `MANIFEST_SHA` from `compose.py`; `media_info_from_plex` from `values.py`; `upload_artwork` from `artwork.py`.
- Produces: `apply_badges(session, config, render, item, plex_item, facts) -> None`.

**Config to add**, under a new `badges:` section:

```yaml
badges:
  enabled: true # composite badge overlays and upload them to Plex
  upload_to_plex: false # dry run by default: compose and fingerprint, upload nothing
  lock_artwork: true # lock the Plex field after upload so the agent cannot reclaim it
  apply_overlay_label: false # add the literal Plex label "Overlay", as the previous tool did
```

`upload_to_plex` defaults to **false** deliberately. This is the first thing on this project that writes images to the live Plex server across ~16,000 items, and the operator should compose, inspect and only then enable it — the same posture `operations.write_to_plex` already takes.

`apply_overlay_label` defaults to **false** and is a genuine behaviour change from the tool being replaced, which labels every overlaid item `Overlay`. We track overlay state in Postgres so we do not need it, but it is visible and filterable in Plex, so it is offered rather than silently dropped.

- [ ] **Step 1: Write the failing test**

Create `tests/test_badge_pipeline.py`:

```python
"""The badge stage inside the per-item pipeline."""
from pathlib import Path

from autoposter.badges.values import MediaInfo
from autoposter.db.models import MediaItem, Render
from autoposter.render.pipeline import apply_badges

ORACLE = Path("tests/fixtures/oracle")


class FakePlexItem:
    def __init__(self):
        self.media = [type("M", (), {"parts": [], "videoResolution": "1080",
                                     "audioCodec": "eac3", "audioChannels": 6})()]
        self.duration = 4845912
        self.seasonNumber = None
        self.episodeNumber = None
        self.uploads = 0

    def uploadPoster(self, url=None, filepath=None):
        self.uploads += 1

    def lockPoster(self):
        pass


class Facts:
    critic_rating = 4.9
    audience_rating = 6.3
    content_rating = "17"


async def _render(session, **kw):
    item = MediaItem(kind="movie", rating_key="1", title="X")
    session.add(item)
    await session.flush()
    render = Render(item_id=item.id, art_kind="poster",
                    asset_path=str(ORACLE / "All_Souls_base_no_overlay.jpg"),
                    base_sha256="abc", **kw)
    session.add(render)
    await session.flush()
    return item, render


async def test_badging_records_a_fingerprint_and_uploads(session, config_with_badges):
    item, render = await _render(session)
    plex_item = FakePlexItem()
    await apply_badges(session, config_with_badges, render, item, plex_item, Facts())
    assert render.badge_fingerprint is not None
    assert render.upload_status == "uploaded"
    assert plex_item.uploads == 1


async def test_an_unchanged_fingerprint_skips_the_upload_entirely(
    session, config_with_badges
):
    """This is what stops the upload bloat seen in production, where repeated
    runs left five accumulated uploads on every item."""
    item, render = await _render(session)
    plex_item = FakePlexItem()
    await apply_badges(session, config_with_badges, render, item, plex_item, Facts())
    first = render.badge_fingerprint
    await apply_badges(session, config_with_badges, render, item, plex_item, Facts())
    assert render.badge_fingerprint == first
    assert plex_item.uploads == 1


async def test_a_changed_rating_re_badges_and_re_uploads(session, config_with_badges):
    item, render = await _render(session)
    plex_item = FakePlexItem()
    await apply_badges(session, config_with_badges, render, item, plex_item, Facts())

    class Changed(Facts):
        critic_rating = 5.4

    await apply_badges(session, config_with_badges, render, item, plex_item, Changed())
    assert plex_item.uploads == 2


async def test_dry_run_composes_and_fingerprints_but_uploads_nothing(
    session, config_badges_dry_run
):
    item, render = await _render(session)
    plex_item = FakePlexItem()
    await apply_badges(session, config_badges_dry_run, render, item, plex_item, Facts())
    assert render.badge_fingerprint is not None
    assert render.upload_status == "skipped"
    assert plex_item.uploads == 0


async def test_disabled_badges_do_nothing_at_all(session, config_badges_disabled):
    item, render = await _render(session)
    plex_item = FakePlexItem()
    await apply_badges(session, config_badges_disabled, render, item, plex_item, Facts())
    assert render.badge_fingerprint is None
    assert plex_item.uploads == 0
```

Add three fixtures to `tests/conftest.py` returning a `Config` with `badges.enabled/upload_to_plex` set to `(True, True)`, `(True, False)` and `(False, False)` respectively, following the existing config-fixture pattern in that file.

- [ ] **Step 2: Run test to verify it fails**

Run: `rtk proxy python -m pytest tests/test_badge_pipeline.py -v`
Expected: FAIL with `ImportError: cannot import name 'apply_badges'`

- [ ] **Step 3: Add the config section**

In `src/autoposter/config/schema.py`, add a `BadgesConfig` model alongside the existing section models, with the four fields above and those defaults, and reference it from the top-level `Config` as `badges: BadgesConfig = BadgesConfig()`. Mirror the four fields into `config/autoposter.example.yaml` with the comments shown above.

- [ ] **Step 4: Write the pipeline function**

Add to `src/autoposter/render/pipeline.py`:

```python
async def apply_badges(session, config, render, item, plex_item, facts) -> None:
    """Badge one rendered artifact and upload it, if anything changed.

    The fingerprint gate is the point of this whole stage: an unchanged item
    costs one hash and no image work at all. It is also what stops uploads
    accumulating on the Plex server, which is what happens when every run
    uploads unconditionally.
    """
    if not config.badges.enabled:
        return
    # Backgrounds are never badged. The tool being replaced overlays posters,
    # season posters and episode title cards only -- a fanart backdrop with a
    # runtime badge stamped on it is not something it produces, and not
    # something we should start producing.
    if render.art_kind == "background":
        return

    from autoposter.badges.compose import (
        MANIFEST_SHA,
        BadgeInputs,
        badge_fingerprint,
        badge_values,
        compose,
    )
    from autoposter.badges.values import media_info_from_plex
    from autoposter.plex.artwork import upload_artwork

    media = media_info_from_plex(plex_item)
    inputs = BadgeInputs(
        media=media,
        critic_rating=getattr(facts, "critic_rating", None),
        audience_rating=getattr(facts, "audience_rating", None),
        content_rating=getattr(facts, "content_rating", None),
        video_format=None,
    )
    values = badge_values(render.art_kind, inputs)
    fingerprint = badge_fingerprint(
        render.base_sha256 or "", render.art_kind, values, MANIFEST_SHA
    )
    if fingerprint == render.badge_fingerprint and render.upload_status == "uploaded":
        return

    data = await asyncio.to_thread(compose, Path(render.asset_path), render.art_kind, inputs)
    render.badge_fingerprint = fingerprint

    if not config.badges.upload_to_plex:
        render.upload_status = "skipped"
        await session.flush()
        return

    try:
        await asyncio.to_thread(
            upload_artwork, plex_item, data, render.art_kind, config.badges.lock_artwork
        )
    except Exception:
        render.upload_status = "failed"
        await session.flush()
        logger.warning("badge upload failed for %s", item.rating_key, exc_info=True)
        return

    render.upload_status = "uploaded"
    render.uploaded_at = func.now()
    await session.flush()
```

Both `compose` and `upload_artwork` are synchronous and slow — Pillow work and an HTTP upload — so both go through `asyncio.to_thread` to keep the event loop free, as every other blocking call in this codebase does. Move the local imports to the module's import block; they are shown inline only to make the dependencies explicit.

- [ ] **Step 5: Add the background-skip test**

Add to `tests/test_badge_pipeline.py`:

```python
async def test_backgrounds_are_never_badged(session, config_with_badges):
    """The tool being replaced overlays posters, season posters and episode
    title cards only -- never fanart backdrops."""
    item = MediaItem(kind="movie", rating_key="2", title="Y")
    session.add(item)
    await session.flush()
    render = Render(item_id=item.id, art_kind="background",
                    asset_path=str(ORACLE / "All_Souls_base_no_overlay.jpg"),
                    base_sha256="abc")
    session.add(render)
    await session.flush()

    plex_item = FakePlexItem()
    await apply_badges(session, config_with_badges, render, item, plex_item, Facts())
    assert render.badge_fingerprint is None
    assert plex_item.uploads == 0
```

- [ ] **Step 6: Call it from `process_item`**

In `process_item`, after the artifact loop collects `results`, badge each rendered artifact. Wrap the whole block in `try`/`except` with `await session.rollback()` on failure and a warning log, matching how the metadata-operations block already contains its own failures — a badge failure must not cost the item its base artwork, which is already safely on disk by that point.

- [ ] **Step 7: Run the full suite**

Run: `rtk proxy python -m pytest tests/ -v`
Expected: PASS — 387 baseline plus roughly 110 new tests, 5 skipped

- [ ] **Step 8: Update the deployment docs**

In `deploy/README.md`, document the `badges` section: that `upload_to_plex` defaults to false and why, that enabling it uploads to every item in the library, that artwork is locked after upload, and that `apply_overlay_label` is off by default and differs from the previous tool's behaviour.

- [ ] **Step 9: Run ruff and commit**

```bash
rtk proxy ruff check src tests
git add src/autoposter config/autoposter.example.yaml deploy/README.md tests
git commit --no-gpg-sign -m "Wire badge rendering and Plex upload into the pipeline"
```

---

## Deferred, and why

- **The `Overlay` Plex label** is implemented as a config flag but defaults off. The previous tool applies it to every overlaid item; we track that state in Postgres instead. Turning it on later is cheap; having applied it to 16,000 items and wanting it gone is not.
- **Reaping accumulated uploads.** Production items carry roughly five orphaned `upload://` posters each from repeated unconditional uploads. Fingerprint-gated upload stops this getting worse, but removing the existing ones is destructive and needs an explicit decision plus a dry-run report first. Not in this phase.
- **The duplicated `languages` overlay** in the source config, whose second application is fully transparent and contributes nothing visible. We reproduce the observed output. Worth confirming whether the duplicate was intentional.
