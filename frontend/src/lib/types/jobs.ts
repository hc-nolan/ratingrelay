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
	allow_mass_undo?: boolean;
	/** This relay's targets are not authoritative: writes they dropped are re-applied. */
	reapply_target_drift?: boolean;
	cancel_requested?: boolean;
	events?: TrackEvent[];
	/** One tally per destination; `tracks_ok` is the sum across them. */
	by_target?: TargetTally[];
}

/** The body `POST /api/relay/jobs` takes. */
export interface JobCreateRequest {
	source: Record<string, unknown>;
	targets: Record<string, unknown>[];
	recurring?: boolean;
	interval_minutes?: number | null;
	dry_run?: boolean;
	/** One run's override of the mass-undo guard; refused together with `recurring`. */
	allow_mass_undo?: boolean;
	/** Write again what this relay's targets have since dropped. A policy, so a
	 * scheduled relay keeps it. */
	reapply_target_drift?: boolean;
}

/** A destination whose reconcile was refused because it would undo most of it. */
export interface MassUndoRefusal {
	target: string;
	/** How many recorded writes the refused reconcile would have undone. */
	count: number;
	/** The engine's own words, e.g. "would undo 120 of 130 recorded writes". */
	detail: string;
}
