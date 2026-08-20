# Kometa v2.4.8 Overlay Rendering Specification

Reverse-engineered from two sources, cross-checked against each other:

1. **Source of truth**: `Kometa-Team/Kometa` at git tag `v2.4.8`, extracted from the pinned
   production Docker image (`kometateam/kometa:v2.4.8`, the exact digest the production CronJob
   runs) — `defaults/overlays/*.yml`, `defaults/overlays/images/**/*.png`, `fonts/*.ttf`, and the
   Python source (`modules/overlay.py`, `modules/overlays.py`). This is authoritative: it is the
   literal code that produced the oracle files, not a later version.
2. **Pixel measurement**: `tests/fixtures/oracle/All_Souls_plex_overlaid.jpg` (WebP, 1000x1500,
   really a WebP despite the extension) and `Dune_Part_Two_plex_overlaid.jpg` (same), diffed
   against `All_Souls_base_no_overlay.jpg` (2000x3000 JPEG) resized to 1000x1500 with LANCZOS.

Where the two disagree, **the pixel measurement wins** and the disagreement is called out
explicitly. In every case checked here, the YAML/source-derived formula reproduced the measured
pixel geometry to within single-digit pixels — differences are attributable to JPEG/WebP
compression halos and to the diff-threshold method clipping anti-aliased edges, not to wrong
formulas. Two independent pixel-measurement passes (mine, threshold 20-30 with 6-15px dilation;
and a second pass from the coordinator, threshold 28 with 9x9 binary closing) were compared; both
converge on the same formula-derived numbers within that same few-pixel tolerance.

Confirmed directly from `tests/fixtures/oracle/All_Souls_plex_overlaid.jpg` via Pillow:
`im.format == "WEBP"`, `im.size == (1000, 1500)`, `im.getexif() == {1212: "overlay"}`
(1212 decimal = 0x04BC). This matches `modules/overlays.py` exactly (see §6).

---

## 1. The compositing algorithm (source: `modules/overlay.py`, `modules/overlays.py`)

### 1.1 Canvas sizes (`modules/overlay.py`)

```python
portrait_dim = (1000, 1500)   # movies, shows, seasons
landscape_dim = (1920, 1080)  # episodes
square_dim = (1000, 1000)     # music albums
```

`get_canvas_size(item)` picks `landscape_dim` for `plexapi.video.Episode`, `square_dim` for
`plexapi.audio.Album`, `portrait_dim` otherwise. All 8 target badge types render onto **1000x1500**
except `episode_info`, which is `builder_level: episode` and therefore always renders onto
**1920x1080** (unverified against pixels — no episode-card oracle image was available; see Open
Questions).

### 1.2 Base image load and resize (`modules/overlays.py`, inside `run_overlays`)

```python
with Image.open(poster.location if poster else has_original) as new_poster:
    exif_tags = new_poster.getexif()
    exif_tags[0x04BC] = "overlay"
    new_poster = new_poster.convert("RGB").resize((canvas_width, canvas_height), Image.Resampling.LANCZOS)
    if blur_num > 0:
        new_poster = new_poster.filter(ImageFilter.GaussianBlur(blur_num))
```

The base poster (whatever size it was fetched/uploaded at — 2000x3000 for the production movie
assets per the oracle) is opened, converted to RGB (dropping any alpha/CMYK), and resized with
**LANCZOS** to the canvas size **before any overlay is drawn**. Note: this happens on the
*original* poster, not necessarily on our 2000x3000 base JPEG specifically — but the coordinator's
independent check found that downscaling the 2000x3000 base with LANCZOS to 1000x1500 reproduces
the oracle's non-badge pixels closely (median per-pixel diff 2, 92.6% of pixels within 12 of the
oracle), confirming LANCZOS at this exact 2:1 ratio.

### 1.3 Per-overlay rendering (`Overlay.get_backdrop`, `modules/overlay.py:434-505`)

Each overlay produces a full-canvas-sized transparent `RGBA` layer, drawn onto with `ImageDraw`,
which is then composited onto the running poster with `paste(layer, (0,0), layer)` — i.e. plain
`Image.paste` using the layer's own alpha channel as the mask, **not** `Image.alpha_composite`.
Concretely, per overlay:

1. Compute `start_x, start_y` — the top-left corner of the *content box* — via `get_coordinates`
   (§2 below).
2. If the overlay `has_back` (a `back_color` or `back_line_color` is set): draw a backdrop rounded
   rectangle (or plain rectangle if `back_radius` is unset) at
   `(start_x - back_padding, start_y - back_padding, start_x + back_width + back_padding, start_y + back_height + back_padding)`
   using `ImageDraw.rounded_rectangle(..., radius=back_radius)` or `ImageDraw.rectangle(...)`.
