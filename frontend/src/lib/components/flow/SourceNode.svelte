<script lang="ts">
	import FlowNode from './FlowNode.svelte';
	import ServiceMark from './ServiceMark.svelte';
	import { plexStarOptions } from '$lib/relay';
	import { Select } from '$lib/components/ui/select';
	import { cn } from '$lib/utils';
	import type {
		ListenBrainzSourceConfig,
		PlexSourceConfig,
		SourceId,
		SourceOption
	} from '$lib/types/relay';

	let {
		options,
		selected = $bindable(),
		plexConfig,
		lbzConfig,
		resetService = $bindable(),
		resetOptions
	}: {
		options: SourceOption[];
		selected: SourceId;
		plexConfig: PlexSourceConfig;
		lbzConfig: ListenBrainzSourceConfig;
		resetService: SourceId;
		resetOptions: SourceOption[];
	} = $props();
</script>

{#snippet feedbackToggle(value: 'love' | 'hate', text: string)}
	<button
		type="button"
		class={cn(
			'rounded px-2.5 py-0.5 text-sm transition-colors',
			lbzConfig.feedbackType === value
				? 'bg-primary text-primary-foreground'
				: 'text-muted-foreground hover:text-foreground'
		)}
		onclick={() => (lbzConfig.feedbackType = value)}
		aria-pressed={lbzConfig.feedbackType === value}
	>
		{text}
	</button>
{/snippet}

<FlowNode label="From" tone={selected === 'reset' ? 'danger' : 'source'}>
	<div class="flex flex-wrap items-stretch gap-1.5">
		{#each options as option (option.id)}
			{@const active = selected === option.id}
			<button
				type="button"
				onclick={() => (selected = option.id)}
				class={cn(
					'group flex min-w-32 flex-1 items-center gap-2.5 rounded-md border px-3 py-2.5 text-left transition-colors',
					active
						? option.id === 'reset'
							? 'border-destructive/50 bg-destructive/10'
							: 'border-primary/50 bg-primary/10'
						: 'border-border bg-background/40 hover:border-border hover:bg-muted/60'
				)}
				aria-pressed={active}
			>
				<ServiceMark service={option.id} size={18} />
				<span
					class={cn('text-base font-medium', active ? 'text-foreground' : 'text-muted-foreground')}
				>
					{option.name}
				</span>
			</button>
		{/each}
	</div>

	<div class="mt-3 flex flex-wrap items-center gap-x-2 gap-y-2 border-t border-border/60 pt-3">
		{#if selected === 'plex'}
			<span class="text-sm text-muted-foreground">Rated</span>
			<Select
				bind:value={plexConfig.comparison}
				label="Rating comparison"
				options={[
					{ value: 'gte', label: 'at least' },
					{ value: 'lte', label: 'at most' }
				]}
			/>
			<Select
				bind:value={plexConfig.ratingThreshold}
				label="Rating threshold"
				class="min-w-40"
				options={plexStarOptions}
			/>
		{:else if selected === 'lastfm'}
			<p class="text-sm text-muted-foreground">Every loved track on Last.fm.</p>
		{:else if selected === 'listenbrainz'}
			<span class="text-sm text-muted-foreground">Fetch</span>
			<div
				class="inline-flex items-center gap-0.5 rounded-md border border-border bg-background p-0.5"
			>
				{@render feedbackToggle('love', 'loved')}
				{@render feedbackToggle('hate', 'hated')}
			</div>
			<span class="text-sm text-muted-foreground">recordings</span>
		{:else}
			<span class="text-sm text-muted-foreground">Clear</span>
			<div class="flex flex-wrap items-center gap-1">
				{#each resetOptions as option (option.id)}
					<button
						type="button"
						onclick={() => (resetService = option.id)}
						class={cn(
							'inline-flex items-center gap-2 rounded-md border px-2.5 py-1.5 text-sm transition-colors',
							resetService === option.id
								? 'border-destructive/50 bg-destructive/10 text-foreground'
								: 'border-border text-muted-foreground hover:bg-muted/60'
						)}
						aria-pressed={resetService === option.id}
					>
						<ServiceMark service={option.id} size={14} />
						{option.name}
					</button>
				{/each}
			</div>
			<p class="w-full text-sm leading-snug text-muted-foreground">
				Undoes the writes this tool recorded on that service. Ratings it did not write are left
				alone.
			</p>
		{/if}
	</div>
</FlowNode>
