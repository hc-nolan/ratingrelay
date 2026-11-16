<script lang="ts">
	import FlowNode from './FlowNode.svelte';
	import ServiceMark from './ServiceMark.svelte';
	import StarRating from '$lib/components/StarRating.svelte';
	import { saveThreshold, thresholdSetting } from '$lib/relay';
	import { cn } from '$lib/utils';
	import type { Direction, PlexThresholds } from '$lib/types/relay';

	let {
		config,
		thresholds = $bindable(),
		plexInvolved
	}: {
		/** The source's direction, or null for a source that only has loves (Last.fm). */
		config: { feedbackType: Direction } | null;
		/** Shared by every relay; saving one changes it for all of them. */
		thresholds: PlexThresholds | null;
		/** Plex is the source or a destination, so its threshold matters to this relay. */
		plexInvolved: boolean;
	} = $props();

	let direction = $derived<Direction>(config?.feedbackType ?? 'love');
	let saving = $state(false);
	let error = $state('');

	async function save(rating: number) {
		if (!thresholds || saving) return;
		const key = `${direction}_threshold` as const;
		if (thresholds[key] === rating) return;
		const previous = thresholds;
		// Show the pick at once; the server's answer replaces it, or puts it back.
		thresholds = { ...thresholds, [key]: rating };
		saving = true;
		error = '';
		try {
			thresholds = await saveThreshold(direction, rating);
		} catch (e) {
			thresholds = previous;
			error = e instanceof Error ? e.message : 'Could not save the threshold';
		} finally {
			saving = false;
		}
	}
</script>

{#snippet directionToggle(target: { feedbackType: Direction }, value: Direction, text: string)}
	<button
		type="button"
		class={cn(
			'rounded px-2.5 py-0.5 text-sm transition-colors',
			target.feedbackType === value
				? 'bg-primary text-primary-foreground'
				: 'text-muted-foreground hover:text-foreground'
		)}
		onclick={() => (target.feedbackType = value)}
		aria-pressed={target.feedbackType === value}
	>
		{text}
	</button>
{/snippet}

<!-- Both rows are always laid out (the threshold row just goes invisible) so the box
     keeps one height whichever source and destinations are picked. -->
<FlowNode label="Rating">
	<div class="flex min-h-8 flex-wrap items-center gap-x-2 gap-y-2">
		<span class="text-sm text-muted-foreground">Relay</span>
		{#if config}
			<div
				class="inline-flex items-center gap-0.5 rounded-md border border-border bg-background p-0.5"
			>
				{@render directionToggle(config, 'love', 'loves')}
				{@render directionToggle(config, 'hate', 'hates')}
			</div>
		{:else}
			<span class="text-sm">loves</span>
			<span class="text-sm text-muted-foreground">— Last.fm has no hates.</span>
		{/if}
	</div>

	<div
		class={cn(
			'mt-3 flex min-h-10 flex-wrap items-center gap-x-2 gap-y-2 border-t border-border/60 pt-3',
			!(plexInvolved && thresholds) && 'invisible'
		)}
		aria-hidden={!(plexInvolved && thresholds)}
	>
		{#if plexInvolved && thresholds}
			{@const current = thresholds}
			{@const locked = direction === 'love' ? current.love_locked : current.hate_locked}
			<span title="The Plex rating that counts as a {direction}, shared by every relay">
				<ServiceMark service="plex" size={16} />
			</span>
			<StarRating
				value={current[`${direction}_threshold`]}
				label="Plex {direction} threshold"
				readonly={locked}
				disabled={saving}
				allowed={(r) =>
					direction === 'love' ? r > current.hate_threshold : r < current.love_threshold}
				onpick={save}
			/>
			{#if locked}
				<span class="text-xs text-muted-foreground">
					pinned by <code class="font-mono">{thresholdSetting[direction]}</code>
				</span>
			{/if}
		{/if}
	</div>
	{#if error && plexInvolved}
		<p class="mt-2 text-sm text-destructive" role="alert">{error}</p>
	{/if}
</FlowNode>
