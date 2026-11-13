/**
 * Presentation of time and status.
 *
 * Timestamps are stored in UTC and shown in the browser's zone; a run is
 * described by when it happened ("today 14:32"), not by a raw ISO string. Every
 * page borrows these, so there is one answer to "what colour is partial".
 */

const TIME = new Intl.DateTimeFormat(undefined, { hour: '2-digit', minute: '2-digit' });
const DAY = new Intl.DateTimeFormat(undefined, {
	weekday: 'short',
	day: 'numeric',
	month: 'short'
});

function startOfDay(d: Date): number {
	return new Date(d.getFullYear(), d.getMonth(), d.getDate()).getTime();
}

/** How many calendar days ago was this, in the viewer's zone. */
function daysAgo(d: Date): number {
	return Math.round((startOfDay(new Date()) - startOfDay(d)) / 86_400_000);
}

/** "today 14:32", "yesterday 09:10", "Wed 12 Nov 09:10". */
export function whenLabel(iso?: string | null): string {
	if (!iso) return '';
	const d = new Date(iso);
	const days = daysAgo(d);
	if (days === 0) return `today ${TIME.format(d)}`;
	if (days === 1) return `yesterday ${TIME.format(d)}`;
	return `${DAY.format(d)} ${TIME.format(d)}`;
}

/** The heading a run belongs under on the history page: Today, Yesterday, … */
export function dayLabel(iso: string): string {
	const d = new Date(iso);
	const days = daysAgo(d);
	if (days === 0) return 'Today';
	if (days === 1) return 'Yesterday';
	if (days < 7) return DAY.format(d);
	return DAY.format(d);
}

/** A stable key so consecutive runs from the same day land in one group. */
export function dayKey(iso: string): string {
	return new Date(iso).toDateString();
}

/** "2m 21s", "18.3s" — how long a run took, or how long it has been going. */
export function durationLabel(start?: string | null, end?: string | null): string {
	if (!start) return '';
	const from = new Date(start).getTime();
	const to = end ? new Date(end).getTime() : Date.now();
	const secs = Math.max(0, Math.round((to - from) / 1000));
	if (secs < 60) return `${secs}s`;
	const m = Math.floor(secs / 60);
	const s = secs % 60;
	if (m < 60) return s ? `${m}m ${s}s` : `${m}m`;
	const h = Math.floor(m / 60);
	return `${h}h ${m % 60}m`;
}

export const STATUS_LABEL: Record<string, string> = {
	// A run.
	queued: 'Queued',
	scheduled: 'Scheduled',
	running: 'Running',
	completed: 'Completed',
	partial: 'Partial',
	failed: 'Failed',
	cancelled: 'Cancelled',
	// A service connection, which shares the same pill.
	checking: 'Checking…',
	connected: 'Connected',
	'needs-auth': 'Not connected',
	disabled: 'Disabled',
	error: 'Error'
};

export function statusLabel(status: string): string {
	return STATUS_LABEL[status] ?? status;
}

/**
 * One meaning per tone. A zero error count is never painted red: the tone is
 * the run's verdict, not the colour of a number that happens to be bad when
 * larger than zero.
 */
export type Tone = 'ok' | 'warn' | 'err' | 'info' | 'muted';

export function statusTone(status: string): Tone {
	const map: Record<string, Tone> = {
		completed: 'ok',
		partial: 'warn',
		failed: 'err',
		running: 'info',
		queued: 'muted',
		scheduled: 'muted',
		cancelled: 'muted',
		// A connection: the same tones mean the same things.
		connected: 'ok',
		checking: 'info',
		'needs-auth': 'warn',
		disabled: 'muted',
		error: 'err'
	};
	return map[status] ?? 'muted';
}

export const TONE_DOT: Record<Tone, string> = {
	ok: 'bg-ok',
	warn: 'bg-warn',
	err: 'bg-destructive',
	info: 'bg-info animate-pulse',
	muted: 'bg-muted-foreground/50'
};

export const TONE_TEXT: Record<Tone, string> = {
	ok: 'text-ok',
	warn: 'text-warn',
	err: 'text-destructive',
	info: 'text-info',
	muted: 'text-muted-foreground'
};

export const TONE_CHIP: Record<Tone, string> = {
	ok: 'border-ok/30 bg-ok/10 text-ok',
	warn: 'border-warn/30 bg-warn/10 text-warn',
	err: 'border-destructive/30 bg-destructive/10 text-destructive',
	info: 'border-info/30 bg-info/10 text-info',
	muted: 'border-border bg-muted/40 text-muted-foreground'
};
