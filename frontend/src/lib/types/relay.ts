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
	ratingThreshold: number;
	comparison: 'gte' | 'lte';
}

export interface ListenBrainzSourceConfig {
	feedbackType: 'love' | 'hate';
}

export interface PlexTargetConfig {
	rating: number;
}

export interface ListenBrainzTargetConfig {
	feedbackType: 'love' | 'hate';
}
