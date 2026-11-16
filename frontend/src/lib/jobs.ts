import type { TrackEvent } from '$lib/types/jobs';

/** Where a track event lands in a run's log and its per-destination tallies. */
export type LogBucket = 'written' | 'skipped' | 'failed' | 'unmatched';

export function bucketOf(evt: TrackEvent): LogBucket {
	if (evt.success) return 'written';
	if (evt.outcome === 'failed' || evt.error) return 'failed';
	if (evt.outcome === 'unmatched') return 'unmatched';
	return 'skipped';
}
