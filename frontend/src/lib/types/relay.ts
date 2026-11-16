/** A target's copy of a track that was close to, but not, a match. */
export interface Candidate {
	artist: string;
	title: string;
	recording_mbid?: string | null;
}

/**
 * A source or target is an account **ref** (`lastfm`, or `lastfm:old` for a
 * second account of it), plus the literal `reset` as a source. Refs come from
 * `/api/services`, so the UI does not carry a list of services of its own.
 */
export type SourceRef = string;

/**
 * The three services, plus the reset node: only what a *mark* is drawn for. It
 * says nothing about which account — that is the ref above.
 */
export type ServiceMarkId = 'plex' | 'lastfm' | 'listenbrainz' | 'reset';

/** The mark to draw for an account ref; `reset` is the one non-service node. */
export function markFor(ref: string): ServiceMarkId {
	if (ref === 'reset') return 'reset';
	const kind = ref.split(':')[0];
	return (['plex', 'lastfm', 'listenbrainz'].includes(kind) ? kind : 'reset') as ServiceMarkId;
}

export interface SourceOption {
	id: SourceRef;
	name: string;
}

export interface TargetOption {
	id: SourceRef;
	name: string;
	/** What the node says when it is not allocated yet. */
	hint: string;
}

export interface PlexSourceConfig {
	feedbackType: 'love' | 'hate';
}

export interface ListenBrainzSourceConfig {
	feedbackType: 'love' | 'hate';
}

/**
 * The Plex ratings (0–10) that count as a love and a hate, shared by every relay.
 * A locked one is pinned in the server settings and can't be changed here.
 */
export interface PlexThresholds {
	love_threshold: number;
	hate_threshold: number;
	love_locked: boolean;
	hate_locked: boolean;
}

export type Direction = 'love' | 'hate';
