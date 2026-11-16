import type { PageLoad } from './$types';
import { loadServiceStatus } from '$lib/service-status';
import { loadThresholds } from '$lib/relay';

export const load: PageLoad = async ({ fetch }) => ({
	authStatus: await loadServiceStatus(fetch),
	thresholds: await loadThresholds(fetch)
});
