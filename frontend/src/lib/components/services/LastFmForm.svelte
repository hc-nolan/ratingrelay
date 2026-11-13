<script lang="ts">
	import { Button } from '$lib/components/ui/button';
	import { Input } from '$lib/components/ui/input';

	let {
		onsubmitted,
		submitLabel = 'Connect Last.fm'
	}: {
		onsubmitted: (username: string) => void;
		submitLabel?: string;
	} = $props();

	// The four fields Last.fm needs, in one place: the setup wizard and the
	// services page both post exactly this.
	let form = $state({ api_key: '', api_secret: '', username: '', password: '' });
	let error = $state('');
	let pending = $state(false);

	let complete = $derived(
		!!form.api_key && !!form.api_secret && !!form.username && !!form.password
	);

	async function submit() {
		if (!complete || pending) return;
		pending = true;
		error = '';
		try {
			const res = await fetch('/api/lastfm/auth', {
				method: 'POST',
				headers: { 'Content-Type': 'application/json' },
				body: JSON.stringify(form)
			});
			if (!res.ok) {
				const body = await res.json().catch(() => ({ detail: 'Authentication failed' }));
				error = body.detail ?? 'Authentication failed';
				return;
			}
			const body: { username?: string } = await res.json();
			const connectedAs = body.username ?? form.username;
			form = { api_key: '', api_secret: '', username: '', password: '' };
			onsubmitted(connectedAs);
		} catch {
			error = 'Network error';
		} finally {
			pending = false;
		}
	}
</script>

<p class="text-sm text-muted-foreground">
	Get an API key and secret from
	<a
		class="text-primary hover:underline"
		href="https://www.last.fm/api/account/create"
		target="_blank"
		rel="noopener">last.fm/api/account/create</a
	>, then enter your credentials below.
</p>

<div class="mt-3 grid gap-2 sm:grid-cols-2">
	<Input bind:value={form.api_key} placeholder="API key" autocomplete="off" spellcheck={false} />
	<Input
		bind:value={form.api_secret}
		type="password"
		placeholder="API secret"
		autocomplete="off"
		spellcheck={false}
	/>
	<Input bind:value={form.username} placeholder="Username" autocomplete="off" spellcheck={false} />
	<Input
		bind:value={form.password}
		type="password"
		placeholder="Password"
		autocomplete="current-password"
		onkeydown={(e) => e.key === 'Enter' && submit()}
	/>
</div>

{#if error}
	<p class="mt-2 text-sm text-destructive" role="alert">{error}</p>
{/if}

<Button class="mt-3 h-8 px-3 text-sm" onclick={submit} disabled={!complete || pending}>
	{pending ? 'Verifying…' : submitLabel}
</Button>
