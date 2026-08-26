"""THE ORACLE -- Kometa's own build_filter, transcribed standalone.

Provenance: every table and every branch below is copied from Kometa v2.4.8
(https://raw.githubusercontent.com/Kometa-Team/Kometa/v2.4.8), fetched
2026-08-26. The line references are that tag's:

  modules/plex.py:60-138      search_translation
  modules/plex.py:168-193     show_translation
  modules/plex.py:195         modifier_translation
  modules/plex.py:234-305     method_alias
  modules/plex.py:306-307     modifier_alias, date_sub_mods
  modules/plex.py:430-445     movie_only_searches
  modules/plex.py:446-506     show_only_searches
  modules/plex.py:507-601     the category lists and the ``searches`` set
  modules/plex.py:604-667     movie_sorts, show_sorts
  modules/plex.py:779-787     sort_types
  modules/plex.py:2735-2751   Plex.split
  modules/builder.py:469      builder.date_attributes (what ``split`` reads)
  modules/builder.py:4092-4295  Builder.build_filter
  modules/builder.py:4297-4456  Builder.validate_attribute (the reached branches)
  modules/util.py:256-286     get_list
  modules/util.py:299-307     validate_date
  modules/util.py:859-879     check_int
  modules/util.py:911-1034    parse (the int/float/bool branches)
  modules/request.py:37-38    quote

NOTHING from the autoposter repository is imported. Unlike 9a's oracle this one
does not import plexapi either: ``build_filter`` reads a config dict and a tag
vocabulary and writes a string -- there is no Plex object anywhere in it.

The library's tag vocabulary is supplied by ``CHOICES`` below rather than by
``get_search_choices``, because that function's only job is to turn a written
word into the key Plex knows it by, and pinning the mapping is what makes the
comparison about TRANSLATION rather than about a server's contents.

Run:  python kometa_build_filter.py
      -> prints one line per config: ``N <url>``
"""
import os
import re
from datetime import datetime
from urllib.parse import quote as _urllib_quote

# --- the vocabulary fixture, shared BY VALUE with tests/test_collection_search_oracle.py
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


class Failed(Exception):
    """Stands in for modules.util.Failed / BuilderValidationError / FilterFailed.

    REMOVED: Kometa's four distinct exception classes collapse to one here.
    They differ only in how the RUN reacts to them (skip the builder, skip the
    collection, abort), and this driver has no run -- every one of them means
    "Kometa refuses to build this URL", which is the only distinction the
    oracle needs.
    """


# --- modules/request.py:37-38 -------------------------------------------------
# Kometa's request.py does ``from urllib import parse`` and calls
# ``parse.quote``. Here that module alias would be shadowed by ``util.parse``
# below, so the same callable is imported under a private name; the body is
# otherwise verbatim, and ``urllib.parse.quote``'s default ``safe="/"`` is what
# makes ``Hallmark & Co`` come back as ``Hallmark%20%26%20Co``.
def quote(data):
    return _urllib_quote(str(data))


# --- modules/plex.py:60-138, verbatim --------------------------------------
search_translation = {
    "episode_actor": "episode.actor",
    "episode_title": "episode.title",
    "network": "show.network",
    "edition": "editionTitle",
    "critic_rating": "rating",
    "audience_rating": "audienceRating",
    "episode_critic_rating": "episode.rating",
    "episode_audience_rating": "episode.audienceRating",
    "user_rating": "userRating",
    "episode_user_rating": "episode.userRating",
    "content_rating": "contentRating",
    "episode_year": "episode.year",
    "release": "originallyAvailableAt",
    "show_unmatched": "show.unmatched",
    "episode_unmatched": "episode.unmatched",
    "episode_duplicate": "episode.duplicate",
    "added": "addedAt",
    "episode_added": "episode.addedAt",
    "episode_air_date": "episode.originallyAvailableAt",
    "plays": "viewCount",
    "episode_plays": "episode.viewCount",
    "last_played": "lastViewedAt",
    "episode_last_played": "episode.lastViewedAt",
    "unplayed": "unwatched",
    "episode_unplayed": "episode.unwatched",
    "dovi": "dovi",
    "subtitle_language": "subtitleLanguage",
    "audio_language": "audioLanguage",
    "progress": "inProgress",
    "episode_progress": "episode.inProgress",
    "unplayed_episodes": "show.unwatchedLeaves",
    "season_collection": "season.collection",
    "episode_collection": "episode.collection",
    "season_label": "season.label",
    "episode_label": "episode.label",
    "artist_title": "artist.title",
    "artist_user_rating": "artist.userRating",
    "artist_genre": "artist.genre",
    "artist_collection": "artist.collection",
    "artist_country": "artist.country",
    "artist_mood": "artist.mood",
    "artist_style": "artist.style",
    "artist_added": "artist.addedAt",
    "artist_last_played": "artist.lastViewedAt",
    "artist_unmatched": "artist.unmatched",
    "artist_label": "artist.label",
    "album_title": "album.title",
    "album_year": "album.year",
    "album_decade": "album.decade",
    "album_genre": "album.genre",
    "album_plays": "album.viewCount",
    "album_last_played": "album.lastViewedAt",
    "album_user_rating": "album.userRating",
    "album_critic_rating": "album.rating",
    "album_record_label": "album.studio",
    "album_mood": "album.mood",
    "album_style": "album.style",
    "album_format": "album.format",
    "album_type": "album.subformat",
    "album_collection": "album.collection",
    "album_added": "album.addedAt",
    "album_released": "album.originallyAvailableAt",
    "album_unmatched": "album.unmatched",
    "album_source": "album.source",
    "album_label": "album.label",
    "track_mood": "track.mood",
    "track_title": "track.title",
    "track_plays": "track.viewCount",
    "track_last_played": "track.lastViewedAt",
    "track_skips": "track.skipCount",
    "track_last_skipped": "track.lastSkippedAt",
    "track_user_rating": "track.userRating",
    "track_last_rated": "track.lastRatedAt",
    "track_added": "track.addedAt",
    "track_trash": "track.trash",
    "track_source": "track.source",
    "track_label": "track.label",
}

