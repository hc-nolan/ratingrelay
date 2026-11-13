<script lang="ts">
	import { getSession, lostCookieMessage, startPlexLogin, waitForLogin } from '$lib/session';
	import ServiceMark from '$lib/components/flow/ServiceMark.svelte';
	import { Button } from '$lib/components/ui/button';
	import { Card, CardContent, CardHeader, CardTitle } from '$lib/components/ui/card';

	let { onauthed }: { onauthed: (username: string, ok: boolean) => void } = $props();

	let busy = $state(false);
	let error = $state('');
	let notice = $state('');

	async function signIn() {
		busy = true;
		error = '';
		notice = '';
		let popup: Window | null = null;
		try {
			const { pin_id, auth_url } = await startPlexLogin();
			popup = window.open(auth_url, 'plex-login', 'width=600,height=730');
			const username = await waitForLogin(pin_id);
			// The poll answered "signed in", so believe it — and then check. A
			// browser that did not keep the cookie looks exactly like a failed
			// login from here, and saying so beats a card that never changes.
			const status = await getSession();
			if (!status.authenticated) notice = lostCookieMessage(status);
			onauthed(username, status.authenticated);
		} catch (err) {
			error = err instanceof Error ? err.message : 'Login failed';
		} finally {
			popup?.close();
			busy = false;
		}
	}
</script>

<div class="flex min-h-dvh items-center justify-center bg-background px-6">
	<Card class="w-full max-w-md">
		<CardHeader>
			<div class="mb-3 flex items-center gap-3">
				<ServiceMark service="plex" size={28} />
				<CardTitle class="text-xl">RatingRelay</CardTitle>
			</div>
		</CardHeader>
		<CardContent class="flex flex-col gap-4 text-base">
			<p class="text-muted-foreground">
				This relay is private. Sign in with the Plex account it syncs as — there is nothing else to
				remember.
			</p>
			<Button size="lg" onclick={signIn} disabled={busy}>
				{busy ? 'Waiting for Plex…' : 'Sign in with Plex'}
			</Button>
			{#if error}
				<p class="text-sm text-destructive" role="alert">{error}</p>
			{/if}
			{#if notice}
				<p class="text-sm text-amber-400" role="status">{notice}</p>
			{/if}
		</CardContent>
	</Card>
</div>
