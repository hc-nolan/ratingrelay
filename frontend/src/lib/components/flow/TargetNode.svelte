<script lang="ts">
	import { plexStarOptions } from '$lib/relay';
	import type { ListenBrainzTargetConfig, PlexTargetConfig, TargetOption } from '$lib/types/relay';
	import { cn } from '$lib/utils';
	import { NativeSelect } from '$lib/components/ui/native-select';
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

	const choices = [
		{ value: 'love', label: 'Love' },
		{ value: 'hate', label: 'Hate' }
	] as const;
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

		<div class="flex flex-wrap items-center gap-x-3 gap-y-2">
			<ServiceMark service={option.id} size={22} />
			<span class="text-lg font-medium">{option.name}</span>
			<div class="ml-auto flex items-center gap-1.5">
				{#if option.id === 'plex' && plexConfig}
					<span class="text-sm text-muted-foreground">Rate as</span>
					<NativeSelect bind:value={plexConfig.rating}>
						{#each plexStarOptions as opt (opt.value)}
							<option value={opt.value}>{opt.label}</option>
						{/each}
					</NativeSelect>
				{:else if option.id === 'lastfm'}
					<span class="text-sm text-muted-foreground">Love every track</span>
				{:else if lbzConfig}
					<div
						class="inline-flex items-center gap-0.5 rounded-md border border-border bg-background p-0.5"
					>
						{#each choices as choice (choice.value)}
							<button
								type="button"
								class={cn(
									'rounded px-2.5 py-0.5 text-sm transition-colors',
									lbzConfig.feedbackType === choice.value
										? 'bg-primary text-primary-foreground'
										: 'text-muted-foreground hover:text-foreground'
								)}
								onclick={() => (lbzConfig.feedbackType = choice.value)}
								aria-pressed={lbzConfig.feedbackType === choice.value}
							>
								{choice.label}
							</button>
						{/each}
					</div>
				{/if}
			</div>
		</div>
	</FlowNode>
{:else}
	<!-- A ghost is a card you can pick, not a link: the whole surface answers to
	     hover, press and keyboard focus, and picking it turns it into a node. -->
	<button
		type="button"
		onclick={onadd}
		aria-label="Add {option.name} as a target"
		class={cn(
			'group flex w-full items-center gap-3 rounded-lg border border-dashed border-border/70 bg-card/40 px-4 py-3 text-left',
			'transition-colors motion-safe:transition-transform',
			'hover:border-primary/60 hover:bg-primary/5',
			'active:scale-[0.99] active:border-primary active:bg-primary/10',
			'focus-visible:ring-2 focus-visible:ring-ring focus-visible:outline-none'
		)}
	>
		<ServiceMark service={option.id} size={22} />
		<span class="min-w-0 flex-1">
			<span class="block text-base font-medium text-foreground">{option.name}</span>
			<span class="block truncate text-sm text-muted-foreground">{option.hint}</span>
		</span>
		<span
			class="grid size-7 shrink-0 place-items-center rounded-md border border-border text-muted-foreground transition-colors group-hover:border-primary/60 group-hover:bg-primary/10 group-hover:text-primary group-active:bg-primary group-active:text-primary-foreground"
			aria-hidden="true"
		>
			<svg viewBox="0 0 16 16" width="11" height="11">
				<path d="M8 3v10M3 8h10" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" />
			</svg>
		</span>
	</button>
{/if}