# --- modules/plex.py:168-193, verbatim -------------------------------------
show_translation = {
    "title": "show.title",
    "country": "show.country",
    "studio": "show.studio",
    "rating": "show.rating",
    "audienceRating": "show.audienceRating",
    "userRating": "show.userRating",
    "contentRating": "show.contentRating",
    "year": "show.year",
    "originallyAvailableAt": "show.originallyAvailableAt",
    "unmatched": "show.unmatched",
    "genre": "show.genre",
    "collection": "show.collection",
    "actor": "show.actor",
    "addedAt": "show.addedAt",
    "viewCount": "show.viewCount",
    "lastViewedAt": "show.lastViewedAt",
    "editionTitle": "show.editionTitle",
    "resolution": "episode.resolution",
    "hdr": "episode.hdr",
    "dovi": "episode.dovi",
    "subtitleLanguage": "episode.subtitleLanguage",
    "audioLanguage": "episode.audioLanguage",
    "trash": "episode.trash",
    "label": "show.label",
}

# --- modules/plex.py:195, verbatim -----------------------------------------
modifier_translation = {"": "", ".not": "!", ".is": "%3D", ".isnot": "!%3D", ".gt": "%3E%3E", ".gte": "%3E", ".lt": "%3C%3C", ".lte": "%3C", ".before": "%3C%3C", ".after": "%3E%3E", ".begins": "%3C", ".ends": "%3E", ".regex": "", ".rated": ""}

# --- modules/plex.py:234-305, verbatim -------------------------------------
method_alias = {
    "actors": "actor",
    "role": "actor",
    "roles": "actor",
    "show_actor": "actor",
    "show_actors": "actor",
    "show_role": "actor",
    "show_roles": "actor",
    "collections": "collection",
    "plex_collection": "collection",
    "show_collections": "collection",
    "show_collection": "collection",
    "content_ratings": "content_rating",
    "contentRating": "content_rating",
    "contentRatings": "content_rating",
    "countries": "country",
    "decades": "decade",
    "directors": "director",
    "genres": "genre",
    "labels": "label",
    "collection_minimum": "minimum_items",
    "playlist_minimum": "minimum_items",
    "save_missing": "save_report",
    "rating": "critic_rating",
    "show_user_rating": "user_rating",
    "video_resolution": "resolution",
    "tmdb_trending": "tmdb_trending_daily",
    "play": "plays",
    "show_plays": "plays",
    "show_play": "plays",
    "episode_play": "episode_plays",
    "originally_available": "release",
    "episode_originally_available": "episode_air_date",
    "episode_release": "episode_air_date",
    "episode_released": "episode_air_date",
    "show_originally_available": "release",
    "show_release": "release",
    "show_air_date": "release",
    "released": "release",
    "show_released": "release",
    "max_age": "release",
    "studios": "studio",
    "networks": "network",
    "producers": "producer",
    "writers": "writer",
    "years": "year",
    "show_year": "year",
    "show_years": "year",
    "filter": "filters",
    "seasonyear": "year",
    "isadult": "adult",
    "startdate": "start",
    "enddate": "end",
    "averagescore": "score",
    "minimum_tag_percentage": "min_tag_percent",
    "minimumtagrank": "min_tag_percent",
    "minimum_tag_rank": "min_tag_percent",
    "anilist_tag": "anilist_search",
    "anilist_genre": "anilist_search",
    "anilist_season": "anilist_search",
    "mal_producer": "mal_studio",
    "mal_licensor": "mal_studio",
    "trakt_recommended": "trakt_recommended_weekly",
    "trakt_watched": "trakt_watched_weekly",
    "trakt_collected": "trakt_collected_weekly",
    "collection_changes_webhooks": "changes_webhooks",
    "radarr_add": "radarr_add_missing",
    "sonarr_add": "sonarr_add_missing",
    "trakt_recommended_personal": "trakt_recommendations",
    "collection_level": "builder_level",
    "overlay_level": "builder_level",
}

