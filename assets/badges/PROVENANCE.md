# Badge asset provenance

These files are copied verbatim from the Kometa container image that the deployment
being replaced runs, so badge output matches it exactly rather than approximately.

**Source image**

    docker.io/kometateam/kometa:v2.4.8
    sha256:c58f6d4af511613f218b6dafbfc84078af4e5a6089790c1fdba58fd7c5dad70a

That is the exact digest pinned by the `kometa` CronJob in the `media` namespace, not a
floating tag, so this set is reproducible.

**What was taken**

| Path in image | Path here | Files | Used by |
|---|---|---|---|
| `/defaults/overlays/images/resolution` | `images/resolution` | 43 | resolution badge |
| `/defaults/overlays/images/audio_codec` | `images/audio_codec` | 34 | audio codec badge |
| `/defaults/overlays/images/rating` | `images/rating` | 36 | IMDb critic / TMDB audience badges |
| `/defaults/overlays/images/flag` | `images/flag` | 396 | languages badge |
| `/defaults/overlays/images/Commonsense.png` | `images/Commonsense.png` | 1 | Common Sense badge |
| `/fonts/Inter-Bold.ttf` | `fonts/Inter-Bold.ttf` | 1 | ratings 63pt, languages 50pt |
| `/fonts/Inter-Medium.ttf` | `fonts/Inter-Medium.ttf` | 1 | video_format, runtimes, commonsense, episode_info — all 55pt |
| `/defaults/overlays/images/cr` | `images/cr` | 98 | the six content-rating regional overlay families |
| `/defaults/overlays/images/Direct-Play.png` | `images/Direct-Play.png` | 1 | the direct_play overlay family |
| `/defaults/overlays/images/versions.png` | `images/versions.png` | 1 | the versions overlay family |
| `/defaults/overlays/images/dual_audio.png` | `images/dual_audio.png` | 1 | the language_count overlay family |
| `/defaults/overlays/images/multi_audio.png` | `images/multi_audio.png` | 1 | the language_count overlay family |

Deliberately **not** taken: `edition/`, `network/`, `ribbon/`, `streaming/`, `studio/` — the
overlay families this deployment does not yet enable (roadmap row 100, sub-phases C2 and C3).
`cr/`, `Direct-Play.png`, `versions.png` and the two `*_audio.png` files WERE deliberately not
taken until the overlay era's sub-phases C1, C2a and C2b shipped the families that draw them.
`dual_subs.png` and `multi_subs.png` are still untaken: `language_count`'s subtitle pair is
`use_subtitles: true` upstream, a template variable this service does not resolve (it ships flat
definitions), so only the audio pair is a shipped family. The subtitle STREAM LANGUAGES are
collected all the same (`badges/values.py::MediaInfo.subtitle_stream_languages`), so that family
is a one-commit follow-up.

**To reproduce this copy**

```sh
IMG="docker.io/kometateam/kometa@sha256:c58f6d4af511613f218b6dafbfc84078af4e5a6089790c1fdba58fd7c5dad70a"
CID=$(docker create --entrypoint sh "$IMG")
docker cp "$CID:/defaults/overlays/images" ./_images
docker cp "$CID:/fonts/Inter-Bold.ttf" ./
docker rm -f "$CID"
```

**Licensing**

The Kometa image ships no `LICENSE` file. Kometa's own code is open source, but a number
of these images are third-party marks that are not Kometa's to sublicense — Dolby and DTS
in `audio_codec/`, the IMDb and TMDB logos in `rating/`, and the Common Sense Media mark.
Country flags in `flag/` are not generally a concern.

Committing them here mirrors what the existing Kometa deployment already does on this
private, single-operator install. **If this repository is ever published, replace this
directory with a build-time fetch** rather than shipping the marks in version control.

## `languages.json`

Generated, not copied. Extracted from `/defaults/overlays/languages.yml` in the same image:
each language entry there carries `{key, text, weight, country}`, where `country` selects the
flag PNG and defaults to `key` when absent (so `de` uses `de.png`, but `en` uses `us.png`,
`ja` uses `jp.png`, `ko` uses `kr.png`, `zh` uses `cn.png`, `da` uses `dk.png`).

79 languages, every one of which resolves to a flag file present in `images/flag/round/`.
`weight` is Kometa's queue ordering: higher weight is drawn in an earlier slot.

## `OVERLAY-MANIFEST.sha256` — a second manifest, on purpose

`MANIFEST.sha256` covers the 513 files the nine BUILT-IN badges draw, and
`badges/compose.py::manifest_sha()` folds its hash into every item's
`badge_fingerprint` so that replacing any of them re-badges the library.

The overlay-family art (`images/cr/`, `images/Direct-Play.png`,
`images/versions.png`, `images/dual_audio.png`, `images/multi_audio.png`) is
checksummed separately and is **not** in that hash. Adding it there would have moved
`manifest_sha()` and re-fingerprinted every already-badged item in a library
that enables no family at all — a ~16,000-item re-render for a file nothing
draws. The trade is stated rather than hidden: replacing one of these PNGs
without editing the config moves no fingerprint, which is the same residual
`url:`-sourced overlay definitions already carry (roadmap row 97).

Regenerate with, from `assets/badges/`:

    find images/cr images/Direct-Play.png images/versions.png images/dual_audio.png images/multi_audio.png -type f | LC_ALL=C sort | xargs sha256sum > OVERLAY-MANIFEST.sha256

Run it in a Linux container, not this repo's Windows host shell: git-bash's
`sha256sum` writes binary-mode lines (`<hash> *<path>`) and the manifest's
parser splits on the two-space text-mode separator (`line.partition("  ")`).
