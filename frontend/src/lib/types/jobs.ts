export interface TrackEvent {
	artist: string;
	title: string;
	target: string;
	success: boolean;
	error?: string | null;
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
	events?: TrackEvent[];
}