# --- modules/plex.py:306-307, verbatim -------------------------------------
modifier_alias = {".greater": ".gt", ".less": ".lt"}
date_sub_mods = {"s": "Seconds", "m": "Minutes", "h": "Hours", "d": "Days", "w": "Weeks", "o": "Months", "y": "Years"}

# --- modules/plex.py:430-445, verbatim -------------------------------------
movie_only_searches = [
    "director",
    "director.not",
    "producer",
    "producer.not",
    "writer",
    "writer.not",
    "decade",
    "duplicate",
    "unplayed",
    "progress",
    "duration.gt",
    "duration.gte",
    "duration.lt",
    "duration.lte",
]

# --- modules/plex.py:446-506, verbatim -------------------------------------
show_only_searches = [
    "network",
    "network.not",
    "season_collection",
    "season_collection.not",
    "episode_collection",
    "episode_collection.not",
    "season_label",
    "season_label.not",
    "episode_label",
    "episode_label.not",
    "episode_title",
    "episode_title.not",
    "episode_title.is",
    "episode_title.isnot",
    "episode_title.begins",
    "episode_title.ends",
    "episode_added",
    "episode_added.not",
    "episode_added.before",
    "episode_added.after",
    "episode_air_date",
    "episode_air_date.not",
    "episode_air_date.before",
    "episode_air_date.after",
    "episode_last_played",
    "episode_last_played.not",
    "episode_last_played.before",
    "episode_last_played.after",
    "episode_plays.gt",
    "episode_plays.gte",
    "episode_plays.lt",
    "episode_plays.lte",
    "episode_user_rating.gt",
    "episode_user_rating.gte",
    "episode_user_rating.lt",
    "episode_user_rating.lte",
    "episode_user_rating.rated",
    "episode_critic_rating.gt",
    "episode_critic_rating.gte",
    "episode_critic_rating.lt",
    "episode_critic_rating.lte",
    "episode_critic_rating.rated",
    "episode_audience_rating.gt",
    "episode_audience_rating.gte",
    "episode_audience_rating.lt",
    "episode_audience_rating.lte",
    "episode_audience_rating.rated",
    "episode_year",
    "episode_year.not",
    "episode_year.gt",
    "episode_year.gte",
    "episode_year.lt",
    "episode_year.lte",
    "unplayed_episodes",
    "episode_unplayed",
    "episode_duplicate",
    "episode_progress",
    "episode_unmatched",
    "show_unmatched",
]

# --- modules/plex.py:507-601, verbatim -------------------------------------
string_attributes = ["title", "studio", "edition", "episode_title", "artist_title", "album_title", "album_record_label", "track_title", "audio_codec"]
string_modifiers = ["", ".not", ".is", ".isnot", ".begins", ".ends"]
boolean_attributes = [
    "dovi",
    "hdr",
    "unmatched",
    "duplicate",
    "unplayed",
    "progress",
    "trash",
    "unplayed_episodes",
    "episode_unplayed",
    "episode_duplicate",
    "episode_progress",
    "episode_unmatched",
    "show_unmatched",
    "artist_unmatched",
    "album_unmatched",
    "track_trash",
]
tmdb_attributes = ["actor", "director", "producer", "writer"]
date_attributes = [
    "added",
    "episode_added",
    "release",
    "episode_air_date",
    "last_played",
    "episode_last_played",
    "artist_added",
    "artist_last_played",
    "album_last_played",
    "album_added",
    "album_released",
    "track_last_played",
    "track_last_skipped",
    "track_last_rated",
    "track_added",
]
date_modifiers = ["", ".not", ".before", ".after"]
year_attributes = ["decade", "year", "episode_year", "album_year", "album_decade"]
number_attributes = ["plays", "episode_plays", "album_plays", "track_plays", "track_skips"] + year_attributes
number_modifiers = [".gt", ".gte", ".lt", ".lte"]
float_attributes = ["user_rating", "episode_user_rating", "critic_rating", "episode_critic_rating", "audience_rating", "episode_audience_rating", "duration", "artist_user_rating", "album_user_rating", "album_critic_rating", "track_user_rating"]
float_modifiers = number_modifiers + [".rated"]
search_display = {"added": "Date Added", "release": "Release Date", "folder_location": "Folder Location", "hdr": "HDR", "progress": "In Progress", "episode_progress": "Episode In Progress"}
tag_attributes = [
    "actor",
    "episode_actor",
    "audio_language",
    "collection",
    "content_rating",
    "country",
    "director",
    "folder_location",
    "genre",
    "label",
    "season_label",
    "episode_label",
    "network",
    "producer",
    "resolution",
    "studio",
    "subtitle_language",
    "writer",
    "season_collection",
    "episode_collection",
    "edition",
    "artist_genre",
    "artist_collection",
    "artist_country",
    "artist_mood",
    "artist_label",
    "artist_style",
    "album_genre",
    "album_mood",
    "album_style",
    "album_format",
    "album_type",
    "album_collection",
    "album_source",
    "album_label",
    "track_mood",
    "track_source",
    "track_label",
]
tag_modifiers = ["", ".not", ".regex"]
no_not_mods = ["resolution", "decade", "album_decade"]
searches = (
    boolean_attributes
    + [f"{f}{m}" for f in string_attributes for m in string_modifiers]
    + [f"{f}{m}" for f in tag_attributes + year_attributes for m in tag_modifiers if f not in no_not_mods or m != ".not"]
    + [f"{f}{m}" for f in date_attributes for m in date_modifiers]
    + [f"{f}{m}" for f in number_attributes for m in number_modifiers if f not in no_not_mods]
    + [f"{f}{m}" for f in float_attributes for m in float_modifiers if f != "duration" or m != ".rated"]
)

