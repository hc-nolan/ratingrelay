<script lang="ts">
	import FlowNode from './FlowNode.svelte';
	import ServiceMark from './ServiceMark.svelte';
	import { cn } from '$lib/utils';
	import type { SourceOption } from '$lib/types/relay';
	import { markFor } from '$lib/types/relay';

	let {
		options,
		selected = $bindable(),
		resetService = $bindable(),
		resetOptions
	}: {
		options: SourceOption[];
		selected: string;
		resetService: string;
		resetOptions: SourceOption[];
	} = $props();
</script>

<FlowNode label="Source" tone={selected === 'reset' ? 'danger' : 'source'}>
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
				<ServiceMark service={markFor(option.id)} size={18} />
				<span
					class={cn('text-base font-medium', active ? 'text-foreground' : 'text-muted-foreground')}
				>
					{option.name}
				</span>
			</button>
		{/each}
	</div>

	<!-- Only a reset has more to choose here; the direction and thresholds of a
	     relay live in the Rating box below. -->
	{#if selected === 'reset'}
		<div class="mt-3 flex flex-wrap items-center gap-x-2 gap-y-2 border-t border-border/60 pt-3">
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
						<ServiceMark service={markFor(option.id)} size={14} />
						{option.name}
					</button>
				{/each}
			</div>
			<p class="w-full text-sm leading-snug text-muted-foreground">
				Undoes the writes this tool recorded on that service. Ratings it did not write are left
				alone.
			</p>
		</div>
	{/if}
</FlowNode>