3. If `back_align` (default `"center"`) repositions the *content* within the backdrop box when the
   content is smaller than the box (e.g. a 269x38 native-resolution image centered inside a
   305x105 backdrop — see the `1080p.png` example in §3).
4. If there's a text string plus an addon image (icon/logo), lay them out relative to each other
   per `addon_position` (`left`/`right`/`top`/`bottom`) and `addon_offset` — e.g. ratings puts the
   logo image **above** the number text (`addon_position: top`, vertical stacking) with `15px` gap.
5. Draw the text with `drawing.text((main_x, main_y), text, font=..., fill=font_color, stroke_fill=stroke_color, stroke_width=stroke_width, anchor="lt")`.
6. Composite the layer (`overlay_image`) onto the poster with `paste(overlay_image, (0,0), overlay_image)`, then separately paste the addon **image** (icon PNG) with `paste(current_overlay.image, addon_box, current_overlay.image)` at its own native pixel size — **images are pasted unscaled**, never stretched to fill their backdrop box.

### 1.4 Application order (`modules/overlays.py:365-425`)

- Non-queue overlays (`resolution`, `audio_codec`, `ratings` x2, `commonsense`, `video_format`,
  `runtimes`, `episode_info`) are applied first, in `applied_names` order — i.e. the order the
  overlay *files* were listed and matched for that item during `compile_overlays`. Collisions
  between mutually-exclusive options within one overlay type (e.g. which resolution wins, which
  audio codec wins) are resolved earlier via the `group`/`weight` mechanism: within a `group`, only
  the entry with the highest `weight` survives (`overlays.py:576-590`).
