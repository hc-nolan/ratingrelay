/**
 * A connected account of a service.
 *
 * `id` is the account's **ref** — the same string the API's job configs and
 * `relays.toml` use. The first account of a service is the bare kind
 * (`lastfm`); a second one is `lastfm:old`. That is what lets a relay say which
 * account it means without a new concept anywhere else.
 */
export type ServiceKind = 'plex' | 'lastfm' | 'listenbrainz';

export interface ServiceAccountItem {
	id: string;
	service: ServiceKind;
	label: string;
	/** The account the bare kind names, and the one settings may override. */
	primary: boolean;
	/** The stored values are complete. Not a live check: see each service's status. */
	configured: boolean;
	username?: string | null;
	server_name?: string | null;
}

export interface ServicesResponse {
	accounts: ServiceAccountItem[];
}

/** The kind behind a ref: `lastfm:old` is a Last.fm account. */
export function kindOf(ref: string): ServiceKind {
	return ref.split(':')[0] as ServiceKind;
}

/** The account name behind a ref, or undefined for the first account. */
export function accountOf(ref: string): string | undefined {
	const [, account] = ref.split(':');
	return account || undefined;
}

/** The `service`/`account` pair a relay config takes for this ref. */
export function accountPayload(ref: string): { service: ServiceKind; account?: string } {
	const account = accountOf(ref);
	return account ? { service: kindOf(ref), account } : { service: kindOf(ref) };
}

/** The label to show on a node: the first account stays the bare service name. */
export function displayName(ref: string, accounts: ServiceAccountItem[]): string {
	const match = accounts.find((a) => a.id === ref);
	if (match) return match.label;
	return ref;
}
