# Third-party notices

autoposter is licensed under the GNU General Public License, version 3 only
(`GPL-3.0-only`; see [LICENSE](LICENSE)). It is a rewrite of, and carries
material from, the works below. Each keeps its own licence; the copyleft one
among them is what fixes the licence of the project as a whole.

The service's own data sources (TMDB, TheTVDB, Fanart.tv, IMDb, MDBList) are
credited under *Attribution* in [README.md](README.md); they are usage terms,
not code licences, and are not repeated here.

## Posterizarr — GPL-3.0

<https://github.com/fscorrupt/Posterizarr>
Copyright (c) fscorrupt and the Posterizarr contributors.

The poster and title-card pipeline (Phase 1) reproduces Posterizarr's
textless-artwork-plus-ImageMagick compositing and its configuration semantics,
rewritten in Python in 2026. Posterizarr is licensed under GPL-3.0 with no
"or later" grant, which is why this project is `GPL-3.0-only` rather than the
MIT licence its other upstream carries. No Posterizarr source file is copied
into this repository; the overlay images and fonts that `assets/README.md`
asks the operator to supply are the operator's own.

## Kometa — MIT

<https://github.com/Kometa-Team/Kometa>
Copyright (c) 2026 meisnate12

The badge and overlay images under `assets/badges/images/`, and the data files
generated from Kometa's defaults (`assets/badges/languages.json`,
`assets/collections/content_rating_cs.json`), were taken from the pinned
`kometateam/kometa:v2.4.8` image. The overlay and collection definitions this
service ships are modelled on those defaults. Exact paths, digests and the
caveat about third-party marks inside the image set are in
`assets/badges/PROVENANCE.md` and `assets/collections/PROVENANCE.md`.

## Kometa Defaults-Image-Creation — MIT

<https://github.com/Kometa-Team/Defaults-Image-Creation>
Copyright (c) 2025 Kometa Team

The separator-art rendering parameters in
`src/autoposter/collections/separator_art.py` are transcribed from its
scripts; see `assets/fonts/PROVENANCE.md`.

### MIT License (applies to the two Kometa works above, under their respective copyright lines)

    Permission is hereby granted, free of charge, to any person obtaining a copy
    of this software and associated documentation files (the "Software"), to deal
    in the Software without restriction, including without limitation the rights
    to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
    copies of the Software, and to permit persons to whom the Software is
    furnished to do so, subject to the following conditions:

    The above copyright notice and this permission notice shall be included in all
    copies or substantial portions of the Software.

    THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
    IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
    FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
    AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
    LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
    OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
    SOFTWARE.

## Fonts — SIL Open Font License 1.1

- `assets/fonts/Comfortaa-Medium.ttf` — Copyright 2011 The Comfortaa Project
  Authors (<https://github.com/alexeiva/comfortaa>), Reserved Font Name
  "Comfortaa". Licence text vendored at `assets/fonts/OFL.txt`.
- `assets/badges/fonts/Inter-Bold.ttf`, `Inter-Medium.ttf` — Copyright (c) The
  Inter Project Authors (<https://github.com/rsms/inter>), Reserved Font Name
  "Inter". Taken from the Kometa image, which ships no licence file beside
  them; Inter's own OFL text is not yet vendored here.
