<script lang="ts">
	import { cn, type WithElementRef } from '$lib/utils.js';
	import type { HTMLSelectAttributes } from 'svelte/elements';

	let {
		class: className,
		ref = $bindable(null),
		value = $bindable(),
		children,
		...restProps
	}: WithElementRef<HTMLSelectAttributes> = $props();
</script>

<!-- Native select on purpose: two and five options do not need a popover, and
       the platform control is the one that works with a keyboard and a screen
       reader out of the box. Only the chrome is ours: layout.css opts every
       select into `appearance: base-select`, which lets the option list take
       our popover instead of the browser's own cool grey, and hides the picker
       icon so the chevron drawn below is the only one. -->
<div class="relative inline-flex items-center">
	<select
		bind:this={ref}
		bind:value
		data-slot="native-select"
		class={cn(
			'h-8 min-w-0 rounded-md border border-border bg-background pr-7 pl-2.5 text-sm text-foreground transition-colors',
			'hover:border-border focus-visible:border-primary/60 focus-visible:outline-none',
			'disabled:cursor-not-allowed disabled:opacity-50',
			className
		)}
		{...restProps}
	>
		{@render children?.()}
	</select>
	<svg
		viewBox="0 0 12 12"
		width="10"
		height="10"
		class="pointer-events-none absolute right-2 text-muted-foreground"
		aria-hidden="true"
	>
		<path
			d="M3 4.5L6 8l3-3.5"
			fill="none"
			stroke="currentColor"
			stroke-width="1.6"
			stroke-linecap="round"
			stroke-linejoin="round"
		/>
	</svg>
</div>
