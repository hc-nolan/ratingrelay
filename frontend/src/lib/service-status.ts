import type { AuthCheckResult, ServiceStatus } from '$lib/types/auth';
import type { ServiceAccountItem, ServicesResponse } from '$lib/types/services';

/** Every service this relay can hold credentials for, in display order. */
export const SERVICE_ORDER = ['plex', 'lastfm', 'listenbrainz', 'hyperfix'] as const;

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
	const [plex, lastfm, listenbrainz, hyperfix] = await Promise.all([
		fetchStatus(fetch, '/api/plex/auth/status'),
		fetchStatus(fetch, '/api/lastfm/auth/status'),
		fetchStatus(fetch, '/api/listenbrainz/auth/status'),
		fetchStatus(fetch, '/api/hyperfix/auth/status')
	]);

	return { plex, lastfm, listenbrainz, hyperfix } as AuthCheckResult;
}

/**
 * Every connected account, for the services page and the flow builder.
 *
 * One request, not four: this is the list of what exists, including a second
 * account of a service. Each account's *live* status still comes from its own
 * service's `/auth/status`, which is what `loadServiceStatus` asks for.
 */
export async function loadAccounts(fetch: typeof globalThis.fetch): Promise<ServiceAccountItem[]> {
	try {
		const res = await fetch('/api/services');
		if (!res.ok) return [];
		const body: ServicesResponse = await res.json();
		return body.accounts;
	} catch {
		return [];
	}
}
