import type { PageLoad } from './$types';

export const prerender = false;
export const ssr = false;

export const load: PageLoad = async ({ params, fetch }) => {
	try {
		const res = await fetch(`/api/relay/jobs/${params.id}`);
		if (!res.ok) return { job: null, jobId: params.id };
		return { job: await res.json(), jobId: params.id };
	} catch {
		return { job: null, jobId: params.id };
	}
};