# --- modules/plex.py:604-667, verbatim -------------------------------------
movie_sorts = {
    "title.asc": "titleSort",
    "title.desc": "titleSort%3Adesc",
    "year.asc": "year",
    "year.desc": "year%3Adesc",
    "originally_available.asc": "originallyAvailableAt",
    "originally_available.desc": "originallyAvailableAt%3Adesc",
    "release.asc": "originallyAvailableAt",
    "release.desc": "originallyAvailableAt%3Adesc",
    "critic_rating.asc": "rating",
    "critic_rating.desc": "rating%3Adesc",
    "audience_rating.asc": "audienceRating",
    "audience_rating.desc": "audienceRating%3Adesc",
    "user_rating.asc": "userRating",
    "user_rating.desc": "userRating%3Adesc",
    "content_rating.asc": "contentRating",
    "content_rating.desc": "contentRating%3Adesc",
    "duration.asc": "duration",
    "duration.desc": "duration%3Adesc",
    "progress.asc": "viewOffset",
    "progress.desc": "viewOffset%3Adesc",
    "plays.asc": "viewCount",
    "plays.desc": "viewCount%3Adesc",
    "added.asc": "addedAt",
    "added.desc": "addedAt%3Adesc",
    "viewed.asc": "lastViewedAt",
    "viewed.desc": "lastViewedAt%3Adesc",
    "resolution.asc": "mediaHeight",
    "resolution.desc": "mediaHeight%3Adesc",
    "bitrate.asc": "mediaBitrate",
    "bitrate.desc": "mediaBitrate%3Adesc",
    "random": "random",
}
show_sorts = {
    "title.asc": "titleSort",
    "title.desc": "titleSort%3Adesc",
    "year.asc": "year",
    "year.desc": "year%3Adesc",
    "originally_available.asc": "originallyAvailableAt",
    "originally_available.desc": "originallyAvailableAt%3Adesc",
    "episode_originally_available.asc": "episode.originallyAvailableAt",
    "episode_originally_available.desc": "episode.originallyAvailableAt%3Adesc",
    "release.asc": "originallyAvailableAt",
    "release.desc": "originallyAvailableAt%3Adesc",
    "episode_release.asc": "episode.originallyAvailableAt",
    "episode_release.desc": "episode.originallyAvailableAt%3Adesc",
    "critic_rating.asc": "rating",
    "critic_rating.desc": "rating%3Adesc",
    "audience_rating.asc": "audienceRating",
    "audience_rating.desc": "audienceRating%3Adesc",
    "user_rating.asc": "userRating",
    "user_rating.desc": "userRating%3Adesc",
    "content_rating.asc": "contentRating",
    "content_rating.desc": "contentRating%3Adesc",
    "unplayed.asc": "unviewedLeafCount",
    "unplayed.desc": "unviewedLeafCount%3Adesc",
    "episode_added.asc": "episode.addedAt",
    "episode_added.desc": "episode.addedAt%3Adesc",
    "added.asc": "addedAt",
    "added.desc": "addedAt%3Adesc",
    "viewed.asc": "lastViewedAt",
    "viewed.desc": "lastViewedAt%3Adesc",
    "random": "random",
}

# --- modules/plex.py:779-787 --------------------------------------------------
# REMOVED: the "season", "episode", "artist", "album" and "track" entries. Their
# sort matrices (plex.py:668-778) are not transcribed -- v1 searches movie and
# show libraries, and no config names another libtype.
sort_types = {
    "movie": ("title.asc", 1, movie_sorts),
    "show": ("title.asc", 2, show_sorts),
}

# ``Plex.split`` reads ``builder.date_attributes``, not ``plex.date_attributes``
# -- modules/builder.py:469, which is the plex list plus three show-only aired
# attributes. Copied so the date-modifier rewrite below reads the same list
# Kometa's does.
builder_date_attributes = date_attributes + [
    "first_episode_aired",
    "last_episode_aired",
    "last_episode_aired_or_never",
]

# The builder's ``self.Type``, which only ever appears inside message strings.
TYPE = "Collection"


