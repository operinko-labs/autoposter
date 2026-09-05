"""Roadmap row 92 through the config editor's own endpoints.

The write path has four guards in front of the merge -- the structural
refusal, the unknown-key walk, the empty-object refusal and the drop cap --
and a per-library document has to meet all four the way any other document
does. Three of them needed no change at all, and the tests that say so are
worth as much as the one that did.

The fixtures are the config editor's own, imported rather than rebuilt so the
two files cannot drift about what a deployment looks like -- the shape
``tests/test_config_safety.py`` already uses. This file is deliberately NOT
named ``test_api_*``: ``tests/test_ci_path_filters.py`` globs that prefix and
requires a CI lane assignment for each one, and this suite is the config
editor's suite wearing a different hat rather than a twenty-third API suite.
"""
import pytest
from sqlalchemy import select

from autoposter.db.models import ConfigOverride

# Fixtures, re-exported. pytest's default import mode puts `tests/` on
# sys.path, so this is a plain module import.
from test_api_config_editor import (  # noqa: F401
    app,
    auth_headers,
    client,
    config_file,
)

pytestmark = pytest.mark.asyncio

BLOCK = {"libraries": {"Movies": {"operations": {"write_to_plex": False}}}}


async def _put(client, auth_headers, document: dict, **extra):
    return await client.put(
        "/api/config/overrides",
        headers=auth_headers,
        json={"document": document, **extra},
    )


async def test_a_per_library_override_round_trips(client, auth_headers):
    """Stored, merged, served back as an overridden path at full depth."""
    saved = await _put(client, auth_headers, BLOCK)
    assert saved.status_code == 200, saved.text

    body = (await client.get("/api/config", headers=auth_headers)).json()
    assert "libraries.Movies.operations.write_to_plex" in body["overridden_paths"]
    assert body["libraries"]["Movies"]["operations"]["write_to_plex"] is False
    # The GLOBAL setting is untouched, which is the whole point of the layer.
    assert body["operations"]["write_to_plex"] is True
    # Served as what the block STATES: no null leaf for anything unstated,
    # and no null section for a section the block never named.
    assert body["libraries"]["Movies"] == {"operations": {"write_to_plex": False}}


async def test_the_stored_document_moves_no_render_version(client, auth_headers):
    """The storm proof, through the endpoint that actually stores one.

    ``version_before`` and ``version_after`` are what the Settings page shows
    an operator as "A to B"; for a whitelist edit they must be the same
    string, and the response must therefore promise no restart and no
    re-render.
    """
    response = await _put(client, auth_headers, BLOCK)
    saved = response.json()
    assert saved["version_before"] == saved["version_after"]
    assert saved["restart_required"] == []

    preview = await client.post(
        "/api/config/preview", headers=auth_headers, json={"document": BLOCK},
    )
    # `_render_affecting` reads version and skip_tba only, so the walk never
    # runs and `impact: null` is the honest answer -- "this cannot change a
    # rendered image", not "zero items".
    assert preview.json()["impact"] is None


async def test_a_real_library_name_is_not_an_unknown_setting(client, auth_headers):
    """The defect T1's walk arm closed, proved where it would have been felt:
    before it, this save answered 422 with `libraries.Movies` unknown."""
    response = await _put(client, auth_headers, BLOCK)
    assert response.status_code == 200, response.text


async def test_a_typo_under_a_library_is_refused_at_full_depth(client, auth_headers):
    response = await _put(client, auth_headers, {
        "libraries": {"Movies": {"operations": {"write_to_plexx": False}}},
    })
    assert response.status_code == 422
    assert response.json()["detail"] == [
        {"path": "libraries.Movies.operations.write_to_plexx",
         "message": "unknown setting"},
    ]


async def test_a_structurally_global_key_is_refused_with_its_reason(
    client, auth_headers,
):
    """Ahead of the unknown-key walk on purpose: this IS a real setting, and
    "unknown setting" would be a true sentence that sends the operator
    looking for a typo."""
    response = await _put(client, auth_headers, {
        "libraries": {"Movies": {"operations": {"tmdb_backoff_seconds": 30}}},
    })
    assert response.status_code == 422
    detail = response.json()["detail"]
    assert len(detail) == 1
    assert detail[0]["path"] == "libraries.Movies.operations.tmdb_backoff_seconds"
    assert "TMDb rate budget" in detail[0]["message"]
    assert "unknown" not in detail[0]["message"]


