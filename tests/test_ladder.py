from autoposter.providers.base import POSTER, ArtCandidate, ArtRequest
from autoposter.providers.ladder import (
    Selection, best_candidate, language_rank, select_artwork,
)

ORDER = ["xx", "en", "fi"]
REQUEST = ArtRequest(art_kind=POSTER, is_movie=True, tmdb_id=693134)


def candidate(language, score=1.0, provider="TMDB", includes_text=None, width=2000):
    return ArtCandidate(
        provider=provider, url=f"http://x/{language}-{score}-{width}.jpg",
        language=language, width=width, height=3000, score=score,
        includes_text=includes_text,
    )


def test_textless_ranks_first():
    assert language_rank(candidate(None), ORDER) == 0


def test_configured_languages_rank_in_order():
    assert language_rank(candidate("en"), ORDER) == 1
    assert language_rank(candidate("fi"), ORDER) == 2


def test_unlisted_languages_rank_last():
    assert language_rank(candidate("de"), ORDER) > 2


def test_tvdb_three_letter_codes_are_matched():
    assert language_rank(candidate("eng"), ORDER) == 1
    assert language_rank(candidate("fin"), ORDER) == 2


def test_textless_wins_over_a_higher_scored_text_image():
    best = best_candidate([candidate("en", score=9.9), candidate(None, score=1.0)], ORDER)
    assert best.language is None


def test_score_breaks_ties_within_a_language():
    best = best_candidate([candidate("en", score=4.0), candidate("en", score=8.0)], ORDER)
    assert best.score == 8.0


def test_larger_image_breaks_a_score_tie():
    best = best_candidate(
        [candidate("en", score=5.0, width=1000), candidate("en", score=5.0, width=2000)], ORDER
    )
    assert best.width == 2000


def test_tvdb_includes_text_false_beats_an_untagged_image():
    tagged_textless = candidate("eng", score=1.0, provider="TVDB", includes_text=False)
    untagged = candidate(None, score=1.0, provider="TVDB", includes_text=True)
    best = best_candidate([untagged, tagged_textless], ORDER)
    assert best.includes_text is False


def test_empty_candidate_list_returns_none():
    assert best_candidate([], ORDER) is None


class FakeProvider:
    def __init__(self, name, candidates):
        self.name = name
        self._candidates = candidates
        self.calls = 0

    async def fetch(self, request):
        self.calls += 1
        return self._candidates


async def test_first_provider_with_textless_art_wins():
    first = FakeProvider("TMDB", [candidate(None)])
    second = FakeProvider("TVDB", [candidate(None)])
    result = await select_artwork([first, second], ORDER, REQUEST)
    assert result.candidate.provider == "TMDB"
    assert second.calls == 0


async def test_text_only_art_is_parked_and_later_providers_are_tried():
    first = FakeProvider("TMDB", [candidate("en")])
    second = FakeProvider("TVDB", [candidate(None, provider="TVDB")])
    result = await select_artwork([first, second], ORDER, REQUEST)
    assert result.candidate.provider == "TVDB"
    assert result.is_fallback is False
    assert second.calls == 1


async def test_parked_fallback_is_used_when_nothing_textless_exists():
    first = FakeProvider("TMDB", [candidate("en")])
    second = FakeProvider("TVDB", [])
    result = await select_artwork([first, second], ORDER, REQUEST)
    assert result.candidate.language == "en"
    assert result.is_fallback is True


async def test_textless_only_mode_discards_text_art():
    provider = FakeProvider("TMDB", [candidate("en")])
    result = await select_artwork([provider], ["xx"], REQUEST)
    assert result.candidate is None


async def test_language_order_without_xx_takes_the_best_listed_language():
    provider = FakeProvider("TMDB", [candidate("fi", score=9.0), candidate("en", score=1.0)])
    result = await select_artwork([provider], ["en", "fi"], REQUEST)
    assert result.candidate.language == "en"


async def test_a_failing_provider_does_not_stop_the_walk():
    class Broken:
        name = "TMDB"

        async def fetch(self, request):
            raise RuntimeError("provider down")

    working = FakeProvider("TVDB", [candidate(None, provider="TVDB")])
    result = await select_artwork([Broken(), working], ORDER, REQUEST)
    assert result.candidate.provider == "TVDB"


async def test_no_art_anywhere_returns_an_empty_selection():
    result = await select_artwork([FakeProvider("TMDB", [])], ORDER, REQUEST)
    assert result == Selection(candidate=None, is_fallback=False)


async def test_only_the_first_text_bearing_candidate_is_parked():
    """Across three providers, a later text-bearing hit must not replace the parked one.

    The second provider's candidate scores far higher than the first's, so if the
    park slot were ever overwritten by a later text-bearing candidate, this test
    would catch it by observing the wrong (higher-scored) candidate win.
    """
    first = FakeProvider("TMDB", [candidate("en", score=1.0)])
    second = FakeProvider("TVDB", [candidate("en", score=99.0, provider="TVDB")])
    third = FakeProvider("Fanart", [])
    result = await select_artwork([first, second, third], ORDER, REQUEST)
    assert result.is_fallback is True
    assert result.candidate.provider == "TMDB"
    assert result.candidate.score == 1.0


async def test_textless_hit_at_a_later_provider_beats_a_much_higher_scored_parked_candidate():
    first = FakeProvider("TMDB", [candidate("en", score=99.0)])
    second = FakeProvider("TVDB", [candidate(None, score=0.1, provider="TVDB")])
    result = await select_artwork([first, second], ORDER, REQUEST)
    assert result.candidate.provider == "TVDB"
    assert result.candidate.language is None
    assert result.is_fallback is False
