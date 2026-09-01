"""Snapshots, restore, and export/import for the overrides document.

The 2026-09-01 incident: seventeen stored overrides went to zero through a
200-OK write, and the only copy that survived was one an operator happened to
have in a chat window. Everything here exists so that is never again the
recovery plan.

Two rules are load-bearing and each has a test whose failure message says which
one broke.

1. **A snapshot and its write commit together.** The capture is an insert in
   the same session and the same transaction as the upsert. A snapshot without
   its write, or a write without its snapshot, is worse than neither.
2. **Restore and import are SAVES.** Both run the full validation and every
   write guard. A restore is itself snapshotted first, so it is undoable; and a
   snapshot taken before a config section left the schema must fail loudly or
   be stripped, never brick the pod.

The fixtures are the config editor's own -- one app, one client, one login --
imported rather than rebuilt so the two files cannot drift about what a
deployment looks like.
"""
from copy import deepcopy

import pytest
from sqlalchemy import select

from autoposter.api import routes as routes_module
from autoposter.config.overrides import EMPTY_DOCUMENT_REVISION
from autoposter.config.snapshots import SNAPSHOT_RETENTION
from autoposter.db.models import ConfigOverride, ConfigOverrideSnapshot

# Fixtures, re-exported. pytest's default import mode puts `tests/` on
# sys.path, so this is a plain module import.
from test_api_config_editor import (  # noqa: F401
    THE_INCIDENT_DOCUMENT,
    app,
    auth_headers,
    client,
    config_file,
)

pytestmark = pytest.mark.asyncio


async def _store(client, auth_headers, document: dict, **extra) -> None:
    response = await client.put(
        "/api/config/overrides",
        headers=auth_headers,
        json={"document": document, **extra},
    )
    assert response.status_code == 200, response.text


async def _snapshots(session) -> list[ConfigOverrideSnapshot]:
    result = await session.execute(
        select(ConfigOverrideSnapshot).order_by(ConfigOverrideSnapshot.id.desc())
    )
    return list(result.scalars().all())


# --- capture --------------------------------------------------------------


async def test_a_save_snapshots_the_document_it_is_about_to_replace(
    client, auth_headers, session
):
    """The OUTGOING document, so the newest snapshot is always "what you had
    before the last save"."""
    await _store(client, auth_headers, THE_INCIDENT_DOCUMENT)
    edited = deepcopy(THE_INCIDENT_DOCUMENT)
    edited["workers"] = 11
    await _store(client, auth_headers, edited)

    rows = await _snapshots(session)
    assert len(rows) == 1, "the first save had nothing to snapshot; the second did"
    assert rows[0].document == THE_INCIDENT_DOCUMENT
    assert rows[0].path_count == 12
    assert rows[0].reason == "save"


async def test_the_first_save_of_a_fresh_deployment_snapshots_nothing(
    client, auth_headers, session
):
    """There is nothing to restore to, and a fresh deployment would otherwise
    accumulate empty rows for as long as nobody saved anything."""
    await _store(client, auth_headers, THE_INCIDENT_DOCUMENT)
    assert await _snapshots(session) == []


async def test_a_refused_write_leaves_no_snapshot_orphan(
    client, auth_headers, session
):
    """Same transaction, so a write that never happened has no history entry
    claiming it did. Three refusal routes, one for each guard that can fire
    after the capture point is reached."""
    await _store(client, auth_headers, THE_INCIDENT_DOCUMENT)

    # The drop cap.
    await client.put(
        "/api/config/overrides", headers=auth_headers, json={"document": {"workers": 9}}
    )
    # The empty-document guard.
    await client.put(
        "/api/config/overrides", headers=auth_headers, json={"document": {}}
    )
    # The revision check.
    edited = deepcopy(THE_INCIDENT_DOCUMENT)
    edited["workers"] = 11
    await client.put(
        "/api/config/overrides",
        headers=auth_headers,
        json={"document": edited, "expected_revision": EMPTY_DOCUMENT_REVISION},
    )

    assert await _snapshots(session) == [], (
        "a refused write left a snapshot behind. The capture and the upsert "
        "must commit together or not at all"
    )
    stored = (await session.execute(select(ConfigOverride))).scalar_one().document
    assert stored == THE_INCIDENT_DOCUMENT


async def test_a_failure_between_capture_and_commit_leaves_no_snapshot_orphan(
    client, auth_headers, session, monkeypatch
):
    """The refusal test above only reaches refusals raised BEFORE
    ``capture_snapshot`` runs -- it would pass even if ``capture_snapshot``
    opened its own session and committed independently. This is the other
    half: something fails AFTER the snapshot row is added but BEFORE the
    transaction commits. Only a same-transaction rollback keeps the promise
    that a snapshot and its write commit together, so the failure is placed
    at the ``EventLog`` insert -- the last thing that happens after the
    capture and before ``session.commit()``.
    """
    await _store(client, auth_headers, THE_INCIDENT_DOCUMENT)

    def _raiser(*args, **kwargs):
        raise RuntimeError("simulated failure between capture and commit")

    monkeypatch.setattr(routes_module, "EventLog", _raiser)

    edited = deepcopy(THE_INCIDENT_DOCUMENT)
    edited["workers"] = 11
    with pytest.raises(RuntimeError):
        await client.put(
            "/api/config/overrides", headers=auth_headers, json={"document": edited}
        )

    assert await _snapshots(session) == [], (
        "a snapshot committed even though the write it belongs to failed "
        "before its own commit"
    )
    stored = (await session.execute(select(ConfigOverride))).scalar_one().document
    assert stored == THE_INCIDENT_DOCUMENT


