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
