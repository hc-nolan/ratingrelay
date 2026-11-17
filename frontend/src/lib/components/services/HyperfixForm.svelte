<script lang="ts">
	import { Button } from '$lib/components/ui/button';
	import { Input } from '$lib/components/ui/input';

	let {
		onsubmitted,
		submitLabel = 'Connect',
		account = undefined,
		label = undefined
	}: {
		onsubmitted: (url: string) => void;
		submitLabel?: string;
		/** Adding a second account: its slug, and the name to show for it. */
		account?: string;
		label?: string;
	} = $props();

	let form = $state({ url: '', token: '' });
	let error = $state('');
	let pending = $state(false);

	let complete = $derived(/^https?:\/\/.+/.test(form.url.trim()) && !!form.token.trim());

	async function submit() {
		if (!complete || pending || (account !== undefined && !account)) return;
		pending = true;
		error = '';
		try {
			const res = await fetch('/api/hyperfix/auth', {
				method: 'POST',
				headers: { 'Content-Type': 'application/json' },
				body: JSON.stringify({
					url: form.url.trim(),
					token: form.token.trim(),
					...(account ? { account, label } : {})
				})
			});
			if (!res.ok) {
				const body = await res.json().catch(() => ({ detail: 'Could not connect' }));
				error = body.detail ?? 'Could not connect';
				return;
			}
			const body: { url?: string } = await res.json();
			const connectedUrl = body.url ?? form.url.trim();
			form = { url: '', token: '' };
			onsubmitted(connectedUrl);
		} catch {
			error = 'Network error';
		} finally {
			pending = false;
		}
	}
</script>

<p class="text-sm text-muted-foreground">
	The address of your hyperfix instance, and an API key from its <code>/settings</code> page. The key
	is checked against the ratings feed it will read — a key has no scopes, so keep it to this tool and
	revoke it if you remove the account.
</p>

<div class="mt-3 grid gap-2 sm:grid-cols-2">
	<Input
		bind:value={form.url}
		placeholder="https://hyperfix.example"
		autocomplete="off"
		spellcheck={false}
	/>
	<Input
		bind:value={form.token}
		type="password"
		placeholder="API key (hfx_…)"
		autocomplete="off"
		spellcheck={false}
		onkeydown={(e) => e.key === 'Enter' && submit()}
	/>
</div>

{#if error}
	<p class="mt-2 text-sm text-destructive" role="alert">{error}</p>
{/if}

<Button class="mt-3 h-8 px-3 text-sm" onclick={submit} disabled={!complete || pending}>
	{pending ? 'Checking…' : submitLabel}
</Button>
