<script lang="ts">
	import { cn, type WithElementRef } from '$lib/utils.js';
	import type { HTMLLabelAttributes } from 'svelte/elements';

	let {
		class: className,
		ref = $bindable(null),
		children,
		...restProps
	}: WithElementRef<HTMLLabelAttributes> = $props();
</script>

<label
	bind:this={ref}
	data-slot="label"
	class={cn(
		'flex items-center gap-2 text-sm leading-none font-medium text-foreground select-none',
		'group-data-[disabled=true]:pointer-events-none group-data-[disabled=true]:opacity-50',
		className
	)}
	{...restProps}
>
	{@render children?.()}
</label>
