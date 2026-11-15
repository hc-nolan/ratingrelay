<script lang="ts">
	import { cn, type WithElementRef } from '$lib/utils.js';
	import type { HTMLInputAttributes } from 'svelte/elements';

	let {
		class: className,
		ref = $bindable(null),
		value = $bindable(),
		type = 'text',
		...restProps
	}: WithElementRef<HTMLInputAttributes> = $props();
</script>

<input
	bind:this={ref}
	bind:value
	data-slot="input"
	{type}
	class={cn(
		'flex h-9 w-full min-w-0 border border-border bg-background px-2.5 py-1.5 text-sm text-foreground transition-colors',
		'placeholder:text-muted-foreground/70',
		'hover:border-border/80',
		'focus-visible:border-primary/60 focus-visible:outline-none',
		'disabled:cursor-not-allowed disabled:opacity-50',
		'aria-invalid:border-destructive',
		className
	)}
	{...restProps}
/>
