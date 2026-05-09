import type { PageLoad } from './$types';
import type { AuthCheckResult, ServiceStatus } from '$lib/types/auth';

async function fetchServiceStatus(
	fetch: typeof globalThis.fetch,
	url: string
): Promise<{ status: ServiceStatus; [key: string]: unknown }> {
	try {
		const res = await fetch(url);
		if (!res.ok) return { status: 'needs-auth' };
		return await res.json();
	} catch {
		return { status: 'needs-auth' };
	}
}

/**
 * Fetch auth status for all services on page load.
 *
 * Each service has its own status endpoint; we fetch them concurrently.
 * Failures are caught per-service so one unavailable service doesn't block
 * the rest of the page from rendering.
 */
export const load: PageLoad = async ({ fetch }) => {
	const [plex, lastfm, listenbrainz] = await Promise.all([
		fetchServiceStatus(fetch, '/api/plex/auth/status'),
		fetchServiceStatus(fetch, '/api/lastfm/auth/status'),
		fetchServiceStatus(fetch, '/api/listenbrainz/auth/status'),
	]);

	const authStatus = { plex, lastfm, listenbrainz } as AuthCheckResult;
	return { authStatus };
};
