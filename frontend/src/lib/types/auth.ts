/**
 * Auth status for a single service.
 *
 * - `checking`   – actively verifying credentials
 * - `connected`  – credentials found and connection verified
 * - `needs-auth` – no credentials found, or connection check failed
 * - `disabled`   – service is explicitly disabled in config
 * - `error`      – unexpected error during check
 */
export type ServiceStatus = 'checking' | 'connected' | 'needs-auth' | 'disabled' | 'error';

export interface PlexAuthState {
	status: ServiceStatus;
	/** Plex username once connected */
	username?: string;
	/** Plex server name once a server has been selected */
	server_name?: string;
	/** Plex server URL once a server has been selected */
	server_url?: string;
}

export interface LastFMAuthState {
	status: ServiceStatus;
	username?: string;
}

export interface ListenBrainzAuthState {
	status: ServiceStatus;
	username?: string;
}

export interface PlexServerItem {
	name: string;
	product: string;
	client_identifier: string;
}

export interface AuthCheckResult {
	plex: PlexAuthState;
	lastfm: LastFMAuthState;
	listenbrainz: ListenBrainzAuthState;
}

/**
 * Overall wizard step – drives which panel is focused.
 * - plex         – waiting for Plex OAuth
 * - server-select – Plex authed, pick a Plex Media Server
 * - music-services – pick at least one music service
 * - done         – all required services connected
 */
export type SetupStep = 'plex' | 'server-select' | 'music-services' | 'done';
