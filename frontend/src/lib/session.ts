/**
 * Talking to the login surface.
 *
 * The relay has one identity — the Plex account it syncs as — so "signed in"
 * and "signed in as the owner" are the same question, answered by the server.
 * Nothing here decides anything: it asks, the cookie is httpOnly, and the
 * server refuses what it should.
 */

export interface SessionStatus {
	required: boolean;
	authenticated: boolean;
	username?: string | null;
	/** Did this request carry a session cookie at all? */
	cookie?: boolean;
}

export interface LoginInit {
	pin_id: number;
	auth_url: string;
}

export async function getSession(): Promise<SessionStatus> {
	const res = await fetch('/api/auth/status');
	if (!res.ok) return { required: true, authenticated: false };
	return (await res.json()) as SessionStatus;
}

export async function startPlexLogin(): Promise<LoginInit> {
	const res = await fetch('/api/auth/plex/init', { method: 'POST' });
	if (!res.ok) {
		const detail = await res.json().catch(() => ({}));
		throw new Error(detail.detail ?? 'Plex did not answer');
	}
	return (await res.json()) as LoginInit;
}

/** Poll until the popup is confirmed, the PIN expires, or the deadline passes. */
export async function waitForLogin(pinId: number, deadlineMs = 120_000): Promise<string> {
	const deadline = Date.now() + deadlineMs;
	while (Date.now() < deadline) {
		const res = await fetch(`/api/auth/plex/poll?pin_id=${pinId}`);
		if (res.status === 403) throw new Error('This relay is not yours');
		if (res.status === 404) throw new Error('Login expired — try again');
		if (res.ok) {
			const body = await res.json();
			if (body.status === 'ok') return body.username ?? '';
		}
		await new Promise((resolve) => setTimeout(resolve, 1500));
	}
	throw new Error('Timed out waiting for Plex');
}

/** A logged-in browser still gets refused if it never stored the cookie. */
export function lostCookieMessage(status: SessionStatus): string {
	return status.cookie
		? 'Plex signed you in, but the browser sent back a session this relay cannot verify. Sign in again — if it keeps happening, the Plex token has changed.'
		: 'Plex signed you in, but the browser did not keep the session cookie. Allow cookies for this site and try again.';
}

export async function logout(): Promise<void> {
	await fetch('/api/auth/logout', { method: 'POST' });
}
