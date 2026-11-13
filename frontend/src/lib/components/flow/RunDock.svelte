<script lang="ts">
	import { Button } from '$lib/components/ui/button';
	import { Checkbox } from '$lib/components/ui/checkbox';
	import { repeatOptions } from '$lib/relay';
	import { cn } from '$lib/utils';
	import FlowNode from './FlowNode.svelte';

	let {
		repeat = $bindable(),
		dryRun = $bindable(),
		mode,
		busy,
		canRun,
		onrun
	}: {
		/** Minutes between runs; 0 means "once". */
		repeat: number;
		dryRun: boolean;
		mode: 'run' | 'reset';
		busy: boolean;
		canRun: boolean;
		onrun: () => void;
	} = $props();

	const label = $derived(
		busy ? 'Queueing…' : mode === 'reset' ? 'Queue reset' : dryRun ? 'Preview run' : 'Queue run'
	);
</script>

<!-- The trigger is the last node in the chain, not a bar bolted to the bottom
     of the screen: what you set here is part of the flow you just built. -->
<FlowNode label="Trigger" tone={mode === 'reset' ? 'danger' : 'neutral'}>
	<div class="flex flex-wrap items-center gap-x-4 gap-y-3">
		{#if mode === 'run'}
			<label class="flex items-center gap-2 text-sm text-muted-foreground">
				<span>Repeat</span>
				<select
					class="h-8 rounded-md border border-border bg-background px-2 text-sm text-foreground"
					bind:value={repeat}
				>
					{#each repeatOptions as opt (opt.value)}
						<option value={opt.value}>{opt.label}</option>
					{/each}
				</select>
			</label>
		{/if}

		{#if mode === 'run' && repeat === 0}
			<label class="flex cursor-pointer items-center gap-2 text-sm text-muted-foreground">
				<Checkbox bind:checked={dryRun} />
				<span>Dry run — write nothing</span>
			</label>
		{/if}

		{#if mode === 'reset'}
			<span class="text-sm text-destructive">Writes that cannot be undone are left in place.</span>
		{/if}

		<Button
			size="lg"
			class={cn(
				'ml-auto h-9 px-4 text-sm',
				mode === 'reset' && 'bg-destructive text-destructive-foreground hover:bg-destructive/90'
			)}
			disabled={!canRun || busy}
			aria-busy={busy}
			onclick={onrun}
		>
			{#if busy}
				<span
					class="size-3.5 animate-spin rounded-full border border-current border-t-transparent"
					aria-hidden="true"
				></span>
			{:else}
				<svg viewBox="0 0 20 20" width="15" height="15" fill="currentColor" aria-hidden="true">
					{#if mode === 'reset'}
						<path
							d="M8 2a6 6 0 1 0 4.5 2M8 2V5m0-3h3"
							fill="none"
							stroke="currentColor"
							stroke-width="1.6"
							stroke-linecap="round"
						/>
					{:else}
						<path
							fill-rule="evenodd"
							d="M10 18a8 8 0 100-16 8 8 0 000 16zM9.555 7.168A1 1 0 008 8v4a1 1 0 001.555.832l3-2a1 1 0 000-1.664l-3-2z"
							clip-rule="evenodd"
						/>
					{/if}
				</svg>
			{/if}
			{label}
		</Button>
	</div>
</FlowNode>
