<script lang="ts">
	import { Button } from '$lib/components/ui/button';
	import type { ServiceAccountItem } from '$lib/types/services';

	/**
	 * One connected account in a service panel.
	 *
	 * The label is what the user named it; the ref it is stored under is shown
	 * only when it is not the first account, because that is the string a
	 * `relays.toml` has to name to reach it.
	 *
	 * An account the environment describes has no row to delete, so it offers no
	 * Disconnect — the button would forget nothing and the service would still be
	 * connected on the next page load.
	 */
	let {
		account,
		busy = false,
		ondisconnect
	}: {
		account: ServiceAccountItem;
		busy?: boolean;
		ondisconnect: () => void;
	} = $props();

	let detail = $derived(account.server_name ?? account.username ?? account.url ?? '');
</script>

<div class="flex items-center gap-2 rounded-md border border-border px-2.5 py-1.5">
	<span class="truncate text-sm">{account.label}</span>
	{#if account.primary}
		<span
			class="rounded bg-muted px-1.5 py-0.5 text-[10px] font-medium tracking-wide text-muted-foreground uppercase"
			>first</span
		>
	{:else}
		<code class="rounded bg-muted px-1.5 py-0.5 text-[11px] text-muted-foreground"
			>{account.id}</code
		>
	{/if}
	<span class="ml-auto truncate text-xs text-muted-foreground">{detail}</span>
	{#if account.managed === 'settings'}
		<span
			class="shrink-0 text-xs text-muted-foreground"
			title="Set in the environment (PLEX_TOKEN, LISTENBRAINZ_TOKEN, …), which the web app cannot change."
		>
			from the environment
		</span>
	{:else}
		<Button
			variant="ghost"
			class="h-7 px-2 text-xs text-destructive"
			onclick={ondisconnect}
			disabled={busy}
		>
			Disconnect
		</Button>
	{/if}
</div>
