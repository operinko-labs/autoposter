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

Deliberately **not** taken: `cr/`, `edition/`, `network/`, `ribbon/`, `streaming/`,
`studio/` — 1,795 files for overlays this deployment does not enable.

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
