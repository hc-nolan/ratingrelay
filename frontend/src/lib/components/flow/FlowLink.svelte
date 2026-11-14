<script lang="ts">
	/**
	 * The rail between two nodes. The `+` is the only way to allocate a target:
	 * the builder idiom is that empty space is a slot, not a hidden checkbox.
	 */
	let {
		onadd,
		label = 'Add a target',
		active = false
	}: { onadd?: () => void; label?: string; active?: boolean } = $props();
</script>

<div class="flex flex-col items-center" aria-hidden={onadd ? undefined : 'true'}>
	<span class="h-6 w-px bg-muted-foreground/30"></span>
	{#if onadd}
		<button
			type="button"
			onclick={onadd}
			aria-label={label}
			title={label}
			class="relative z-10 grid size-8 place-items-center border bg-background text-muted-foreground transition-colors hover:border-primary hover:text-primary focus-visible:ring-2 focus-visible:ring-ring focus-visible:outline-none"
			class:border-primary={active}
			class:text-primary={active}
		>
			<svg viewBox="0 0 16 16" width="15" height="15" aria-hidden="true">
				<path d="M8 3v10M3 8h10" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" />
			</svg>
		</button>
	{:else}
		<span class="grid size-8 place-items-center" aria-hidden="true">
			<span class="size-2 rounded-full bg-muted-foreground/40"></span>
		</span>
	{/if}
	<span class="h-6 w-px bg-muted-foreground/30"></span>
</div>
