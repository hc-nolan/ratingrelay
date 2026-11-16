import type { AuthCheckResult, ServiceStatus } from '$lib/types/auth';

/** The three services this relay can hold credentials for, in display order. */
export const SERVICE_ORDER = ['plex', 'lastfm', 'listenbrainz'] as const;

async function fetchStatus(
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
 * Fetch auth status for all services.
 *
 * Each service has its own endpoint and they are fetched concurrently; a
 * failure is caught per service so one unreachable target does not blank the
 * page. Both the setup wizard and the services page read the same shape.
 */
export async function loadServiceStatus(fetch: typeof globalThis.fetch): Promise<AuthCheckResult> {
	const [plex, lastfm, listenbrainz] = await Promise.all([
		fetchStatus(fetch, '/api/plex/auth/status'),
		fetchStatus(fetch, '/api/lastfm/auth/status'),
		fetchStatus(fetch, '/api/listenbrainz/auth/status')
	]);

	return { plex, lastfm, listenbrainz } as AuthCheckResult;
}
