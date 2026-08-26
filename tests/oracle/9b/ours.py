"""Our side of the oracle: parse_filters + build_search_url, fourteen URLs.

The comparison itself lives in ``tests/test_collection_search_oracle.py``,
where Kometa's answers are pinned as data. This script exists so a reviewer can
put the two sides side by side without pytest::

    docker compose -p p9bt3 -f docker-compose.yml -f .superpowers/isolated-db.yml \
        run --rm test sh -c 'python tests/oracle/9b/kometa_build_filter.py > /tmp/k.txt; \
                             python tests/oracle/9b/ours.py > /tmp/o.txt; diff /tmp/k.txt /tmp/o.txt && echo IDENTICAL'

The configs below are OUR spelling of the same fourteen. Config 7 is the one
place the two differ -- ``2:30`` is 9a's written duration form and Kometa has
no equivalent, so its side is driven with the same value written ``150``.

Unlike ``kometa_build_filter.py``, this one DOES import from the repository:
that is the point of it.
"""
from autoposter.collections.filters import parse_filters
from autoposter.collections.search_url import build_search_url

CHOICES = {
    ("content_rating", "PG-13"): ("5",),
    ("content_rating", "R"): ("7",),
    ("genre", "Horror"): ("1138",),
    ("genre", "Drama"): ("9",),
    ("resolution", "1080"): ("1080",),
    ("network", "HBO"): ("42",),
    ("audio_language", "en"): ("en",),
    ("audio_language", "es"): ("es-419", "es-MX", "spa"),
}


def resolve(attribute, value, /):
    return CHOICES.get((attribute, value), ())


CONFIGS = [
    ("movie", {"all": {"content_rating": ["PG-13", "R"]}}),
    ("movie", {
        "any": {"studio": "A24", "year.gte": 2020},
        "sort_by": "critic_rating.desc", "limit": 25,
    }),
    ("movie", {"all": {
        "content_rating": "PG-13",
        "any": [{"studio": "A24", "year.gte": 2020}, {"genre": "Horror"}],
    }}),
    ("movie", {"all": {
        "year.gte": 2000, "all": {"studio": "A24", "critic_rating.gte": 8},
    }}),
    ("movie", {"all": {
        "added": 30, "release.not": "6o", "last_played.not": "2y",
    }}),
    ("movie", {"all": {
        "release.after": "2000-01-01", "added.before": "12/25/2020",
    }}),
    ("movie", {"all": {"duration.gt": 90, "duration.lte": "2:30"}}),
    ("movie", {"all": {
        "critic_rating.rated": True, "audience_rating.rated": False,
    }}),
    ("movie", {"all": {"unplayed": True, "progress": False}}),
    ("movie", {"all": {
        "studio.begins": "Warner Bros",
        "studio.not": "Hallmark & Co",
        "studio.is": "A24",
    }}),
    ("movie", {
        "all": {"year.gte": 2010},
        "sort_by": ["critic_rating.desc", "title.asc"], "limit": 100,
    }),
    ("show", {
        "all": {
            "genre": "Drama", "resolution": "1080", "audio_language": "en",
            "network": "HBO", "added.after": "2024-01-01",
        },
        "sort_by": "episode_added.desc", "limit": 10,
    }),
    ("movie", {"all": {"audio_language": "es"}}),
    ("movie", {"any": {"content_rating": ["PG-13", "R"]}}),
]


def main():
    for index, (libtype, params) in enumerate(CONFIGS, start=1):
        base = "all" if "all" in params else "any"
        group = parse_filters(params[base], field="params", searching=True, base=base)
        sort_by = params.get("sort_by") or ()
        if isinstance(sort_by, str):
            sort_by = [sort_by]
        url = build_search_url(
            group,
            libtype=libtype,
            sort_by=sort_by,
            limit=params.get("limit"),
            resolve_tag=resolve,
        )
        print(f"{index} {url}")


if __name__ == "__main__":
    main()
