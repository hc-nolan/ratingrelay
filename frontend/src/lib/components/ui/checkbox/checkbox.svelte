<script lang="ts">
	import { cn, type WithElementRef } from '$lib/utils.js';
	import type { HTMLInputAttributes } from 'svelte/elements';

	let {
		class: className,
		ref = $bindable(null),
		checked = $bindable(false),
		disabled,
		...restProps
	}: WithElementRef<HTMLInputAttributes> = $props();
</script>

<!-- A real checkbox with the browser control hidden: the input keeps focus and
     keyboard behaviour, the span draws it. Nothing here needs JS to toggle. -->
<label class={cn('group/checkbox relative inline-flex items-center', className)}>
	<input
		bind:this={ref}
		bind:checked
		{disabled}
		type="checkbox"
		class="peer absolute size-4 appearance-none opacity-0"
		{...restProps}
	/>
	<span
		aria-hidden="true"
		class="grid size-4 shrink-0 place-items-center rounded-sm border border-input bg-background transition-colors peer-checked:border-primary peer-checked:bg-primary peer-focus-visible:ring-2 peer-focus-visible:ring-ring/50 peer-disabled:opacity-50"
	>
		<svg viewBox="0 0 12 12" width="9" height="9" class="hidden group-has-checked/checkbox:block">
			<path
				d="M2 6.4L4.6 9 10 3.2"
				fill="none"
				stroke="currentColor"
				stroke-width="1.9"
				stroke-linecap="round"
				stroke-linejoin="round"
				class="text-primary-foreground"
			/>
		</svg>
	</span>
</label>
