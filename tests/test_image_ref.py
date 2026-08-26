"""``parse_image_ref`` -- splitting AUTOPOSTER_IMAGE_REF into the registry
host, Harbor project and repository api/version.py needs.

Unset/blank and unparseable both return None (the version check off), but
only the latter logs -- and never the ref itself, which carries the registry
host. See config/image_ref.py's docstring for the shape assumed.
"""
import logging

from autoposter.config.image_ref import parse_image_ref


def test_a_plain_ref_splits_into_registry_project_repository():
    result = parse_image_ref("registry.example.com/library/autoposter:sha-abc1234")
    assert result == ("registry.example.com", "library", "autoposter")


def test_a_registry_port_is_kept_with_the_registry_not_read_as_a_tag():
    result = parse_image_ref("registry.example.com:5000/library/autoposter:sha-abc1234")
    assert result == ("registry.example.com:5000", "library", "autoposter")


def test_a_multi_segment_repository_path_is_joined():
    result = parse_image_ref("registry.example.com/library/nested/autoposter:sha-abc1234")
    assert result == ("registry.example.com", "library", "nested/autoposter")


def test_a_digest_reference_is_accepted_in_place_of_a_tag():
    result = parse_image_ref(
        "registry.example.com/library/autoposter"
        "@sha256:e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"
    )
    assert result == ("registry.example.com", "library", "autoposter")


def test_a_tag_and_a_digest_together_are_both_stripped():
    result = parse_image_ref(
        "registry.example.com/library/autoposter:sha-abc1234"
        "@sha256:e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"
    )
    assert result == ("registry.example.com", "library", "autoposter")


def test_no_tag_at_all_is_fine():
    result = parse_image_ref("registry.example.com/library/autoposter")
    assert result == ("registry.example.com", "library", "autoposter")


# --- off -----------------------------------------------------------------


def test_unset_is_off_without_a_warning(caplog):
    with caplog.at_level(logging.WARNING):
        assert parse_image_ref("") is None
    assert caplog.records == []


def test_whitespace_only_is_off_without_a_warning(caplog):
    with caplog.at_level(logging.WARNING):
        assert parse_image_ref("   ") is None
    assert caplog.records == []


# --- garbage: logs one warning, never the ref -----------------------------


def test_a_single_segment_is_unparseable():
    """No registry, no project -- Docker Hub's own short form, which this
    scheme has no way to attribute a Harbor project or repository to."""
    result = parse_image_ref("autoposter:sha-abc1234")
    assert result is None


def test_two_segments_with_no_registry_host_is_unparseable():
    result = parse_image_ref("library/autoposter:sha-abc1234")
    assert result is None


def test_a_first_segment_with_no_dot_or_colon_is_not_a_registry():
    """Three segments, but the first doesn't look like a host -- refused
    rather than guessed at, per this module's stated shape."""
    result = parse_image_ref("myharbor/library/autoposter:sha-abc1234")
    assert result is None


def test_a_trailing_slash_leaves_an_empty_repository():
    result = parse_image_ref("registry.example.com/library/")
    assert result is None


def test_garbage_logs_exactly_one_warning(caplog):
    with caplog.at_level(logging.WARNING):
        result = parse_image_ref("not-a-valid-ref")

    assert result is None
    warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert len(warnings) == 1
    assert "AUTOPOSTER_IMAGE_REF" in warnings[0].getMessage()


def test_the_unparseable_ref_never_appears_in_the_log(caplog):
    """The rule api/version.py already holds the Harbor URL to: an internal
    registry host must never reach a log at INFO+ (WARNING here)."""
    secret_looking_ref = "internal.harbor.corp.example/oops"

    with caplog.at_level(logging.WARNING):
        result = parse_image_ref(secret_looking_ref)

    assert result is None
    assert secret_looking_ref not in caplog.text
    assert "internal.harbor.corp.example" not in caplog.text
