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
		'flex h-9 w-full min-w-0 rounded-md border border-input bg-background px-2.5 py-1.5 text-base text-foreground shadow-none transition-colors',
		'placeholder:text-muted-foreground/70',
		'focus-visible:border-primary/60 focus-visible:outline-none',
		'disabled:cursor-not-allowed disabled:opacity-50',
		'aria-invalid:border-destructive',
		'md:text-sm',
		className
	)}
	{...restProps}
/>