# --- modules/plex.py:2735-2751 (Plex.split) -----------------------------------
def split(text):
    attribute, modifier = os.path.splitext(str(text).lower())
    attribute = method_alias[attribute] if attribute in method_alias else attribute
    modifier = modifier_alias[modifier] if modifier in modifier_alias else modifier

    # REMOVED: the "add_to_arr" and "arr_tag"/"arr_folder" branches
    # (plex.py:2742-2745). Both rewrite a Radarr/Sonarr setting name using
    # ``self.is_movie`` and neither is a search attribute.
    if attribute in builder_date_attributes and modifier in [".gt", ".gte"]:
        modifier = ".after"
    elif attribute in builder_date_attributes and modifier in [".lt", ".lte"]:
        modifier = ".before"
    final = f"{attribute}{modifier}"
    # REMOVED: logger.warning when text != final.
    return attribute, modifier, final


# --- modules/util.py:259-286 (get_list) ---------------------------------------
# REMOVED: the two ``@overload`` stubs above it (util.py:256-258), which are
# typing declarations with no runtime effect.
def get_list(data, lower=False, upper=False, split=True, int_list=False, trim=True, return_none=True):
    if split is True:
        split = ","
    if data is None:
        return None if return_none else []
    elif isinstance(data, list):
        list_data = data
    elif isinstance(data, dict):
        return [data]
    elif split is False:
        list_data = [str(data)]
    else:
        list_data = [s.strip() for s in str(data).split(split)]

    def get_str(input_data):
        return str(input_data).strip() if trim else str(input_data)

    if lower is True:
        return [get_str(d).lower() for d in list_data]
    elif upper is True:
        return [get_str(d).upper() for d in list_data]
    elif int_list is True:
        try:
            return [int(get_str(d)) for d in list_data]
        except ValueError:
            return []
    else:
        return [d if isinstance(d, dict) else get_str(d) for d in list_data]


# --- modules/util.py:299-307 (validate_date), verbatim ------------------------
def validate_date(date_text, return_as=None):
    if isinstance(date_text, datetime):
        date_obg = date_text
    else:
        try:
            date_obg = datetime.strptime(str(date_text), "%Y-%m-%d" if "-" in str(date_text) else "%m/%d/%Y")
        except ValueError:
            raise Failed(f"{date_text} must match pattern YYYY-MM-DD (e.g. 2020-12-25) or MM/DD/YYYY (e.g. 12/25/2020)")
    return datetime.strftime(date_obg, return_as) if return_as else date_obg


# --- modules/util.py:859-879 (check_int), verbatim ----------------------------
def check_int(value, datatype="int", minimum=1, maximum=None, throw=False):
    try:
        value = int(str(value)) if datatype == "int" else float(str(value))
        if (
            (maximum is None and minimum is None)
            or (maximum is not None and minimum is None and maximum >= value)
            or (maximum is None and minimum is not None and minimum <= value)
            or (maximum is not None and minimum is not None and minimum <= value <= maximum)
        ):
            return value
    except ValueError:
        if throw:
            message = f"{value} must be {'an integer' if datatype == 'int' else 'a number'}"
            if maximum is not None and minimum is None:
                message = f"{message} {maximum} or less"
            elif maximum is None and minimum is not None:
                message = f"{message} {minimum} or greater"
            elif maximum is not None and minimum is not None:
                message = f"{message} between {minimum} and {maximum}"
            raise Failed(message)
        return None


# --- modules/util.py:911-1034 (parse) -----------------------------------------
# REMOVED: the list/commalist/strlist/lowerlist/upperlist, intlist, listdict and
# dict/dictlist/dictdict/strdict/dictliststr datatype branches (util.py:917-975).
# Each returns before reaching the branches below, and ``validate_attribute``
# calls parse with datatype "int", "float" or "bool" only on every path the
# thirteen configs take.
def parse(error, attribute, data, datatype=None, methods=None, parent=None, default=None, options=None, translation=None, minimum=1, maximum=None, regex=None, range_split=None, date_return=None):
    display = f"{parent + ' ' if parent else ''}{attribute} attribute"
    if options is None and translation is not None:
        options = [o for o in translation]
    value = data[methods[attribute]] if methods and attribute in methods else data

    if methods and attribute not in methods:
        message = f"{display} not found"
    elif value is None:
        message = f"{display} is blank"
    elif regex is not None:
        regex_str, example = regex
        if re.compile(regex_str).match(str(value)):
            return str(value)
        else:
            message = f"{display}: {value} must match pattern {regex_str} e.g. {example}"
    elif datatype == "bool":
        if isinstance(value, bool):
            return value
        elif isinstance(value, (int, float)):
            return value > 0
        elif str(value).lower() in ["t", "true", "y", "yes"]:
            return True
        elif str(value).lower() in ["f", "false", "n", "no"]:
            return False
        else:
            message = f"{display} must be either true or false"
    elif datatype in ["int", "float"]:
        if range_split:
            range_values = str(value).split(range_split)
            if len(range_values) == 2:
                start = check_int(range_values[0], datatype=datatype, minimum=minimum, maximum=maximum)
                end = check_int(range_values[1], datatype=datatype, minimum=minimum, maximum=maximum)
                if start and end and start < end:
                    return f"{start}{range_split}{end}"
        else:
            new_value = check_int(value, datatype=datatype, minimum=minimum, maximum=maximum)
            if new_value is not None:
                return new_value
        message = f"{display} {value} must {'each ' if range_split else ''}be {'an integer' if datatype == 'int' else 'a number'}"
        if maximum is not None and minimum is None:
            message = f"{message} {maximum} or less"
        elif maximum is None and minimum is not None:
            message = f"{message} {minimum} or greater"
        elif maximum is not None and minimum is not None:
            message = f"{message} between {minimum} and {maximum}"
        if range_split:
            message = f"{message} separated by a {range_split}"
    elif datatype == "date":
        try:
            if default in ["today", "current"]:
                default = validate_date(datetime.now(), return_as=date_return)
            return validate_date(datetime.now() if data in ["today", "current"] else data, return_as=date_return)
        except Failed as e:
            message = f"{e}"
    elif (translation is not None and str(value).lower() not in translation) or (options is not None and translation is None and str(value).lower() not in options):
        valid_options = options if options is not None else list(translation) if translation is not None else []
        message = f"{display} {value} must be in [{', '.join([str(o) for o in valid_options])}]"
    else:
        return translation[str(value).lower()] if translation is not None else value

    # REMOVED: the ``default is not None`` half, which logs a warning and
    # substitutes the default. Nothing on this path passes a default.
    raise Failed(f"{error} Error: {message}")


