import type { PageLoad } from './$types';
import { loadServiceStatus } from '$lib/service-status';

export const load: PageLoad = async ({ fetch }) => ({
	authStatus: await loadServiceStatus(fetch)
});
