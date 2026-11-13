<script lang="ts">
	import type { JobData } from '$lib/types/jobs';
	import { cn } from '$lib/utils';
	import JobCard from './JobCard.svelte';

	let {
		jobs,
		oncancel,
		open = $bindable(true)
	}: { jobs: JobData[]; oncancel: (id: string) => void; open?: boolean } = $props();

	const active = $derived(
		jobs.filter((j) => j.status === 'queued' || j.status === 'scheduled' || j.status === 'running')
	);
</script>

<aside
	class={cn(
		'flex h-full shrink-0 flex-col border-l bg-card/40 transition-[width] duration-200 ease-out',
		open ? 'w-80' : 'w-12'
	)}
	aria-label="Jobs"
>
	<div
		class={cn(
			'flex items-center border-b border-border/70',
			open ? 'gap-2 px-4 py-2.5' : 'justify-center py-2.5'
		)}
	>
		{#if open}
			<span
				class="font-heading text-xs font-semibold tracking-[0.18em] text-muted-foreground uppercase"
			>
				Jobs
			</span>
			{#if active.length > 0}
				<span class="rounded-full bg-primary/15 px-2 py-px text-[0.6875rem] text-primary">
					{active.length} live
				</span>
			{/if}
		{/if}
		<button
			type="button"
			onclick={() => (open = !open)}
			aria-expanded={open}
			aria-label={open ? 'Collapse jobs' : 'Expand jobs'}
			title={open ? 'Collapse' : 'Expand'}
			class={cn(
				'grid size-7 place-items-center rounded text-muted-foreground transition-colors hover:bg-muted hover:text-foreground',
				open && 'ml-auto'
			)}
		>
			<svg
				viewBox="0 0 16 16"
				width="12"
				height="12"
				aria-hidden="true"
				class={cn('transition-transform', open ? 'rotate-0' : 'rotate-180')}
			>
				<path
					d="M10 3L5 8l5 5"
					fill="none"
					stroke="currentColor"
					stroke-width="1.6"
					stroke-linecap="round"
					stroke-linejoin="round"
				/>
			</svg>
		</button>
	</div>

	{#if open}
		<div class="min-h-0 flex-1 space-y-1.5 overflow-y-auto p-2">
			{#if jobs.length === 0}
				<p class="px-2 py-8 text-center text-base text-muted-foreground">No runs yet.</p>
			{:else}
				{#each jobs as job (job.id)}
					<JobCard {job} {oncancel} />
				{/each}
			{/if}
		</div>
	{:else}
		<div class="flex min-h-0 flex-1 flex-col items-center gap-2 overflow-hidden py-3">
			<span
				class="font-heading text-xs font-semibold tracking-[0.18em] whitespace-nowrap text-muted-foreground uppercase [writing-mode:vertical-rl]"
			>
				Jobs
			</span>
			{#if active.length > 0}
				<span class="rounded-full bg-primary/15 px-2 py-px text-[0.6875rem] text-primary">
					{active.length}
				</span>
			{/if}
		</div>
	{/if}
</aside>
