<script lang="ts">
	import { TONE_CHIP, statusLabel, statusTone } from '$lib/format';

	let { status, username = null }: { status: string; username?: string | null } = $props();
</script>

<!-- One status pill for every service panel: same shape everywhere, and the
     tone comes from the connection state rather than being chosen per service. -->
<div
	class="inline-flex shrink-0 items-center gap-1.5 border px-2 py-0.5 text-xs {TONE_CHIP[
		statusTone(status)
	]}"
>
	{#if status === 'checking'}
		<span class="size-1.5 animate-pulse rounded-full bg-current" aria-hidden="true"></span>
	{:else if status === 'connected'}
		<svg viewBox="0 0 16 16" fill="currentColor" width="10" height="10" aria-hidden="true"
			><path d="M13.5 3.5L6 11 2.5 7.5l-1 1L6 13l8.5-8.5z" /></svg
		>
	{/if}
	{statusLabel(status)}
	{#if username}
		<span class="opacity-70">· {username}</span>
	{/if}
</div>
