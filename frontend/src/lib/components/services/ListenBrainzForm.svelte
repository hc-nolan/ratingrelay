<script lang="ts">
	import { Button } from '$lib/components/ui/button';
	import { Input } from '$lib/components/ui/input';

	let {
		onsubmitted,
		submitLabel = 'Connect'
	}: {
		onsubmitted: (username: string) => void;
		submitLabel?: string;
	} = $props();

	let form = $state({ username: '', token: '' });
	let error = $state('');
	let pending = $state(false);

	let complete = $derived(!!form.username.trim() && !!form.token.trim());

	async function submit() {
		if (!complete || pending) return;
		pending = true;
		error = '';
		try {
			const res = await fetch('/api/listenbrainz/auth', {
				method: 'POST',
				headers: { 'Content-Type': 'application/json' },
				body: JSON.stringify({ username: form.username.trim(), token: form.token.trim() })
			});
			if (!res.ok) {
				const body = await res.json().catch(() => ({ detail: 'Authentication failed' }));
				error = body.detail ?? 'Authentication failed';
				return;
			}
			const body: { username?: string } = await res.json();
			const connectedAs = body.username ?? form.username;
			form = { username: '', token: '' };
			onsubmitted(connectedAs);
		} catch {
			error = 'Network error';
		} finally {
			pending = false;
		}
	}
</script>

<p class="text-sm text-muted-foreground">
	Find your user token at
	<a
		class="text-primary hover:underline"
		href="https://listenbrainz.org/profile/"
		target="_blank"
		rel="noopener">listenbrainz.org/profile</a
	>
	and paste it below.
</p>

<div class="mt-3 grid gap-2 sm:grid-cols-2">
	<Input bind:value={form.username} placeholder="Username" autocomplete="off" spellcheck={false} />
	<Input
		bind:value={form.token}
		type="password"
		placeholder="User token"
		autocomplete="off"
		spellcheck={false}
		onkeydown={(e) => e.key === 'Enter' && submit()}
	/>
</div>

{#if error}
	<p class="mt-2 text-sm text-destructive" role="alert">{error}</p>
{/if}

<Button class="mt-3 h-8 px-3 text-sm" onclick={submit} disabled={!complete || pending}>
	{pending ? 'Verifying…' : submitLabel}
</Button>
