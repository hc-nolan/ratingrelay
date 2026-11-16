import type { PageLoad } from './$types';
import { loadAccounts, loadServiceStatus } from '$lib/service-status';
import { loadThresholds } from '$lib/relay';

export const load: PageLoad = async ({ fetch }) => ({
	authStatus: await loadServiceStatus(fetch),
	accounts: await loadAccounts(fetch),
	thresholds: await loadThresholds(fetch)
});