async def test_the_apply_arm_records_its_own_reason(
    client, auth_headers, session
):
    await _store(client, auth_headers, THE_INCIDENT_DOCUMENT)
    edited = deepcopy(THE_INCIDENT_DOCUMENT)
    edited["workers"] = 11
    response = await client.post(
        "/api/config/apply", headers=auth_headers, json={"document": edited}
    )
    assert response.status_code == 200, response.text

    rows = await _snapshots(session)
    assert [row.reason for row in rows] == ["apply"]


async def test_snapshots_are_pruned_to_the_retention_inline(
    client, auth_headers, session
):
    """Unbounded growth on a config table is not acceptable, and a scheduled
    pruner is more machinery than twenty rows deserve.

    Ordered and asserted on `id`, never on `created_at`: this project has a
    recorded environment whose container clock steps backwards.
    """
    for n in range(SNAPSHOT_RETENTION + 5):
        edited = deepcopy(THE_INCIDENT_DOCUMENT)
        edited["workers"] = n + 1
        await _store(client, auth_headers, edited)

    rows = await _snapshots(session)
    assert len(rows) == SNAPSHOT_RETENTION
    # The survivors are the newest, and the newest holds the document the last
    # save displaced.
    assert rows[0].document["workers"] == SNAPSHOT_RETENTION + 4


# --- the listing and the single read --------------------------------------


async def test_the_listing_carries_metadata_and_no_documents(
    client, auth_headers
):
    """A listing that shipped twenty full config documents is a payload nobody
    asked for, and one of them holds a push token."""
    await _store(client, auth_headers, THE_INCIDENT_DOCUMENT)
    edited = deepcopy(THE_INCIDENT_DOCUMENT)
    edited["workers"] = 11
    await _store(client, auth_headers, edited)

    body = (await client.get("/api/config/snapshots", headers=auth_headers)).json()
    assert len(body) == 1
    assert set(body[0]) == {"id", "created_at", "path_count", "reason"}
    assert body[0]["path_count"] == 12
    assert body[0]["reason"] == "save"


async def test_the_listing_is_newest_first(client, auth_headers):
    for n in range(3):
        edited = deepcopy(THE_INCIDENT_DOCUMENT)
        edited["workers"] = n + 1
        await _store(client, auth_headers, edited)

    body = (await client.get("/api/config/snapshots", headers=auth_headers)).json()
    ids = [row["id"] for row in body]
    assert ids == sorted(ids, reverse=True)


async def test_reading_one_snapshot_redacts_it_exactly_as_the_config_does(
    client, auth_headers
):
    """A snapshot holds notifications.url in full. Serving it raw would leak a
    push token the live config endpoint carefully withholds -- through a new
    endpoint, which is exactly how that kind of hole gets made."""
    await _store(
        client,
        auth_headers,
        {"notifications": {"enabled": True, "url": "https://kuma.example.com/api/push/s3cr3t"}},
    )
    await _store(
        client,
        auth_headers,
        {"notifications": {"enabled": True, "url": "https://kuma.example.com/api/push/s3cr3t"}, "workers": 9},
    )
    [row] = (await client.get("/api/config/snapshots", headers=auth_headers)).json()

    body = (
        await client.get(f"/api/config/snapshots/{row['id']}", headers=auth_headers)
    ).json()
    assert "s3cr3t" not in str(body)
    assert body["document"]["notifications"]["url"] == "kuma.example.com"
    assert body["document"]["notifications"]["enabled"] is True


async def test_reading_a_snapshot_that_is_not_there_is_a_404(client, auth_headers):
    response = await client.get("/api/config/snapshots/999999", headers=auth_headers)
    assert response.status_code == 404


# --- restore --------------------------------------------------------------


async def test_the_restore_round_trip_returns_the_exact_document(
    client, auth_headers, session, app
):
    """Snapshot, wipe, restore, and the stored document is byte-equal to what
    was there before. This is the deliverable the operator asked for."""
    await _store(client, auth_headers, THE_INCIDENT_DOCUMENT)
    # A second save, so there is a snapshot holding the incident document.
    await _store(client, auth_headers, {"workers": 9}, confirm=True)
    [row] = (await client.get("/api/config/snapshots", headers=auth_headers)).json()

    response = await client.post(
        f"/api/config/snapshots/{row['id']}/restore",
        headers=auth_headers,
        json={"confirm": True},
    )

    assert response.status_code == 200, response.text
    stored = (await session.execute(select(ConfigOverride))).scalar_one().document
    assert stored == THE_INCIDENT_DOCUMENT, "the restore did not round-trip"
    assert app.state.config.workers == 9
    assert app.state.config.collections.separator_style == "sand"