def _choices(attribute, final_values, plex_search):
    """REPLACES ``Library.get_search_choices`` (plex.py:1300-1316) and
    ``Library.get_language_search_values`` (plex.py:1321-1344).

    Both are network reads of the library's own vocabulary. This returns the
    same SHAPE ``validate_attribute`` builds from them when ``plex_search`` is
    true -- a list of ``(written value, plex key)`` pairs, one pair per key,
    several pairs when a language code expands -- out of the pinned ``CHOICES``
    above. The language path lowercases its lookup exactly as Kometa does
    (``str(fvalue).lower()``, builder.py:4419).

    REMOVED with them: the ``get_actor_id`` fallback (builder.py:4425-4431),
    reached only for actor/director/producer/writer, and the ``show_options``
    half of the error message.
    """
    is_plex_search_language = plex_search and attribute in ("audio_language", "subtitle_language")
    valid_list = []
    for fvalue in final_values:
        lookup = str(fvalue).lower() if is_plex_search_language else str(fvalue)
        keys = CHOICES.get((attribute, lookup))
        if not keys:
            raise Failed(f"Plex Error: {attribute}: {fvalue} not found")
        valid_list.extend((fvalue, key) for key in keys)
    return valid_list


# --- modules/builder.py:4297-4456 (Builder.validate_attribute) ----------------
# The reached branches only. REMOVED: the ``validate`` parameter (this service
# refuses ``validate: false``, so every error raises); the two ``.regex``
# branches (:4301-4324, refused by 9b); ``origin_country``,
# ``original_language``/``tmdb_keyword``, ``tmdb_genre``/``tvdb_genre``,
# ``history``, ``tmdb_type``, ``tmdb_status``, ``imdb_keyword`` (:4353-4398) and
# ``seasons``/``episodes``/``albums``/``tracks`` and everything after (:4453+) --
# none is a Plex search attribute reachable from the thirteen configs.
def validate_attribute(attribute, modifier, final, data, plex_search=False, plex_search_type=None):
    def smart_pair(list_to_pair):
        return [(t, t) for t in list_to_pair] if plex_search else list_to_pair

    if attribute in string_attributes and modifier in ["", ".not", ".is", ".isnot", ".begins", ".ends"]:
        return smart_pair(get_list(data, split=False))
    elif attribute in year_attributes and modifier in ["", ".not", ".gt", ".gte", ".lt", ".lte"]:
        if modifier in ["", ".not"]:
            final_years = []
            values = get_list(data) or []
            for value in values:
                if str(value).startswith("current_year"):
                    year_values = str(value).split("-")
                    try:
                        final_years.append(datetime.now().year - (0 if len(year_values) == 1 else int(year_values[1].strip())))
                    except ValueError:
                        raise Failed(f"{TYPE} Error: {final} attribute modifier invalid '{year_values[1]}'")
                else:
                    final_years.append(parse(TYPE, final, value, datatype="int"))
            return smart_pair(final_years)
        else:
            if str(data).startswith("current_year"):
                year_values = str(data).split("-")
                try:
                    return datetime.now().year - (0 if len(year_values) == 1 else int(year_values[1].strip()))
                except ValueError:
                    raise Failed(f"{TYPE} Error: {final} attribute modifier invalid '{year_values[1]}'")
            return parse(TYPE, final, data, datatype="int", minimum=0)
    elif (attribute in number_attributes and modifier in ["", ".not", ".gt", ".gte", ".lt", ".lte"]) or (attribute in tag_attributes and modifier in [".count_gt", ".count_gte", ".count_lt", ".count_lte"]):
        return parse(TYPE, final, data, datatype="int", minimum=0)
    elif attribute in tag_attributes and modifier in ["", ".not"]:
        # REMOVED: the ``tmdb_attributes`` person expansion (:4400-4407), which
        # needs ``self.details["tmdb_person"]`` and reaches actor/director/
        # producer/writer only.
        final_values = get_list(data, trim=False) or []
        return _choices(attribute, final_values, plex_search)
    elif attribute in date_attributes and modifier in [".before", ".after"]:
        try:
            return validate_date(datetime.now() if data == "today" else data, return_as="%Y-%m-%d")
        except Failed as e:
            raise Failed(f"{TYPE} Error: {final}: {e}")
    elif attribute in date_attributes and modifier in ["", ".not"]:
        search_mod = "d"
        if plex_search and data and str(data)[-1] in ["s", "m", "h", "d", "w", "o", "y"]:
            search_mod = str(data)[-1]
            data = str(data)[:-1]
        search_data = parse(TYPE, final, data, datatype="int", minimum=0)
        return f"{search_data}{search_mod}" if plex_search else search_data
    elif attribute in float_attributes and modifier in ["", ".not", ".gt", ".gte", ".lt", ".lte"]:
        return parse(TYPE, final, data, datatype="float", minimum=0, maximum=None if attribute == "duration" else 10)
    elif attribute in boolean_attributes or (attribute in float_attributes and modifier in [".rated"]):
        return parse(TYPE, attribute, data, datatype="bool")
    raise Failed(f"{TYPE} Error: {final} reaches a validate_attribute branch this transcription removed")


