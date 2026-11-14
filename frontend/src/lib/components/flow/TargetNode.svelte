<script lang="ts">
	import { plexStarOptions } from '$lib/relay';
	import type { ListenBrainzTargetConfig, PlexTargetConfig, TargetOption } from '$lib/types/relay';
	import { cn } from '$lib/utils';
	import { NativeSelect } from '$lib/components/ui/native-select';
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
		/** Allocated cards are the same card, switched on: filled, accent-edged, configured. */
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
			<ServiceMark service={option.id} size={22} />
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
			{#if option.id === 'plex' && plexConfig}
				<span class="mr-1.5 text-sm text-muted-foreground">Rate as</span>
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
			aria-label="Add {option.name} as a target"
			class="absolute inset-0 rounded-lg focus-visible:ring-2 focus-visible:ring-ring focus-visible:outline-none"
		></button>
	{/if}
</div>
