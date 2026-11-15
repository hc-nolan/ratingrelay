export const serviceLabels: Record<string, string> = {
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

/** Tailwind classes for a job's status dot, so no page has to own the rule. */
export function statusColor(status: string): string {
	const map: Record<string, string> = {
		queued: 'bg-muted-foreground/50',
		scheduled: 'bg-muted-foreground/50',
		running: 'bg-info animate-pulse',
		completed: 'bg-ok',
		partial: 'bg-warn',
		failed: 'bg-destructive',
		cancelled: 'bg-muted-foreground/30'
	};
	return map[status] ?? 'bg-muted-foreground/50';
}

/** "in 3h 12m", "soon" — how long until a scheduled job next runs. */
export function formatNextRun(runAt: string): string {
	const diff = new Date(runAt).getTime() - Date.now();
	if (diff <= 0) return 'soon';
	const h = Math.floor(diff / 3600000);
	const m = Math.floor((diff % 3600000) / 60000);
	if (h >= 24) return `in ${Math.floor(h / 24)}d ${h % 24}h`;
	if (h > 0) return `in ${h}h ${m}m`;
	return `in ${m}m`;
}

/** "Every 6 hours" → "every 6 hours", for the small line under a job summary. */
export function describeInterval(minutes: number): string {
	const known = frequencyOptions.find((o) => o.value === minutes);
	if (!known) return `every ${minutes}m`;
	return known.label.toLowerCase();
}

export const frequencyOptions = [
	{ value: 60, label: 'Every hour' },
	{ value: 360, label: 'Every 6 hours' },
	{ value: 720, label: 'Every 12 hours' },
	{ value: 1440, label: 'Daily' },
	{ value: 10080, label: 'Weekly' }
];

/** The repeat control offers "once" where the API offers intervals. */
export const repeatOptions = [{ value: 0, label: 'Once' }, ...frequencyOptions];

/** Plex stores ratings as 0–10; the UI says stars. */
export const plexStarOptions = [
	{ value: 1, label: '0.5 stars' },
	{ value: 2, label: '1 star' },
	{ value: 3, label: '1.5 stars' },
	{ value: 4, label: '2 stars' },
	{ value: 5, label: '2.5 stars' },
	{ value: 6, label: '3 stars' },
	{ value: 7, label: '3.5 stars' },
	{ value: 8, label: '4 stars' },
	{ value: 9, label: '4.5 stars' },
	{ value: 10, label: '5 stars' }
];
