# Plex episode posters() — the derived-frame probe

Read-only, plexapi one-liner against the operator's own server
(`plex.vaderrp.com`), 2026-09-03. Listed one episode's `posters()` entries —
keys, provider, selected only. No token in the output below or anywhere in
this file.

This is what answered the open question: whether `posters()` exposes the frame Plex derives from the media file
itself, and what its listing entry looks like. It does — the **last** entry,
`provider=None`, `ratingKey` prefixed `media://`. That entry is what
`plex/artwork.generated_title_card_url` selects (`_generated_default`,
`GENERATED_ARTWORK_PREFIX = "media://"`) — never the first non-`upload://`
entry (`_agent_default`'s own rule), which the listing below shows is Plex's
**agent** guess (a `metadata://` local poster, then tmdb/imdb/tvdb hits) —
a different, wrong thing to have picked.

The listing also carries **five** `upload://` entries (one `selected=True`,
four orphaned) — this service's own past re-renders, which Plex keeps
appending to rather than replacing. That accumulation is out of this row's
scope; filed as roadmap row 242.

## Raw output

```
episode 153736 'Richard Madeley, Mel Giedroyc, Lee Mack, Simon Amstell' | thumb: /library/metadata/153736/thumb/1788286708
 poster key='/library/metadata/153736/file?url=metadata%3A%2F%2Fposters%2F8c8ff1e60328d2ed3c3eb1903c257d41c0767fe6' provider='local' selected=False ratingKey='metadata://posters/8c8ff1e60328d2ed3c3eb1903c257d41c0767fe6'
 poster key='https://image.tmdb.org/t/p/original/uFn9CRF9bzRCMCbhFEhLlg82els.jpg' provider='tmdb' selected=False ratingKey='https://image.tmdb.org/t/p/original/uFn9CRF9bzRCMCbhFEhLlg82els.jpg'
 poster key='https://m.media-amazon.com/images/M/MV5BNWE0ZDQxZTAtYWE0ZS00OThlLWJiODUtOTYwNTk0YzEwMTBhXkEyXkFqcGc@._V1_.jpg' provider='imdb' selected=False ratingKey='https://m.media-amazon.com/images/M/MV5BNWE0ZDQxZTAtYWE0ZS00OThlLWJiODUtOTYwNTk0YzEwMTBhXkEyXkFqcGc@._V1_.jpg'
 poster key='https://artworks.thetvdb.com/banners/episodes/79556/356117.jpg' provider='tvdb' selected=False ratingKey='https://artworks.thetvdb.com/banners/episodes/79556/356117.jpg'
 poster key='/library/metadata/153736/file?url=upload%3A%2F%2Fposters%2Fseasons%2F1%2Fepisodes%2F1%2Fdf6421f531157330ca30ff2f5ad604edc847e954' provider=None selected=False ratingKey='upload://posters/seasons/1/episodes/1/df6421f531157330ca30ff2f5ad604edc847e954'
 (three more upload:// entries, one selected=True, omitted here — five total)
 poster key='/library/metadata/153736/file?url=media%3A%2F%2F5%2F236feb94b72684905e052dfe82d48a8bce89bb5%2Ebundle%2FContents%2FThumbnails%2Fthumb1%2Ejpg' provider=None selected=False ratingKey='media://5/236feb94b72684905e052dfe82d48a8bce89bb5.bundle/Contents/Thumbnails/thumb1.jpg'
```
