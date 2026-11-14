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
     reader out of the box. Only the chrome is ours.

     The control is square, and the caret sits in a hairline gutter of its own
     rather than floating over the text — a dropdown should look like a slot
     with a lever on it, not like a rounded search box. layout.css opts every
     select into `appearance: base-select` so the option list takes our popover
     instead of the browser's own cool grey, and hides the browser picker icon
     so this caret is the only indicator. -->
<div
	class={cn(
		'inline-flex items-stretch border border-border bg-background transition-colors',
		'focus-within:border-primary/60 hover:border-border/80',
		className
	)}
	data-slot="native-select"
>
	<select
		bind:this={ref}
		bind:value
		class="h-8 min-w-0 appearance-none border-0 bg-transparent pr-2 pl-2.5 text-sm text-foreground outline-none disabled:cursor-not-allowed disabled:opacity-50"
		{...restProps}
	>
		{@render children?.()}
	</select>
	<span
		class="grid w-7 shrink-0 place-items-center border-l border-border/70 text-muted-foreground"
		aria-hidden="true"
	>
		<svg viewBox="0 0 8 5" width="8" height="5" fill="currentColor">
			<path d="M0 0h8L4 5z" />
		</svg>
	</span>
</div>