- **Queue-based overlays** (`languages`, via `queue: flags`) are applied *after* all non-queue
  overlays, iterating precomputed position slots (`self.library.queues[queue]`) in
  weight-descending order, one language per slot, up to `overlay_limit: 3` slots (from
  `languages.yml`'s `queues.flags` block). Two overlays sharing the same `queue` and the same
  `weight` value raise `OverlayError: Overlays in a queue cannot have the same 'weight' value`
  (`overlays.py:120-122`) — relevant to the double-`languages` question, §8.

### 1.5 Coordinate math (`Overlay.get_coordinates`, `modules/overlay.py:530-552`) — exact formula

```python
def get_cord(value, image_value, over_value, align):
    value = int(image_value * 0.01 * int(value[:-1])) if str(value).endswith("%") else value
    if align in ["right", "bottom"]:
        return image_value - over_value - value
    elif align == "center":
        return int(image_value / 2) - int(over_value / 2) + value
    else:  # "left" / "top"
        return value
```

`image_value` = canvas width/height (1000/1500 for posters). `over_value` = the box's own
width/height (the `back_box` width/height when a `back_width`/`back_height` is set, which is the
case for all 8 badges here). `value` = the configured offset.

- `left`/`top`: the offset **is** the pixel coordinate directly.
- `right`/`bottom`: `canvas_dim - box_dim - offset` (box's far edge sits `offset` px inside the canvas edge).
- `center`: `canvas_dim/2 - box_dim/2 + offset` (offset shifts right/down from dead-center).

---

## 2. Per-badge geometry (measured-and-derived, 1000x1500 canvas)

All numbers below are the **backdrop box** top-left `(x, y)` and size, computed from
`get_cord()` above using each YAML file's resolved defaults, then confirmed against the two oracle
images. "Content" (image/text) may sit centered or offset *inside* that backdrop box per
`back_align` — see §3 for exact content placement within resolution/audio_codec.

| # | Badge | h_align | h_offset | v_align | v_offset | back_width x back_height | back_padding | Computed backdrop box (x0,y0)-(x1,y1) | Pixel match |
|---|---|---|---|---|---|---|---|---|---|
| 1 | `resolution` | left | 15 | top | 15 | 305x105 | 0 | (15,15)-(320,120) | Exact (content sub-box matched to the pixel, see §3) |
| 2 | `audio_codec` | center | 0 | top | 15 | 305x105 | 0 | (348,15)-(653,120)* | Exact (content sub-box matched to the pixel, see §3) |
| 3 | `ratings` (rating1, critic/imdb) | right | 30 | center | -105 | 160x160 | 15 | (795,550)-(985,740) | Within ~10-15px (two independent pixel passes: 790-991/545-746 and 795-963/576-740) |
| 4 | `ratings` (rating2, audience/tmdb) | right | 30 | center | +105 | 160x160 | 15 | (795,760)-(985,950) | Within ~10-15px (790-990/755-956 and 795-961/760-923) |
| 5 | `commonsense` | left | 15 | bottom | **270** (not 30 — see note) | 305x105 | 0 | (15,1125)-(320,1230) | Exact on x1=320; y within ~7-12px of both measured passes |
| 6 | `video_format` | left | 15 | bottom | 30 | 305x105 | 0 | (15,1365)-(320,1470) | Consistent (text glyphs measured at x 87-246, inside this box) |
| 7 | `runtimes` | right | 15 | bottom | 30 | 600x105 | 0 | (385,1365)-(985,1470) | Consistent (text glyphs measured up to x~900; box itself not fully visible against dark backdrop) |
| 8 | `languages` (flag queue, slot 1) | left | 15 | top | 223 | 190x105\*\* | 0 | (15,223)-(205,328)\*\* | y within ~10-15px of measured 216-284/223-283 |

\* `int(1000/2) - int(305/2) + 0 = 500 - 152 + 0 = 348`.

\*\* Languages: `back_width` defaults to 190 (not "big", not "hide_text", not "three_characters"),
`back_height` is **unset by default** (`None`/auto — sized to content) unless `size: big`. But see
§3.4 and §8: the languages badge as observed in the oracle images has **no visible backdrop at
all**, so this box's dimensions cannot be pixel-confirmed independently of the flag+text content.

**`commonsense` vertical_offset note**: `commonsense.yml`'s `vertical_offset` conditional block is:
```yaml
vertical_offset:
  default: 15
  conditions:
    - vertical_align.exists: false
      value: 270
    - vertical_align: center
      value: 0
    - vertical_align: top
      value: 15
    - vertical_align: bottom
      value: 30
```
and the file's own `default:` block sets `vertical_align: bottom`. A naive reading says this should
resolve to offset **30** (the explicit `bottom` branch), which would place `commonsense` in the
same bottom band as `video_format`/`runtimes` (y≈1365) — but that is **not** where the badge is in
either oracle image; it sits at y≈1118-1237, matching offset **270** (the `vertical_align.exists:
false` branch) almost exactly (`1500 - 105 - 270 = 1125`, within a few px of both independent pixel
measurements). We could not fully resolve *why* the `exists: false` branch fires when the file's
own template sets `vertical_align: bottom` — plausibly a template-variable evaluation-order effect
in Kometa's YAML templating engine where the conditional evaluates before the template's own
`default:` block populates `vertical_align`. **Trust 270, not 30** — it matches the pixels in both
oracle images. `video_format.yml`, by contrast, has *no* `exists: false` branch in its own
`vertical_offset` conditional at all, so it isn't subject to this and correctly resolves to 30
(confirmed by pixels — WEB/Runtime sit in the true bottom band).

**`episode_info.yml` has the identical `exists: false` → 150 branch pattern as commonsense's → 270
pattern** (same shape, different constant). By analogy, `episode_info` likely resolves to
`vertical_offset = 150`, not 30 — but this is **unconfirmed**, since no episode-card oracle image
was available to check against. Flagged in Open Questions.

---

## 3. Backdrop, images, and content placement per badge

Background color for every badge except `languages` (see §8): **`back_color: "#00000099"`** — RGB
`(0,0,0)`, alpha `0x99` = 153/255 ≈ **60% opacity black**, drawn as a **rounded rectangle**,
**`back_radius: 30`** (26 for `languages`, not that it matters — see §8). The backdrop is drawn
*only if* a `back_color` (or `back_line_color`) is set — `Overlay.has_back` — which is true for all
8 badges per their YAML defaults.

### 3.1 `resolution` — image-only overlay (no live text)

**This is a critical, non-obvious finding: `resolution` and `audio_codec` are NOT rendered as text
+ icon. Each resolution/format combination is a single, pre-baked PNG containing both the icon and
the full label text ("1080P FHD" is one image, not "1080P" text drawn next to an icon image).**

Confirmed by opening `defaults/overlays/images/resolution/1080p.png` (269x38 px, RGBA) — it
contains the rendered glyphs "1080P FHD" already, at that native small resolution. Kometa does not
scale this image up; it pastes it at native size, centered inside the 305x105 backdrop box via
`back_align: "center"` (the default, unset in `resolution.yml` for plain resolution/edition):

```
main_x = start_x + (back_width - image_width) // 2  = 15 + (305-269)//2 = 15 + 18 = 33
main_y = start_y + (back_height - image_height) // 2 = 15 + (105-38)//2 = 15 + 33 = 48
```

Content box: **(33,48)-(302,86)**. Measured pixel diff clusters for the "1080P FHD" glyphs:
x≈15-302 (left edge is the backdrop's 15, text visually starts a little in), y≈48-85 — **matches
the computed (33,48)-(302,86) essentially exactly** (the x=15 left extent in the raw diff includes
the backdrop rectangle edge itself, not just glyphs).

Image asset naming: `resolution/<<key>><<alt>>.png`, e.g. `resolution/1080p.png` (key=1080p, no
HDR/DV suffix), `resolution/4kdvhdrplus.png`, `edition/imax.png`. 43 files under `resolution/`, 25
under `edition/` in the pinned image.

Suppression: an overlay is only created for items actually matching its `plex_search`/`filters`
regex (`ignore_blank_results: true`), and within the shared `group: resolution` (or `edition`),
only the highest-`weight` match survives — so an item never gets more than one resolution badge and
never gets a badge for a resolution/edition it doesn't have.

### 3.2 `audio_codec` — also image-only

Same mechanism. `style: compact` (the file default; `standard` is the alternative, taller style
used nowhere in our config) picks images from `audio_codec/compact/<<key>>.png`. Confirmed:
`audio_codec/compact/plus.png` is 194x73 (contains "DOLBY DIGITAL+"), matching the All_Souls
example; `audio_codec/compact/plus_atmos.png` is 270x38 and (per the Dune example) renders "DD+"
in white immediately followed by "ATMOS" in a blue-to-cyan gradient wordmark — that gradient is
baked into the PNG (it's the Dolby Atmos logotype), not a font effect.

`back_width: 305, back_height: 105` (the file sets `standard_value: 105`; the `back_height`
conditional only overrides to `standard_value` when `style: standard`, which we don't use, so
`back_height` stays the flat default 105 regardless of one-line vs two-line labels — the two-line
"DOLBY / DIGITAL+" glyphs are simply baked smaller into the PNG to fit).

Content placement, `back_align: center` (default): for `plus.png` (194x73):
```
main_x = 348 + (305-194)//2 = 348 + 55 = 403
main_y = 15 + (105-73)//2 = 15 + 16 = 31
```
Content box **(403,31)-(597,104)** — matches the measured glyph-cluster extents (x≈403-599,
y≈15-107, allowing for the diff method catching the backdrop edge too) almost exactly.

17 files under `audio_codec/compact/`, 17 under `audio_codec/standard/`.

Suppression: same `ignore_blank_results` + weight-based `group: audio_codec` mechanism — no badge
if no audio track title/filepath matches any of the codec regexes.

### 3.3 `ratings`

**Image**: a logo PNG selected via `rating<<n>>_image` — production config sets `rating1_image:
imdb` and `rating2_image: tmdb` explicitly (overriding the `critic`/`audience` generic icons that
`rating1: critic` / `rating2: audience` would otherwise select). Confirmed present:
`rating/IMDb.png` (149x75) and `rating/TMDb.png` (144x75), matching what's visible in both oracle
images (yellow IMDb wordmark, teal/blue TMDb pill logo).

**Text**: font `fonts/Inter-Bold.ttf`, size **63**, color `#FFFFFF`, no stroke. `addon_position:
top` (the default for `rating_alignment: vertical`, which is the un-overridden default — logo
drawn above the number, `addon_offset: 15` gap).

**String format** — traced through `modules/overlays.py`'s `get_text()` modifier chain:
- **Critic (rating1, image=imdb, no `%`-style)**: the overlay name resolves to
  `text(<<critic_rating>>)` with **no format modifier at all**. The value is Plex's raw
  `criticRating` field, used **completely unformatted** (`final_value = actual_value`, the
  fallback branch — critic_rating is not one of Kometa's specially-formatted `rating_sources`).
  The clean one-decimal look ("4.9", "8.4") comes from Plex already storing that field at one
  decimal precision, not from any Kometa-side rounding. **If your Plex/metadata source ever
  returns a critic rating with more precision, Kometa would print it exactly as-is — there is no
  `.1f`-style rounding applied for this field.**
- **Audience (rating2, image=tmdb, in the `%`-suffix image list)**: resolves to
  `text(<<audience_rating%>>%)` (note: the `%` is embedded twice — once inside the template
  variable name as a modifier flag, once as a literal trailing character). Modifier `%` triggers
  `final_value = int(float(actual_value) * 10)` — i.e. Plex's `audienceRating` (0-10 scale, e.g.
  `6.3`) becomes `63`, then the literal `%` is appended: **`"63%"`**. This *is* explicitly computed
  by Kometa (multiply by 10, truncate to int), unlike the critic rating.

**Position**: `horizontal_position: right` (config) forces `horizontal_align: right` for both
rating1/rating2, and since `rating_alignment` defaults to `vertical` and no `horizontal_position:
center` applies, `horizontal_offset` falls through every conditional branch to the flat default
`standard_offset = 30` for both. `vertical_position` defaults to `center`; with 2 ratings configured
(no `rating3`), `rating1_vertical_offset = -cv2_offset = -105`, `rating2_vertical_offset =
+cv2_offset = +105` — i.e. the pair sits symmetrically 105px above/below true vertical center
(750), which is exactly what's measured (rating1 center ≈645, rating2 center ≈855).

`back_width/back_height: 160x160` (vertical-alignment default, not the 270x80 used for
`rating_alignment: horizontal`), `back_padding: 15`. Computed padded backdrop:
rating1 `(795,550)-(985,740)`, rating2 `(795,760)-(985,950)`. Two independent pixel-diff passes
measured `(790,545)-(991,746)`/`(790,755)-(990,956)` and `(795,576)-(963,740)`/`(795,760)-(961,923)`
respectively — both bracket the computed box within roughly 10-15px, consistent with
threshold-based diffing under/over-shooting anti-aliased edges rather than a real formula error;
**x0=795 matches exactly in both passes**.

**Suppression**: `run_this` conditional — for `builder_level: episode`, only
`audience/critic/user/tmdb/imdb/serializd/floppy` ratings run at all (not e.g. `mdb`); for
`builder_level: season`, only `user/tmdb`. At movie level (our case), the badge is skipped if the
rating source has no value, or if it's outside `[minimum_rating, maximum_rating]` = `[0.0, 10.0]`
(effectively always in range for real ratings).

### 3.4 `video_format`, `runtimes`, `commonsense`, `episode_info` — text badges with an optional addon icon

- **`video_format`**: pure text, no addon image. `final_name: text(<<text_<<key>>>>)`, and
  `text_<<key>>` defaults to `<<overlay_name>>` — i.e. the literal overlay key (`WEB`, `REMUX`,
  `BLU-RAY`, etc.) is the displayed string verbatim, no case transform. Font: Inter-Medium 55pt
  white (the `standard` template default — not overridden). `back_width/height: 305x105`.
- **`runtimes`**: pure text. `text: "Runtime: "`, `format: "<<runtimeH>>h <<runtimeM>>m"` →
  displayed string is exactly `"Runtime: {H}h {M}m"`, e.g. `"Runtime: 1h 20m"`. `H` = `int((duration_ms/60000)//60)`,
  `M` = `int((duration_ms/60000)%60)` — no zero-padding on either number. `back_width/height: 600x105`.
- **`commonsense`**: text + addon icon. `final_name: text(<<pre>><<overlay_name>><<post>>)`,
  `post_text: "+"` (empty for the `NR` entry) — e.g. content-rating key `"17"` → displayed `"17+"`;
  `NR` → displayed `"NR"` (no `+`). Addon icon: `Commonsense.png` (92x83, the green
  checkmark-in-circle), `addon_position: left`, `addon_offset: 15`. `back_width/height: 305x105`.
- **`episode_info`**: pure text. `final_name: text(S<<season_number0>>E<<episode_number0>>)`,
  modifier `0` on both numbers → zero-padded to 2 digits (`f"{n:02}"`) — e.g. **`"S01E01"`**.
  `back_width/height: 305x105`. `builder_level: episode`, canvas 1920x1080 (not 1000x1500).

All four: font Inter-Medium.ttf 55pt `#FFFFFF`, no stroke, `back_color: "#00000099"`, `back_radius:
30`, `back_padding: 0` (unset in each of these 4 files → `Overlay.__init__` default of 0).

**Suppression**: `video_format`/`commonsense` are skipped when nothing matches their filter/rating
regex (`ignore_blank_results: true`); `runtimes` runs unconditionally (`plex_all: true`, no
filter) but the badge is effectively absent if the item has no duration; `episode_info` only
applies at `builder_level: episode` (never on a movie or show/season poster).

---

## 4. Font inventory (bundled in the repo/image, at `fonts/`)

| Font file | Used by | Size |
|---|---|---|
| `fonts/Inter-Medium.ttf` | `standard` template default — `video_format`, `runtimes`, `commonsense`, `episode_info` | 55pt |
| `fonts/Inter-Bold.ttf` | `ratings` (63pt), `languages` (50pt default, 70pt if `size: big`) | 63pt / 50pt |

Both confirmed present at 307-309KB each (variable fonts — the code additionally supports
`font_style` variation-axis selection, e.g. Thin/Light/SemiBold/Black weights, unused by any of
our 8 badges). `resolution` and `audio_codec` render **no live text at all** (§3.1-3.2) so no font
applies to them.

---

## 5. Output encoding (`modules/overlays.py:426-433`) — confirmed

```python
ext = "webp" if self.library.overlay_artwork_filetype.startswith("webp") else self.library.overlay_artwork_filetype
temp = os.path.join(self.library.overlay_folder, f"temp.{ext}")
if self.library.overlay_artwork_quality and self.library.overlay_artwork_filetype in ["jpg", "webp_lossy"]:
    new_poster.save(temp, exif=exif_tags, quality=self.library.overlay_artwork_quality)
elif self.library.overlay_artwork_filetype == "webp_lossless":
    new_poster.save(temp, exif=exif_tags, lossless=True)
else:
    new_poster.save(temp, exif=exif_tags)
```

- **Format/dimensions**: WebP, 1000x1500 for movies/shows/seasons (confirmed: oracle file is
  exactly this). Filetype (`jpg` / `webp_lossy` / `webp_lossless` / plain PNG-default) and quality
  are library-level config (`overlay_artwork_filetype`, `overlay_artwork_quality`), not hardcoded —
  production is evidently configured for lossy WebP with some quality setting (the oracle file is
  199,122 bytes for a 1000x1500 image, consistent with lossy compression at a moderate-to-high
  quality; **the exact quality integer could not be determined from the file alone** — see Open
  Questions).
- **EXIF**: `exif_tags[0x04BC] = "overlay"` — set on the **original** poster's EXIF dict (via
  `new_poster.getexif()` before the resize/convert), then passed through to `.save(..., exif=exif_tags)`.
  Confirmed byte-for-byte against the oracle: `Image.open(...).getexif() == {1212: "overlay"}`
  (1212 decimal = 0x04BC). This tag is Kometa's own marker to detect "already overlaid" posters on
  a later run and skip re-processing.
- Mode: image is `.convert("RGB")` before saving — no alpha channel in the final output (matches
  oracle: `im.mode == "RGB"`).

---

## 6. The `languages` double-application

**What's configured** (per the task brief): `languages` is applied twice at movie level (and
similarly at show level) — once with default colors, once with `back_color` and `font_color` both
set to `#FFFFFF00` (fully transparent).

**What's actually in the pixels** (both oracle images, checked independently): a single flag PNG
(e.g. `flag/round/us.png`, 102x60, RGBA — the flag already has its own rounded corners and white
border baked into the asset) immediately followed by 2-letter text (`"EN"`) in solid opaque white,
**with no dark backdrop rectangle behind either element** — unlike every other badge (resolution,
audio_codec, ratings, commonsense, video_format, runtimes all show a visible ~60%-opacity black
rounded-rect backdrop; languages shows none, confirmed by direct visual inspection of a 4x-zoomed
gridded crop of both oracle files).

**This directly contradicts the naive expectation.** `languages.yml`'s own file-level default sets
`back_color: "#00000099"` (visible), so *whichever* of the two configured applications is the one
actually producing the visible flag+text must, at render time, have a transparent `back_color` —
not the "first, default-colored" one as the task brief's phrasing implies. We could not fully
resolve this from the YAML/source alone, for two structural reasons:

1. `languages.yml`'s `flags` queue enforces **unique weights per queue** — two overlays in the same
   queue with the same `weight` raise `OverlayError` (`modules/overlays.py:120-122`). If both
   `languages` applications used the *same* `queue: flags` and the *same* per-language weights
   (`english: weight 610` in both), applying both to an item with English audio would crash the
   run — which evidently isn't happening in production. So the two applications must differ in at
   least one of: `queue` name, per-language `weight`, or which items they match at all (most
   plausibly: `use_subtitles: true` on the second one, switching `search_attribute` from
   `audio_language` to `subtitle_language` — this is a documented `languages.yml` toggle that also
   flips default alignment to `right`). That would make the two applications genuinely
   *independent* — one for audio-language flags, one for subtitle-language flags — not a layered
   "redraw the same thing twice" trick.
2. We do not have the actual production `overlay_files:`/config block for the second `languages`
   entry — only the task brief's summary ("same overlay, back_color and font_color transparent").
   Without seeing its `use_subtitles`, `horizontal_position`/`vertical_position`, or `queue`
   overrides, we can't derive its exact effect from the YAML alone.

**What we can say with confidence, for reproduction purposes**: the visually-observable output is
simply *one* flag icon (opaque, native size, no scaling — same "paste unscaled" behavior as
resolution/audio_codec) plus *one* 2-letter uppercase text label (Inter-Bold 50pt, opaque white,
no backdrop) at position `(15, 223)` (top-left corner), left-aligned, top-anchored, per
`languages.yml`'s `queues.flags.dynamic_position` defaults (`initial_horizontal_offset: 15,
initial_vertical_offset: 223` — the un-overridden defaults, since neither oracle poster shows any
sign of a `horizontal_position`/`vertical_position` override). If a title has multiple
audio-language tracks, subsequent flags stack **61px apart vertically**
(`vertical_spacing: 61` for `group_alignment: vertical`, the default), up to `overlay_limit: 3`
slots. **Whatever the second, fully-transparent `languages` application is for, it contributes
nothing to the visible pixels in either oracle image** — treat it as a no-op for the purpose of
reproducing the visual output, and flag it in your own config as something to investigate
separately (it may be doing something purely Plex-side, e.g. reserving a queue slot or affecting
the "Overlay" label/cache bookkeeping, that has no pixel effect).

---

## 7. Image assets we will need (upstream: bundled inside `Kometa-Team/Kometa` at
`defaults/overlays/images/`, tag `v2.4.8` — not a separate assets repo; not downloaded at runtime
unless a `git:`/`repo:`/`url:` override is used, which none of these 8 badges have)

Confirmed via `modules/overlay.py:_resolve_image_path`: default/`pmm`-style paths resolve to
`<repo_root>/defaults/overlays/images/<path>.png` and raise `OverlayError` if missing there — i.e.
these ship in the repo/Docker image.

| Category | Path pattern | Files needed | Count |
|---|---|---|---|
| Resolution | `defaults/overlays/images/resolution/<key><alt>.png` | All (any resolution+HDR/DV combo could appear); e.g. `1080p.png`, `4kdvhdrplus.png`, `dv.png` | 43 |
| Edition | `defaults/overlays/images/edition/<key>.png` | All, if editions are ever surfaced (not in our 8-badge list, but `resolution.yml` bundles them in the same file — skip unless `edition`-type overlays are separately enabled) | 25 |
| Audio codec | `defaults/overlays/images/audio_codec/compact/<key>.png` | All (`style: compact` is what's configured); e.g. `plus.png`, `plus_atmos.png`, `truehd_atmos.png`, `dtsx.png` | 17 |
| Audio codec (unused style) | `defaults/overlays/images/audio_codec/standard/<key>.png` | Not needed — `style: standard` is not used in production config | 17 (skip) |
| Ratings | `defaults/overlays/images/rating/IMDb.png`, `defaults/overlays/images/rating/TMDb.png` | Exactly these 2 (config pins `rating1_image: imdb`, `rating2_image: tmdb`) | 2 |
| Commonsense | `defaults/overlays/images/Commonsense.png` | 1 file (single checkmark icon, reused for every rating tier 1-18 and NR) | 1 |
| Languages (flags) | `defaults/overlays/images/flag/round/<country>.png` | All languages configured as `use_<<key>>: true` — the default flags list in `languages.yml` is `["en", "de", "fr", "es", "pt", "ja"]` unless the production config overrides `languages:`; safest to vendor all 198 unless the production config's language list is confirmed narrower | 198 (or as narrow as the confirmed `languages:` list allows) |
| Languages (unused style) | `defaults/overlays/images/flag/square/<country>.png` | Not needed — `style: round` is the file default and nothing overrides it in evidence | 198 (skip) |

Exact filenames confirmed present and measured: `resolution/1080p.png` (269x38),
`audio_codec/compact/plus.png` (194x73), `audio_codec/compact/plus_atmos.png` (270x38),
`rating/IMDb.png` (149x75), `rating/TMDb.png` (144x75), `flag/round/us.png` (102x60),
`Commonsense.png` (92x83).

---

## 7a. RESOLVED after this document was first written

An episode-card oracle pair was harvested from production Plex and committed
(`tests/fixtures/oracle/8OO10C_S01E01_base_no_overlay.jpg`, 3840x2160 JPEG, our Phase 1 base;
and `8OO10C_S01E01_plex_overlaid.jpg`, 1920x1080 WebP carrying EXIF `0x04BC = "overlay"`).
Diffing the LANCZOS-downscaled base against the overlaid output yields these boxes:

| Measured box (x0..x1, y0..y1) | w x h | Badge | Predicted formula | Verdict |
|---|---|---|---|---|
| 14..320, 14..121 | 307x108 | `resolution` | left 15 / top 15, 305x105 | confirmed |
| 808..1113, 15..120 | 306x106 | `audio_codec` | `int(1920/2)-int(305/2)=808`, top 15 | confirmed to the pixel |
| 1715..1905, 549..740 | 191x192 | `ratings` (audience) | right 15, 190x190 incl. `back_padding:15`, centre +105 | confirmed |
| 1600..1905, 825..930 | 306x106 | `episode_info` | right 15, bottom **150** -> `1080-105-150 = 825` | **confirmed exactly** |
| 15..320, 945..1049 | 306x105 | `video_format` | left 15, bottom 30 -> `1080-105-30 = 945` | confirmed |
| 1305..1905, 945..1050 | 601x106 | `runtimes` | right 15, 600x105 -> `1920-600-15 = 1305` | confirmed |

This settles three of the open questions below:

- **Q1 `episode_info` vertical offset — RESOLVED.** It is **150**, not 30. The measured y0 is
  825, exactly `1080 - 105 - 150`. The `vertical_align.exists: false` branch does fire, so the
  same reasoning already applied to `commonsense` (270) is correct for `episode_info` too.
- **Q3 `ratings` backdrop box — RESOLVED.** The box measures 191x192 against a bright episode
  still, i.e. **190x190**, confirming the `160x160 + back_padding 15` formula. The earlier
  165x165 poster measurement was threshold noise on a dark poster, not a smaller box.
- **Q4 WebP quality — RESOLVED, and not by pixels.** Kometa's own defaults settle it:
  `modules/config.py` sets `overlay_artwork_filetype` default `"webp_lossy"` and
  `overlay_artwork_quality` default **90**, and the production `config.yml` overrides neither.
  So the encode is `img.save(path, exif=exif, quality=90)` in WebP. No empirical tuning needed.

Also newly confirmed: **badges are suppressed when their value is absent.** Only one of the two
`ratings` badges appears on this card. Its centre sits at y~644.5 against a canvas centre of 540,
i.e. offset +105 — the *audience* (TMDB) badge. The critic (IMDb) badge at -105 is simply absent,
because this episode has no IMDb rating. That is the suppression rule visible in the wild.

And resolving Q6: the `resolution` and `audio_codec` PNGs are **not** uniformly sized. Across the
vendored set, `resolution/` spans widths 95..305 and heights {35,38,39,40,46,47,49,53,105} and
`audio_codec/` spans widths 132..272 and heights {38,46,47,48,49,70,73,76,79,81,133,135}. Two
`resolution` images are exactly 305x105 (the full backdrop box) and two `audio_codec` images are
133-135 tall — i.e. **taller than the 105px backdrop**, so content can overflow its backdrop.
Centre the image in the backdrop box at native size and do not clip.

---

## 8. Open questions (unresolved — do not guess past this point)

1. **`episode_info` vertical offset**: `episode_info.yml` has the same `vertical_align.exists:
   false → 150` conditional shape as `commonsense.yml`'s `→ 270` (§2's note). By analogy it likely
   resolves to `vertical_offset = 150` rather than the naively-read `30`, giving a backdrop at
   `(1920-305-15, 1080-105-150) = (1600,825)` on the 1920x1080 episode canvas rather than
   `(1600,945)`. **We have no episode-card oracle image to confirm this against pixels** — treat
   150 as a reasoned-but-unverified default and prioritize getting a real episode-card sample
   before implementing.
2. **The `languages` double-application's actual purpose** (§6) — we can reproduce the visible
   pixel output (one flag + one label, no backdrop, position `(15,223)`), but don't know what the
   second, invisible application is actually *for* (subtitle flags on a separate queue? a
   deliberate visual no-op? something Plex-side only?) without the real production config for that
   second entry. Low risk to the parity goal (it contributes zero visible pixels in both samples)
   but worth a follow-up if a title with subtitle tracks produces unexpected artifacts.
3. **Exact `ratings` backdrop box in pixels**: the formula gives a 190x190 padded box
   (`(795,550)-(985,740)` for rating1), but two independent pixel-diff passes measured boxes
   ranging from 165x165 to 201x201 depending on threshold/method — all consistent with 190x190
   under measurement noise, but we could not pin the exact edge to better than ~±15px purely from
   pixels. If a byte-exact parity test fails specifically on this badge's backdrop rectangle, trust
   the 190x190/back_padding=15 formula first and re-measure with a lower diff threshold before
   assuming the formula is wrong.
4. **Output WebP quality setting**: confirmed lossy WebP (not lossless — file size and compression
   artifacts both indicate this) but the exact `overlay_artwork_quality` integer used in production
   could not be recovered from the file alone; Pillow's WebP encoder quality parameter would need
   to be tuned/matched empirically against the oracle file size and a visual/SSIM comparison.
5. **`back_color`/`font_color` alpha for the *visible* backdrop rectangles**: pixel-level alpha
   estimation (comparing base-vs-overlaid pixel values to back out the blend alpha) was attempted
   but proved unreliable — both source images are very dark in the badge regions (base pixel values
   near 0), so 8-bit quantization plus JPEG/WebP compression noise dominates any per-pixel alpha
   estimate. We rely on the YAML's literal `#00000099` (60% opacity black) for all 6 badges that
   show a visible backdrop, which is visually consistent with both oracle images (especially
   clearly visible against Dune's bright orange poster) but was not independently confirmed to the
   alpha value by pixel math.
6. **Whether `resolution`/`audio_codec`/`edition` PNGs ever need runtime scaling**: confirmed they
   are pasted at native pixel size for the two examples we have (1080p, Dolby Digital Plus, Dolby
   Digital Plus Atmos) — but did not check every one of the 43+17 variant images for consistent
   native sizing (e.g. whether `4kdvhdrplus.png`, a longer label, is a wider image or a
   smaller-font same-width image). Recommend spot-checking a few more before assuming uniform
   sizing conventions across the whole set.
