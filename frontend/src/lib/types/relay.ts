/** A target's copy of a track that was close to, but not, a match. */
export interface Candidate {
	artist: string;
	title: string;
	recording_mbid?: string | null;
}
