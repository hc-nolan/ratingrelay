import type { PageLoad } from './$types';
import type { AuthCheckResult } from '$lib/types/auth';

/**
 * Fetch auth status for all services on page load.
 *
 * Each service has its own status endpoint; we fetch them concurrently.
 * Failures are caught per-service so one unavailable service doesn't block
 * the rest of the page from rendering.
 *
 * TODO: add lastfm + listenbrainz status endpoints when those routes exist.
 */
export const load: PageLoad = async ({ fetch }) => {
	async function fetchPlexStatus() {
		try {
			const res = await fetch('/api/plex/auth/status');
			if (!res.ok) return { status: 'needs-auth' as const };
			return await res.json();
		} catch {
			return { status: 'needs-auth' as const };
		}
	}

	async function fetchLastFMStatus() {
		try {
			const res = await fetch('/api/lastfm/auth/status');
			if (!res.ok) return { status: 'needs-auth' as const };
			return await res.json();
		} catch {
			return { status: 'needs-auth' as const };
		}
	}
	async function fetchListenBrainzStatus() {
		try {
			const res = await fetch('/api/listenbrainz/auth/status');
			if (!res.ok) return { status: 'needs-auth' as const };
			return await res.json();
		} catch {
			return { status: 'needs-auth' as const };
		}
	}

	const [plex, lastfm, listenbrainz] = await Promise.all([
		fetchPlexStatus(),
		fetchLastFMStatus(),
		fetchListenBrainzStatus(),
	]);

	const authStatus: AuthCheckResult = { plex, lastfm, listenbrainz };
	return { authStatus };
};
