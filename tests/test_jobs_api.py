"""
API tests for the jobs endpoints — focused on dry_run behaviour.
"""
import json

import pytest

PLEX_SOURCE = {"service": "plex", "feedback_type": "love"}
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
    """Same source, target and direction in a different job: each undoes the other."""
    first = await client.post("/api/relay/jobs", json=PAYLOAD)
    assert first.status_code == 201

    resp = await client.post(
        "/api/relay/jobs",
        json={"source": PLEX_SOURCE, "targets": [{"service": "listenbrainz"}, LFM_TARGET]},
    )
    assert resp.status_code == 409
    assert first.json()["id"] in resp.json()["detail"]


async def test_opposite_directions_may_share_a_pair(client):
    lbz = {"service": "listenbrainz"}
    loves = await client.post(
        "/api/relay/jobs", json={"source": PLEX_SOURCE, "targets": [lbz]}
    )
    hates = {**PLEX_SOURCE, "feedback_type": "hate"}
    resp = await client.post("/api/relay/jobs", json={"source": hates, "targets": [lbz]})
    assert loves.status_code == 201
    assert resp.status_code == 201


async def test_a_plex_comparison_from_an_older_build_keeps_its_direction(client):
    """``comparison: lte`` was a hate relay. Pydantic ignores unknown keys, so
    without the legacy mapping it would come back as a love relay."""
    legacy = {"service": "plex", "rating_threshold": 2.0, "comparison": "lte"}
    lbz = {"service": "listenbrainz"}
    resp = await client.post("/api/relay/jobs", json={"source": legacy, "targets": [lbz]})

    assert resp.status_code == 201
    assert resp.json()["source_config"] == {"service": "plex", "feedback_type": "hate"}


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


async def test_cancelling_a_job_the_worker_just_claimed_flags_it_instead(client, db_engine):
    """The cancel read 'queued', the worker claimed it, the cancel wrote 'cancelled'
    over a running job. Now the cancel only wins on a job still waiting."""
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

    from ratingrelay.models.jobs import Job

    job_id = (await client.post("/api/relay/jobs", json=PAYLOAD)).json()["id"]
    factory = async_sessionmaker(db_engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as db:
        row = await db.get(Job, job_id)
        row.status = "running"
        db.add(row)
        await db.commit()

    resp = await client.delete(f"/api/relay/jobs/{job_id}")

    assert resp.status_code == 200
    assert resp.json()["status"] == "running"
    assert resp.json()["cancel_requested"] is True


async def test_cancelling_a_queued_job_cancels_it(client):
    job_id = (await client.post("/api/relay/jobs", json=PAYLOAD)).json()["id"]

    resp = await client.delete(f"/api/relay/jobs/{job_id}")

    assert resp.status_code == 200
    assert resp.json()["status"] == "cancelled"


@pytest.mark.parametrize(
    "source",
    [
        {"service": "plex", "feedback_type": "hate"},
        {"service": "listenbrainz", "feedback_type": "hate"},
    ],
)
async def test_a_hate_relay_to_lastfm_is_refused(client, source):
    resp = await client.post(
        "/api/relay/jobs", json={"source": source, "targets": [LFM_TARGET]}
    )

    assert resp.status_code == 422
    assert "hate relay" in resp.json()["detail"]
