/** A target's copy of a track that was close to, but not, a match. */
export interface Candidate {
	artist: string;
	title: string;
	recording_mbid?: string | null;
}

/** The four things the FROM node can be: three services, or a reset. */
export type SourceId = 'plex' | 'lastfm' | 'listenbrainz' | 'reset';

/** A service that can receive writes. */
export type TargetId = 'plex' | 'lastfm' | 'listenbrainz';

export interface SourceOption {
	id: SourceId;
	name: string;
}

export interface TargetOption {
	id: TargetId;
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

/** The Plex ratings (0–10) that count as a love and a hate. Server settings, read-only here. */
export interface PlexThresholds {
	love_threshold: number;
	hate_threshold: number;
}
