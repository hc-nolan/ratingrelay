<script lang="ts">
	import { Star } from '@lucide/svelte';
	import { starLabel } from '$lib/relay';
	import { cn } from '$lib/utils';

	let {
		value,
		onpick,
		allowed = () => true,
		readonly = false,
		disabled = false,
		label
	}: {
		/** Plex scale, 1–10: each star is 2, so odd values are half stars. */
		value: number;
		onpick?: (rating: number) => void;
		/** Whether a rating may be picked; a refused one can't be clicked. */
		allowed?: (rating: number) => boolean;
		readonly?: boolean;
		disabled?: boolean;
		/** Accessible name for the group. */
		label: string;
	} = $props();

	/** The rating under the pointer, previewed before the click. */
	let hover = $state<number | null>(null);
	let shown = $derived(hover ?? value);

	/** How much of star *i* (0-based) is lit: 0, 50 or 100 percent. */
	function fill(i: number): number {
		return Math.max(0, Math.min(2, shown - i * 2)) * 50;
	}
</script>

<!-- Five stars like Plex's: each one is two buttons, so the left half picks a
     half star. Hovering previews the rating; a click saves it. -->
<div
	role="group"
	aria-label="{label}: {starLabel(value)}"
	class={cn('inline-flex items-center', disabled && 'opacity-60')}
	onpointerleave={() => (hover = null)}
>
	{#each [0, 1, 2, 3, 4] as i (i)}
		<span class="relative inline-flex size-6 items-center justify-center">
			<Star class="size-5 text-muted-foreground/40" strokeWidth={1.5} aria-hidden="true" />
			<span
				class="absolute inset-y-0 left-0.5 overflow-hidden"
				style:width="{(fill(i) / 100) * 20}px"
				aria-hidden="true"
			>
				<Star
					class={cn('size-5', hover === null ? 'text-primary' : 'text-primary/70')}
					fill="currentColor"
					strokeWidth={1.5}
				/>
			</span>
			{#if !readonly}
				{#each [i * 2 + 1, i * 2 + 2] as rating, half (rating)}
					{@const ok = allowed(rating)}
					<button
						type="button"
						class={cn(
							'absolute inset-y-0 w-1/2 focus-visible:rounded-sm focus-visible:ring-2 focus-visible:ring-ring focus-visible:outline-none',
							half === 0 ? 'left-0' : 'right-0',
							ok ? 'cursor-pointer' : 'cursor-not-allowed'
						)}
						disabled={disabled || !ok}
						aria-label={starLabel(rating)}
						aria-pressed={value === rating}
						onpointerenter={() => (hover = ok ? rating : null)}
						onclick={() => onpick?.(rating)}
					></button>
				{/each}
			{/if}
		</span>
	{/each}
</div>
