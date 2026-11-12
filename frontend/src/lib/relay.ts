const serviceLabels: Record<string, string> = {
	plex: 'Plex',
	lastfm: 'Last.fm',
	listenbrainz: 'ListenBrainz'
};

export function summarizeSource(cfg: Record<string, unknown>): string {
	const svc = cfg.service as string;
	if (svc === 'plex') {
		const cmp = cfg.comparison === 'gte' ? '≥' : '≤';
		const rating = (cfg.rating_threshold as number) / 2;
		return `Plex ${cmp} ${rating}★`;
	}
	if (svc === 'lastfm') return 'Last.fm loved';
	if (svc === 'listenbrainz') return `ListenBrainz ${cfg.feedback_type}d`;
	if (svc === 'reset') {
		const target = cfg.target_service as string;
		return `Reset · ${serviceLabels[target] ?? target}`;
	}
	return svc;
}

export function summarizeTargets(cfgs: Record<string, unknown>[]): string {
	return cfgs.map((c) => serviceLabels[c.service as string] ?? (c.service as string)).join(' + ');
}

export function statusColor(status: string): string {
	const map: Record<string, string> = {
		queued: 'status-queued',
		scheduled: 'status-scheduled',
		running: 'status-running',
		completed: 'status-completed',
		partial: 'status-partial',
		failed: 'status-failed',
		cancelled: 'status-cancelled'
	};
	return map[status] ?? 'status-queued';
}

export const frequencyOptions = [
	{ value: 60, label: 'Every hour' },
	{ value: 360, label: 'Every 6 hours' },
	{ value: 720, label: 'Every 12 hours' },
	{ value: 1440, label: 'Daily' },
	{ value: 10080, label: 'Weekly' }
];
