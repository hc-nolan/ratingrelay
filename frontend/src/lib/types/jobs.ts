/** A typed engine outcome: `written | reverted | skipped | unmatched | failed`. */
export type Outcome = 'written' | 'reverted' | 'skipped' | 'unmatched' | 'failed';

export interface TrackEvent {
	artist: string;
	title: string;
	target: string;
	success: boolean;
	error?: string | null;
	/** What the engine decided. Select on `reason`, never on `error` text. */
	outcome?: Outcome | string | null;
	reason?: string | null;
	candidates?: unknown[];
}

export interface TargetTally {
	service: string;
	written: number;
	failed: number;
	skipped: number;
	unmatched: number;
}

export interface JobData {
	id: string;
	status: string;
	source_config: Record<string, unknown>;
	targets_config: Record<string, unknown>[];
	recurring: boolean;
	interval_minutes?: number;
	run_at?: string;
	created_at: string;
	started_at?: string;
	completed_at?: string;
	tracks_fetched: number;
	tracks_ok: number;
	tracks_err: number;
	tracks_skipped: number;
	dry_run: boolean;
	cancel_requested?: boolean;
	events?: TrackEvent[];
	/** One tally per destination; `tracks_ok` is the sum across them. */
	by_target?: TargetTally[];
}
