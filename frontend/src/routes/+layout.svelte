<script lang="ts">
	import './layout.css';
	import { invalidateAll } from '$app/navigation';
	import LoginGate from '$lib/components/LoginGate.svelte';
	import { getSession, type SessionStatus } from '$lib/session';

	let { children } = $props();

	// `null` means "not asked yet": identical on the server and on the first
	// client render, so nothing flashes before the answer arrives.
	let session = $state<SessionStatus | null>(null);

	async function refresh() {
		session = await getSession();
	}

	$effect(() => {
		refresh();
		// The cookie can expire or be revoked while a tab sits open, so ask
		// again when the tab comes back — cheaper than guarding every fetch.
		const check = () => {
			if (document.visibilityState === 'visible') refresh();
		};
		document.addEventListener('visibilitychange', check);
		window.addEventListener('focus', check);
		return () => {
			document.removeEventListener('visibilitychange', check);
			window.removeEventListener('focus', check);
		};
	});

	const gated = $derived(session !== null && session.required && !session.authenticated);

	async function handleSignedIn(username: string, ok: boolean) {
		// Trust the poll's answer first: the page must not look unchanged while a
		// second round trip goes out to confirm it.
		session = { required: true, authenticated: ok, username: ok ? username : session?.username };
		if (ok) await invalidateAll();
	}
</script>

<svelte:head>
	<title>RatingRelay</title>
</svelte:head>

{#if gated}
	<LoginGate onauthed={handleSignedIn} />
{:else}
	{@render children()}
{/if}