async def test_a_restore_is_itself_snapshotted_first(
    client, auth_headers, session
):
    """So an operator who restores the wrong one can get back. A restore that
    was not undoable would be a second way to lose a configuration."""
    await _store(client, auth_headers, THE_INCIDENT_DOCUMENT)
    await _store(client, auth_headers, {"workers": 9}, confirm=True)
    [row] = (await client.get("/api/config/snapshots", headers=auth_headers)).json()

    await client.post(
        f"/api/config/snapshots/{row['id']}/restore",
        headers=auth_headers,
        json={"confirm": True},
    )

    rows = await _snapshots(session)
    assert [r.reason for r in rows] == ["restore", "save"]
    assert rows[0].document == {"workers": 9}


async def test_a_restore_respects_the_drop_cap_without_confirm(
    client, auth_headers, session
):
    """Restore is a save, not a bypass. Restoring a two-path snapshot over a
    twelve-path store drops ten, which is exactly the thing the cap is for."""
    await _store(client, auth_headers, {"workers": 9, "badges": {"enabled": False}})
    await _store(client, auth_headers, THE_INCIDENT_DOCUMENT)
    [row] = (await client.get("/api/config/snapshots", headers=auth_headers)).json()

    response = await client.post(
        f"/api/config/snapshots/{row['id']}/restore", headers=auth_headers, json={}
    )

    assert response.status_code == 422
    assert "confirm: true" in response.json()["detail"][0]["message"]
    stored = (await session.execute(select(ConfigOverride))).scalar_one().document
    assert stored == THE_INCIDENT_DOCUMENT


async def test_a_restore_re_validates_against_the_config_on_file(
    client, auth_headers, config_file, session
):
    """The mounted YAML may have moved under the snapshot since it was taken,
    and a snapshot from before a schema change must fail loudly rather than
    brick the pod on the next boot."""
    await _store(client, auth_headers, {"workers": 9, "badges": {"enabled": False}})
    await _store(client, auth_headers, THE_INCIDENT_DOCUMENT)
    [row] = (await client.get("/api/config/snapshots", headers=auth_headers)).json()

    # Make the snapshot invalid the way a schema change would: a value the
    # merged config can no longer accept.
    stored_row = (
        await session.execute(select(ConfigOverrideSnapshot))
    ).scalar_one()
    stored_row.document = {"workers": "not a number"}
    await session.commit()

    response = await client.post(
        f"/api/config/snapshots/{row['id']}/restore",
        headers=auth_headers,
        json={"confirm": True},
    )
    assert response.status_code == 422
    assert response.json()["detail"][0]["path"] == "workers"


async def test_a_restore_strips_a_migrated_section(
    client, auth_headers, session
):
    """The design trap: `load_overrides_document` strips MIGRATED_SECTIONS on
    read, but a raw snapshot row still holds one. Without the strip, restoring
    an old snapshot 422s on a key the editor can no longer produce -- so the
    one recovery path would be unusable on exactly the old snapshots recovery
    is for."""
    await _store(client, auth_headers, {"workers": 9, "badges": {"enabled": False}})
    await _store(client, auth_headers, {"workers": 9, "badges": {"enabled": True}})
    [row] = (await client.get("/api/config/snapshots", headers=auth_headers)).json()

    stored_row = (
        await session.execute(select(ConfigOverrideSnapshot))
    ).scalar_one()
    stored_row.document = {"workers": 9, "version_check": {"enabled": True}}
    await session.commit()

    response = await client.post(
        f"/api/config/snapshots/{row['id']}/restore",
        headers=auth_headers,
        json={"confirm": True},
    )

    assert response.status_code == 200, response.text
    stored = (await session.execute(select(ConfigOverride))).scalar_one().document
    assert stored == {"workers": 9}


async def test_a_restore_honours_the_revision_check(client, auth_headers):
    """The safety UI sits on the same page as the editor, so a restore can be
    stale for exactly the same reason a save can."""
    await _store(client, auth_headers, {"workers": 9, "badges": {"enabled": False}})
    await _store(client, auth_headers, THE_INCIDENT_DOCUMENT)
    [row] = (await client.get("/api/config/snapshots", headers=auth_headers)).json()

    response = await client.post(
        f"/api/config/snapshots/{row['id']}/restore",
        headers=auth_headers,
        json={"confirm": True, "expected_revision": EMPTY_DOCUMENT_REVISION},
    )
    assert response.status_code == 409


async def test_restoring_a_snapshot_that_is_not_there_is_a_404(
    client, auth_headers
):
    response = await client.post(
        "/api/config/snapshots/999999/restore", headers=auth_headers, json={}
    )
    assert response.status_code == 404


async def test_every_snapshot_endpoint_needs_a_session(client):
    """The same gate every other config endpoint sits behind. A history of the
    configuration is not less sensitive than the configuration."""
    assert (await client.get("/api/config/snapshots")).status_code == 401
    assert (await client.get("/api/config/snapshots/1")).status_code == 401
    assert (
        await client.post("/api/config/snapshots/1/restore", json={})
    ).status_code == 401