async def test_a_server_wide_maintenance_key_is_refused_with_its_reason(
    client, auth_headers,
):
    response = await _put(client, auth_headers, {
        "libraries": {"Movies": {"maintenance": {"optimize": True}}},
    })
    assert response.status_code == 422
    detail = response.json()["detail"]
    assert detail[0]["path"] == "libraries.Movies.maintenance.optimize"
    assert "server-wide" in detail[0]["message"]


@pytest.mark.parametrize("section", ["artwork", "render", "scheduler", "made_up_section"])
async def test_a_non_whitelisted_section_under_a_library_is_refused(
    client, auth_headers, session, section,
):
    """Roadmap row 92 review, Important 1.

    A SECTION under a library block that is not one of the three
    whitelisted ones is refused, whether it names a real config field this
    service does not let vary per library (``artwork``, ``scheduler``) or
    nothing at all (``render``, the made-up placeholder) -- and nothing is
    stored either way, matching every other refusal in this file.
    """
    response = await _put(client, auth_headers, {
        "libraries": {"Movies": {section: {"anything": True}}},
    })
    assert response.status_code == 422
    detail = response.json()["detail"]
    assert detail[0]["path"] == f"libraries.Movies.{section}"
    assert (await session.execute(select(ConfigOverride))).scalars().all() == []


async def test_an_unknown_library_name_is_refused(client, auth_headers):
    response = await _put(client, auth_headers, {
        "libraries": {"Nope": {"badges": {"enabled": False}}},
    })
    assert response.status_code == 422
    joined = str(response.json()["detail"])
    assert "collections.libraries" in joined


async def test_an_emptied_library_block_is_refused_by_the_existing_guard(
    client, auth_headers,
):
    """The freezing hazard's other half, through the endpoint. ``{}`` would
    make ``document_paths`` report the whole block as ONE override and seed
    the editor into storing it wholesale on the next save; the existing
    ``empty_leaf_paths`` 422 guard refuses it, and needed no change for this
    shape."""
    response = await _put(client, auth_headers, {"libraries": {"Movies": {}}})
    assert response.status_code == 422
    detail = response.json()["detail"]
    assert detail[0]["path"] == "libraries.Movies"
    assert "empty object" in detail[0]["message"]


async def test_dropping_more_than_the_cap_needs_confirm_as_today(
    client, auth_headers,
):
    """``OVERRIDE_DROP_CAP`` counts in ``document_paths`` units, and T1
    pinned that walk as already correct for this shape -- so clearing a
    four-leaf library row trips the existing refusal with no new cap, no new
    flag and no new code."""
    populated = {"libraries": {"Movies": {"operations": {
        "enabled": False, "write_to_plex": False,
        "lock_apply": True, "unlock_apply": True,
    }}}}
    assert (await _put(client, auth_headers, populated)).status_code == 200

    refused = await _put(client, auth_headers, {})
    assert refused.status_code == 422
    message = refused.json()["detail"][0]["message"]
    assert "confirm: true" in message

    confirmed = await _put(client, auth_headers, {}, confirm=True)
    assert confirmed.status_code == 200, confirmed.text
    body = (await client.get("/api/config", headers=auth_headers)).json()
    assert body["overridden_paths"] == []


async def test_every_served_per_library_leaf_has_a_description(
    client, auth_headers,
):
    """Established fact i, closed. The map holds the shape ONCE under a ``{}``
    segment, because a library name is data and cannot be enumerated in a
    schema walk; the page substitutes the name in. This asserts the
    substitution actually resolves for a populated block, which the shipped
    empty default would never exercise."""
    assert (await _put(client, auth_headers, {"libraries": {"Movies": {
        "operations": {"write_to_plex": False},
        "badges": {"enabled": False},
        "maintenance": {"empty_trash": True},
    }}})).status_code == 200

    body = (await client.get("/api/config", headers=auth_headers)).json()
    descriptions = body["field_descriptions"]
    served = body["libraries"]["Movies"]
    for section, settings in served.items():
        if not isinstance(settings, dict):
            continue
        for name in settings:
            wildcard = f"libraries.{{}}.{section}.{name}"
            assert wildcard in descriptions, wildcard
            assert descriptions[wildcard].strip()
