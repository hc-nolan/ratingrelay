<script lang="ts">
	import { starLabel } from '$lib/relay';
	import type { Direction, TargetOption } from '$lib/types/relay';
	import { markFor } from '$lib/types/relay';
	import { cn } from '$lib/utils';
	import ServiceMark from './ServiceMark.svelte';

	let {
		option,
		allocated,
		onadd,
		onremove,
		plexWrite,
		direction
	}: {
		option: TargetOption;
		/** Allocated cards are the same card, switched on: filled, accent-edged, configured. */
		allocated: boolean;
		onadd: () => void;
		onremove: () => void;
		/** What a Plex destination writes: the threshold for the relay's direction (null: unknown). */
		plexWrite?: { rating: number | null };
		/** The relay's direction, which every target writes in its own terms. */
		direction?: Direction;
	} = $props();
</script>

<!-- One card per target, in a grid that does not reflow when you pick one:
     choosing a target switches this card on rather than replacing it with a
     different node somewhere else on the page. -->
<div
	class={cn(
		'group relative flex flex-col gap-2 rounded-lg border px-4 py-3 transition-colors',
		// On and off have to read at a glance: a picked target is filled and
		// solid-edged, an unpicked one is hollow, dashed and quieter.
		allocated
			? 'border-primary bg-primary/15'
			: 'border-dashed border-border/60 bg-transparent hover:border-primary/60 hover:bg-primary/5 active:scale-[0.99] motion-safe:transition-transform'
	)}
>
	<div class="flex items-center gap-3">
		<span class={allocated ? 'opacity-100' : 'opacity-60'}>
			<ServiceMark service={markFor(option.id)} size={22} />
		</span>
		<span
			class={cn(
				'min-w-0 flex-1 text-base font-medium',
				allocated ? 'text-foreground' : 'text-muted-foreground'
			)}>{option.name}</span
		>

		{#if allocated}
			<button
				type="button"
				onclick={onremove}
				title="Remove this target"
				aria-label="Remove {option.name}"
				class="grid size-7 shrink-0 place-items-center rounded-md text-muted-foreground transition-colors hover:bg-muted hover:text-foreground focus-visible:ring-2 focus-visible:ring-ring focus-visible:outline-none"
			>
				<svg viewBox="0 0 16 16" width="10" height="10" aria-hidden="true">
					<path
						d="M4 4l8 8M12 4l-8 8"
						stroke="currentColor"
						stroke-width="1.6"
						stroke-linecap="round"
					/>
				</svg>
			</button>
		{:else}
			<!-- The whole card is the control; this is only the affordance. -->
			<span
				class="grid size-7 shrink-0 place-items-center rounded-md border border-border/60 text-muted-foreground/70 transition-colors group-hover:border-primary/60 group-hover:bg-primary/10 group-hover:text-primary group-active:bg-primary group-active:text-primary-foreground"
				aria-hidden="true"
			>
				<svg viewBox="0 0 16 16" width="11" height="11">
					<path
						d="M8 3v10M3 8h10"
						stroke="currentColor"
						stroke-width="1.7"
						stroke-linecap="round"
					/>
				</svg>
			</span>
		{/if}
	</div>

	<div class="flex min-h-6 items-center pl-[34px]">
		{#if allocated}
			{#if direction === 'rating' && (option.id.startsWith('plex') || option.id.startsWith('hyperfix'))}
				<!-- Every rated track, in the number it already has. -->
				<span class="text-sm text-muted-foreground">Each track's own rating</span>
			{:else if option.id === 'plex' && plexWrite}
				<span class="text-sm text-muted-foreground">
					{plexWrite.rating === null
						? 'Rate at the threshold'
						: `Rate ${starLabel(plexWrite.rating)}`}
				</span>
			{:else if option.id === 'lastfm'}
				<span class="text-sm text-muted-foreground">Love every track</span>
			{:else if option.id === 'listenbrainz' && direction}
				<!-- No toggle: a target writes what the source reads. -->
				<span class="text-sm text-muted-foreground">
					{direction === 'love' ? 'Love' : 'Hate'} every track
				</span>
			{/if}
		{:else}
			<span class="truncate text-sm text-muted-foreground/70">{option.hint}</span>
		{/if}
	</div>

	{#if !allocated}
		<!-- A transparent button over the card keeps one interactive element per
		     card while the visible markup stays a plain layout. -->
		<button
			type="button"
			onclick={onadd}
			aria-label="Add {option.name} as a destination"
			class="absolute inset-0 rounded-lg focus-visible:ring-2 focus-visible:ring-ring focus-visible:outline-none"
		></button>
	{/if}
</div>
