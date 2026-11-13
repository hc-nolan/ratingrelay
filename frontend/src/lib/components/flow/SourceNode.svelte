<script lang="ts">
	import FlowNode from './FlowNode.svelte';
	import ServiceMark from './ServiceMark.svelte';
	import { plexStarOptions } from '$lib/relay';
	import type {
		ListenBrainzSourceConfig,
		PlexSourceConfig,
		SourceId,
		SourceOption
	} from '$lib/types/relay';
	import { cn } from '$lib/utils';

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

{#snippet toggle(value: 'love' | 'hate', text: string)}
	<button
		type="button"
		class={cn(
			'rounded px-3 py-1 text-base transition-colors',
			lbzConfig.feedbackType === value
				? 'bg-primary text-primary-foreground'
				: 'text-muted-foreground hover:text-foreground'
		)}
		onclick={() => (lbzConfig.feedbackType = value)}
	>
		{text}
	</button>
{/snippet}

<FlowNode label="From" tone={selected === 'reset' ? 'danger' : 'source'}>
	<div class="flex flex-wrap gap-1.5">
		{#each options as option (option.id)}
			<button
				type="button"
				onclick={() => (selected = option.id)}
				class={cn(
					'inline-flex items-center gap-2 rounded-md border px-3 py-1.5 text-base transition-colors',
					selected === option.id
						? 'border-primary/50 bg-primary/10 text-foreground'
						: 'border-transparent text-muted-foreground hover:bg-muted/60 hover:text-foreground'
				)}
				aria-pressed={selected === option.id}
			>
				<ServiceMark service={option.id} size={16} />
				{option.name}
			</button>
		{/each}
	</div>

	<div class="mt-3.5 rounded-md bg-muted/40 px-3 py-2.5">
		{#if selected === 'plex'}
			<div class="flex flex-wrap items-center gap-2 text-base text-muted-foreground">
				<span>Rated</span>
				<select
					class="rounded-md border border-border bg-background px-2 py-1.5 text-base text-foreground"
					bind:value={plexConfig.comparison}
				>
					<option value="gte">at least</option>
					<option value="lte">at most</option>
				</select>
				<select
					class="rounded-md border border-border bg-background px-2 py-1.5 text-base text-foreground"
					bind:value={plexConfig.ratingThreshold}
				>
					{#each plexStarOptions as opt (opt.value)}
						<option value={opt.value}>{opt.label}</option>
					{/each}
				</select>
			</div>
		{:else if selected === 'lastfm'}
			<p class="text-base text-muted-foreground">Every loved track on Last.fm.</p>
		{:else if selected === 'listenbrainz'}
			<div class="flex items-center gap-2 text-base text-muted-foreground">
				<span>Fetch</span>
				<div
					class="inline-flex items-center gap-0.5 rounded-md border border-border bg-background p-1"
				>
					{@render toggle('love', 'loved')}
					{@render toggle('hate', 'hated')}
				</div>
				<span>recordings</span>
			</div>
		{:else}
			<div class="space-y-2">
				<div class="flex flex-wrap items-center gap-1.5">
					<span class="text-base text-muted-foreground">Clear</span>
					{#each resetOptions as option (option.id)}
						<button
							type="button"
							onclick={() => (resetService = option.id)}
							class={cn(
								'inline-flex items-center gap-2 rounded-md border px-3 py-1.5 text-base transition-colors',
								resetService === option.id
									? 'border-destructive/50 bg-destructive/10 text-foreground'
									: 'border-transparent text-muted-foreground hover:bg-background/60'
							)}
							aria-pressed={resetService === option.id}
						>
							<ServiceMark service={option.id} size={16} />
							{option.name}
						</button>
					{/each}
				</div>
				<p class="flex items-start gap-2 text-sm leading-snug text-destructive">
					<svg
						viewBox="0 0 16 16"
						fill="none"
						stroke="currentColor"
						stroke-width="1.5"
						width="12"
						height="12"
						class="mt-px shrink-0"
						aria-hidden="true"
					>
						<path d="M8 2L1.5 14h13L8 2zm0 5v3m0 2v.5" stroke-linecap="round" />
					</svg>
					Undoes the writes this tool recorded on that service. Ratings it did not write are left alone.
				</p>
			</div>
		{/if}
	</div>
</FlowNode>
