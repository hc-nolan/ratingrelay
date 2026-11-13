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
	<button
		type="button"
		onclick={onadd}
		class="group flex w-full items-center gap-2.5 rounded-lg border border-dashed border-border/70 px-4 py-2.5 text-left transition-colors hover:border-primary/60 hover:bg-primary/5"
	>
		<svg
			viewBox="0 0 16 16"
			width="11"
			height="11"
			class="shrink-0 text-muted-foreground transition-colors group-hover:text-primary"
			aria-hidden="true"
		>
			<path d="M8 3v10M3 8h10" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" />
		</svg>
		<span class="text-sm font-medium text-foreground">{option.name}</span>
		<span class="truncate text-sm text-muted-foreground">{option.hint}</span>
	</button>
{/if}
