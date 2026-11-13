<script lang="ts">
	import { startPlexLogin, waitForLogin } from '$lib/session';
	import ServiceMark from '$lib/components/flow/ServiceMark.svelte';
	import { Button } from '$lib/components/ui/button';
	import { Card, CardContent, CardHeader, CardTitle } from '$lib/components/ui/card';

	let { onauthed }: { onauthed: (username: string) => void } = $props();

	let busy = $state(false);
	let error = $state('');

	async function signIn() {
		busy = true;
		error = '';
		let popup: Window | null = null;
		try {
			const { pin_id, auth_url } = await startPlexLogin();
			popup = window.open(auth_url, 'plex-login', 'width=600,height=730');
			const username = await waitForLogin(pin_id);
			onauthed(username);
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
		</CardContent>
	</Card>
</div>
