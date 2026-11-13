<script lang="ts">
	import { plexStarOptions } from '$lib/relay';
	import type { ListenBrainzTargetConfig, PlexTargetConfig, TargetOption } from '$lib/types/relay';
	import { cn } from '$lib/utils';
	import FlowNode from './FlowNode.svelte';
	import ServiceMark from './ServiceMark.svelte';

	let {
		option,
		allocated,
		onadd,
		onremove,
		plexConfig,
		lbzConfig
	}: {
		option: TargetOption;
		/** Allocated nodes are solid and configured; ghosts are a dashed invitation. */
		allocated: boolean;
		onadd: () => void;
		onremove: () => void;
		plexConfig?: PlexTargetConfig;
		lbzConfig?: ListenBrainzTargetConfig;
	} = $props();
</script>

{#if allocated}
	<FlowNode label="To" tone="target">
		{#snippet actions()}
			<button
				type="button"
				onclick={onremove}
				title="Remove this target"
				aria-label="Remove {option.name}"
				class="grid size-6 place-items-center rounded text-muted-foreground transition-colors hover:bg-muted hover:text-foreground"
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
		{/snippet}

		<div class="flex items-center gap-3">
			<ServiceMark service={option.id} size={24} />
			<span class="font-heading text-lg font-medium">{option.name}</span>
			<span
				class="ml-auto inline-flex items-center gap-1.5 rounded-md border border-border bg-muted/40 px-2.5 py-1 text-base"
			>
				{#if option.id === 'plex' && plexConfig}
					<span class="text-muted-foreground">Rate as</span>
					<select
						class="rounded bg-background px-1.5 py-0.5 text-base text-foreground"
						bind:value={plexConfig.rating}
					>
						{#each plexStarOptions as opt (opt.value)}
							<option value={opt.value}>{opt.label}</option>
						{/each}
					</select>
				{:else if option.id === 'lastfm'}
					<span class="text-muted-foreground">Love tracks</span>
				{:else if lbzConfig}
					{#each [{ value: 'love', label: 'Love' }, { value: 'hate', label: 'Hate' }] as choice (choice.value)}
						<button
							type="button"
							class={cn(
								'rounded px-2.5 py-0.5 text-base transition-colors',
								lbzConfig.feedbackType === choice.value
									? 'bg-primary text-primary-foreground'
									: 'text-muted-foreground hover:text-foreground'
							)}
							onclick={() => (lbzConfig.feedbackType = choice.value as 'love' | 'hate')}
						>
							{choice.label}
						</button>
					{/each}
				{/if}
			</span>
		</div>
	</FlowNode>
{:else}
	<button
		type="button"
		onclick={onadd}
		class="group flex w-full items-center gap-3 rounded-lg border border-dashed border-border/70 px-4 py-3.5 text-left transition-colors hover:border-primary/60 hover:bg-primary/5"
	>
		<span
			class="grid size-6 shrink-0 place-items-center rounded-full border border-border text-muted-foreground transition-colors group-hover:border-primary group-hover:text-primary"
		>
			<svg viewBox="0 0 16 16" width="10" height="10" aria-hidden="true">
				<path d="M8 3v10M3 8h10" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" />
			</svg>
		</span>
		<span class="font-heading text-base font-medium">{option.name}</span>
		<span class="truncate text-base text-muted-foreground">{option.hint}</span>
	</button>
{/if}