# --- modules/builder.py:4092-4295 (Builder.build_filter) ----------------------
# Signature change: Kometa takes ``self`` and derives the libtype from
# ``self.builder_level`` / ``self.library.is_show`` / ``is_music``
# (:4110-4122); with no Builder and no Library the libtype is a parameter.
# ``display`` and ``default_sort`` are dropped -- the first only logs, the
# second only supplies a caller-side default sort that no config uses.
#
# REMOVED throughout: every ``logger.*`` call, and the whole
# ``display``/``filter_details``/``display_out``/``display_line`` half, which
# builds a human-readable summary returned BESIDE the URL and never inside it.
def build_filter(method, plex_filter, sort_type):
    if plex_filter is None:
        raise Failed(f"{TYPE} Error: {method} attribute is blank")
    if not isinstance(plex_filter, dict):
        raise Failed(f"{TYPE} Error: {method} must be a dictionary: {plex_filter}")

    filter_alias = {m.lower(): m for m in plex_filter}

    if "any" in filter_alias and "all" in filter_alias:
        raise Failed(f"{TYPE} Error: Cannot have more than one base")

    is_movie = sort_type == "movie"
    is_show = sort_type == "show"

    type_default_sort, type_key, sorts = sort_types[sort_type]

    sort = []
    if "sort_by" in filter_alias:
        test_sorts = plex_filter[filter_alias["sort_by"]]
        if test_sorts is None:
            raise Failed(f"{TYPE} Error: sort_by attribute is blank")
        if not isinstance(test_sorts, list):
            test_sorts = [test_sorts]
        for test_sort in test_sorts:
            if test_sort not in sorts:
                raise Failed(f"{TYPE} Error: sort_by '{test_sort}' is invalid. Options: {', '.join(sorts)}")
            sort.append(test_sort)
    if not sort:
        sort.append(type_default_sort)

    limit = None
    if "limit" in filter_alias:
        if plex_filter[filter_alias["limit"]] is None:
            raise Failed(f"{TYPE} Error: limit attribute is blank")
        elif str(plex_filter[filter_alias["limit"]]).lower() == "all":
            pass
        else:
            try:
                if int(plex_filter[filter_alias["limit"]]) < 1:
                    raise ValueError
                else:
                    limit = int(plex_filter[filter_alias["limit"]])
            except ValueError:
                raise Failed(f"{TYPE} Error: limit attribute must be an integer greater than 0")

    # REMOVED: the ``validate:`` switch (:4160-4167). This service refuses the
    # key outright, so the oracle always behaves as ``validate=True``: every
    # per-attribute error raises rather than being logged and skipped.

    # REMOVED: the ``level`` parameter, which only fed the display indent.
    def _filter(filter_dict, is_all=True):
        output = ""
        conjunction = f"{'and' if is_all else 'or'}=1&"
        for _key, _data in filter_dict.items():
            attr, modifier, final_attr = split(_key)

            def build_url_arg(arg, mod=None):
                # REMOVED: the ``folder_location`` branch of ``arg_key``, which
                # calls ``Library.get_search_key`` -> ``listFilters`` (a network
                # read). ``folder_location`` is deferred by name and no config
                # reaches it.
                arg_key = search_translation[attr] if attr in search_translation else attr
                arg_key = show_translation[arg_key] if is_show and arg_key in show_translation else arg_key
                if mod is None:
                    mod = modifier_translation[modifier] if modifier in modifier_translation else modifier
                return f"{arg_key}{mod}={arg}&"

            error = None
            if final_attr not in searches and not final_attr.startswith(("any", "all")):
                error = f"{TYPE} Error: {method} attribute '{final_attr}' is not valid"
            elif is_show and final_attr in movie_only_searches:
                error = f"{TYPE} Error: {method} attribute '{final_attr}' only works for movie libraries"
            elif is_movie and final_attr in show_only_searches:
                error = f"{TYPE} Error: {method} attribute '{final_attr}' only works for show libraries"
            # REMOVED: the two music branches (``is_music``/``music_searches``,
            # :4200-4203). v1 searches movies and shows.
            elif _data is not False and _data != 0 and not _data:
                error = f"{TYPE} Error: {method} attribute '{final_attr}' is blank"
            else:
                if final_attr.startswith(("any", "all")):
                    dicts = get_list(_data) or []
                    results = ""
                    for dict_data in dicts:
                        if not isinstance(dict_data, dict):
                            raise Failed(f"{TYPE} Error: {attr} must be either a dictionary or list of dictionaries")
                        inside_filter = _filter(dict_data, is_all=attr == "all")
                        if len(inside_filter) > 0:
                            results += f"{conjunction if len(results) > 0 else ''}push=1&{inside_filter}pop=1&"
                else:
                    validation = validate_attribute(attr, modifier, final_attr, _data, plex_search=True, plex_search_type=sort_type)
                    if validation is not False and validation != 0 and not validation:
                        continue
                    elif attr in date_attributes and modifier in ["", ".not"]:
                        last_mod = "%3E%3E" if modifier == "" else "%3C%3C"
                        search_mod = validation[-1]
                        if search_mod == "o":
                            validation = f"{validation[:-1]}mon"
                        results = build_url_arg(f"-{validation}", mod=last_mod)
                    elif attr == "duration" and modifier in [".gt", ".gte", ".lt", ".lte"]:
                        results = build_url_arg(validation * 60000)
                    elif modifier == ".rated":
                        results = build_url_arg(-1, mod="!" if validation else "")
                    elif attr in boolean_attributes:
                        bool_mod = "" if validation else "!"
                        results = build_url_arg(1, mod=bool_mod)
                    elif (attr in tag_attributes + string_attributes + year_attributes) and modifier in ["", ".is", ".isnot", ".not", ".begins", ".ends", ".regex"]:
                        results = ""
                        for og_value, result in validation:
                            built_arg = build_url_arg(quote(str(result)) if attr in string_attributes else result)
                            results += f"{conjunction if len(results) > 0 else ''}{built_arg}"
                    else:
                        results = build_url_arg(validation)
                output += f"{conjunction if len(output) > 0 else ''}{results}"
            if error:
                # REMOVED: the ``validate=False`` half, which logs and continues.
                raise Failed(error)
        return output

    if "any" not in filter_alias and "all" not in filter_alias:
        # REMOVED: the implicit-base reconstruction (:4265-4277), which rebuilds
        # a base_dict out of the top-level keys using ``and_searches``/
        # ``or_searches``. 9b refuses a plex_search with no written base
        # (decision D1) and all thirteen configs write one, so the branch is
        # dead here -- kept as the refusal Kometa also ends at when nothing
        # matched, so a config that lost its base cannot take a quiet path.
        raise Failed(f"{TYPE} Error: Must have either any or all as a base for {method}")
    base = "all" if "all" in filter_alias else "any"
    base_all = base == "all"
    if plex_filter[filter_alias[base]] is None:
        raise Failed(f"{TYPE} Error: {base} attribute is blank")
    if not isinstance(plex_filter[filter_alias[base]], dict):
        raise Failed(f"{TYPE} Error: {base} must be a dictionary: {plex_filter[filter_alias[base]]}")
    base_dict = plex_filter[filter_alias[base]]
    built_filter = _filter(base_dict, is_all=base_all)
    if len(built_filter) > 0:
        final_filter = built_filter[:-1] if base_all else f"push=1&{built_filter}pop=1"
        filter_url = f"?type={type_key}&{f'limit={limit}&' if limit else ''}sort={'%2C'.join([sorts[s] for s in sort])}&{final_filter}"
    else:
        raise Failed(f"{TYPE} Error: No Plex Filter Created")
    return type_key, filter_url


# --- the thirteen configs, in KOMETA'S spelling -------------------------------
# Config 7 is the one place the two spellings differ: ours writes the second
# duration as ``2:30``, which is 9a's own written form (``_as_minutes``) and has
# no Kometa equivalent. The MINUTE VALUE is identical -- 150 -- which is the
# point of the comparison.
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
    ("movie", {"all": {"duration.gt": 90, "duration.lte": 150}}),
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
]


def main():
    for index, (libtype, plex_filter) in enumerate(CONFIGS, start=1):
        _, url = build_filter("plex_search", plex_filter, libtype)
        print(f"{index} {url}")


if __name__ == "__main__":
    main()
