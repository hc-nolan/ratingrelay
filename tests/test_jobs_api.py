"""
API tests for the jobs endpoints — focused on dry_run behaviour.
"""
import json

import pytest

PLEX_SOURCE = {"service": "plex", "rating_threshold": 6.0, "comparison": "gte"}
LFM_TARGET = {"service": "lastfm"}

PAYLOAD = {"source": PLEX_SOURCE, "targets": [LFM_TARGET]}


# ---------------------------------------------------------------------------
# dry_run field on POST and GET
# ---------------------------------------------------------------------------


async def test_create_job_with_dry_run(client):
    resp = await client.post("/api/relay/jobs", json={**PAYLOAD, "dry_run": True})
    assert resp.status_code == 201
    body = resp.json()
    assert body["dry_run"] is True
    assert body["recurring"] is False


async def test_create_job_without_dry_run_defaults_false(client):
    resp = await client.post("/api/relay/jobs", json=PAYLOAD)
    assert resp.status_code == 201
    assert resp.json()["dry_run"] is False


async def test_dry_run_and_recurring_returns_400(client):
    resp = await client.post(
        "/api/relay/jobs",
        json={**PAYLOAD, "dry_run": True, "recurring": True, "interval_minutes": 60},
    )
    assert resp.status_code == 400


# ---------------------------------------------------------------------------
# Duplicate-check exclusion
# ---------------------------------------------------------------------------


async def test_dry_run_not_blocked_by_active_real_job(client):
    """A dry-run job is accepted even when a real job with matching config is active."""
    real = await client.post("/api/relay/jobs", json=PAYLOAD)
    assert real.status_code == 201

    dry = await client.post("/api/relay/jobs", json={**PAYLOAD, "dry_run": True})
    assert dry.status_code == 201


async def test_real_job_not_blocked_by_active_dry_run(client):
    """A real job is accepted even when a dry-run job with matching config is queued."""
    dry = await client.post("/api/relay/jobs", json={**PAYLOAD, "dry_run": True})
    assert dry.status_code == 201

    real = await client.post("/api/relay/jobs", json=PAYLOAD)
    assert real.status_code == 201


async def test_two_dry_run_jobs_both_accepted(client):
    """Dry-run jobs are never deduplicated against each other."""
    r1 = await client.post("/api/relay/jobs", json={**PAYLOAD, "dry_run": True})
    r2 = await client.post("/api/relay/jobs", json={**PAYLOAD, "dry_run": True})
    assert r1.status_code == 201
    assert r2.status_code == 201


# ---------------------------------------------------------------------------
# Existing duplicate check still works for real jobs
# ---------------------------------------------------------------------------


async def test_duplicate_real_jobs_blocked(client):
    """Real-job deduplication is unchanged."""
    r1 = await client.post("/api/relay/jobs", json=PAYLOAD)
    r2 = await client.post("/api/relay/jobs", json=PAYLOAD)
    assert r1.status_code == 201
    assert r2.status_code == 409


# ---------------------------------------------------------------------------
# dry_run included in list and detail responses
# ---------------------------------------------------------------------------


async def test_dry_run_in_list_response(client):
    await client.post("/api/relay/jobs", json={**PAYLOAD, "dry_run": True})
    jobs = (await client.get("/api/relay/jobs")).json()
    assert any(j["dry_run"] is True for j in jobs)


async def test_dry_run_in_detail_response(client):
    job_id = (await client.post("/api/relay/jobs", json={**PAYLOAD, "dry_run": True})).json()["id"]
    detail = (await client.get(f"/api/relay/jobs/{job_id}")).json()
    assert detail["dry_run"] is True


# ---------------------------------------------------------------------------
# Mass-undo override and relays that would share sync records
# ---------------------------------------------------------------------------


async def test_allow_mass_undo_and_recurring_returns_400(client):
    resp = await client.post(
        "/api/relay/jobs",
        json={**PAYLOAD, "allow_mass_undo": True, "recurring": True, "interval_minutes": 60},
    )
    assert resp.status_code == 400


async def test_allow_mass_undo_is_stored_and_returned(client):
    resp = await client.post("/api/relay/jobs", json={**PAYLOAD, "allow_mass_undo": True})
    assert resp.status_code == 201
    assert resp.json()["allow_mass_undo"] is True
    plain = await client.post(
        "/api/relay/jobs", json={**PAYLOAD, "targets": [{"service": "listenbrainz"}]}
    )
    assert plain.json()["allow_mass_undo"] is False


async def test_the_override_may_run_beside_its_recurring_relay(client):
    recurring = await client.post(
        "/api/relay/jobs", json={**PAYLOAD, "recurring": True, "interval_minutes": 60}
    )
    assert recurring.status_code == 201

    override = await client.post("/api/relay/jobs", json={**PAYLOAD, "allow_mass_undo": True})
    assert override.status_code == 201


async def test_a_relay_that_would_fight_an_active_one_is_refused(client):
    """Same source, target and direction, different threshold: each undoes the other."""
    first = await client.post("/api/relay/jobs", json=PAYLOAD)
    assert first.status_code == 201

    looser = {**PLEX_SOURCE, "rating_threshold": 4.0}
    resp = await client.post(
        "/api/relay/jobs",
        json={"source": looser, "targets": [{"service": "listenbrainz"}, LFM_TARGET]},
    )
    assert resp.status_code == 409
    assert first.json()["id"] in resp.json()["detail"]


async def test_opposite_directions_may_share_a_pair(client):
    loves = await client.post("/api/relay/jobs", json=PAYLOAD)
    hates = {**PLEX_SOURCE, "rating_threshold": 2.0, "comparison": "lte"}
    resp = await client.post("/api/relay/jobs", json={"source": hates, "targets": [LFM_TARGET]})
    assert loves.status_code == 201
    assert resp.status_code == 201


# ---------------------------------------------------------------------------
# Recurring bounds
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("interval", [0, -5, 365 * 24 * 60 + 1])
async def test_an_interval_out_of_bounds_is_rejected(client, interval):
    """0 or less rescheduled the job into the past, so it ran back to back."""
    resp = await client.post(
        "/api/relay/jobs", json={**PAYLOAD, "recurring": True, "interval_minutes": interval}
    )
    assert resp.status_code == 422


async def test_a_reset_cannot_be_recurring(client):
    """A wipe on a timer would clear every rating each time it came round."""
    resp = await client.post(
        "/api/relay/jobs",
        json={
            "source": {"service": "reset", "target_service": "lastfm", "all": True},
            "targets": [],
            "recurring": True,
            "interval_minutes": 60,
        },
    )
    assert resp.status_code == 400

