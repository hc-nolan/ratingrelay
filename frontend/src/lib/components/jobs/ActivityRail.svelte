<script lang="ts">
	import { resolve } from '$app/paths';
	import JobCard from './JobCard.svelte';
	import type { JobData } from '$lib/types/jobs';

	let { jobs, oncancel }: { jobs: JobData[]; oncancel: (id: string) => void } = $props();

	const ACTIVE = new Set(['running', 'queued', 'scheduled']);

	const active = $derived(
		jobs
			.filter((j) => ACTIVE.has(j.status))
			.sort((a, b) => (a.status === 'running' ? -1 : b.status === 'running' ? 1 : 0))
	);
	const recent = $derived(
		jobs
			.filter((j) => !ACTIVE.has(j.status))
			.sort((a, b) => +new Date(b.created_at) - +new Date(a.created_at))
			.slice(0, 7)
	);
</script>

<!-- The right rail answers "what is running" and "what just happened" without
     becoming a second page. Everything older is one link away in /history. -->
<aside
	class="hidden w-80 shrink-0 flex-col border-l border-border bg-sidebar/60 lg:flex"
	aria-label="Activity"
>
	<div class="flex items-center gap-2 border-b border-border px-4 py-2.5">
		<span class="eyebrow">Activity</span>
		{#if active.length > 0}
			<span class="rounded-full bg-info/15 px-1.5 py-px font-mono text-xs text-info tabular-nums">
				{active.length} live
			</span>
		{/if}
		<a
			href={resolve('/history')}
			class="ml-auto text-xs text-muted-foreground underline-offset-4 hover:text-foreground hover:underline"
		>
			All runs
		</a>
	</div>

	<div class="min-h-0 flex-1 overflow-y-auto px-1.5 py-2">
		{#if jobs.length === 0}
			<p class="px-2.5 py-6 text-sm leading-snug text-muted-foreground">
				Nothing has run yet. Queue a run and every track it touches lands here.
			</p>
		{:else}
			{#if active.length > 0}
				<div class="mb-3">
					<div class="px-2.5 pb-1 text-xs font-medium text-muted-foreground">In flight</div>
					{#each active as job (job.id)}
						<JobCard {job} {oncancel} compact />
					{/each}
				</div>
			{/if}

			<div>
				<div class="px-2.5 pb-1 text-xs font-medium text-muted-foreground">Recent</div>
				{#each recent as job (job.id)}
					<JobCard {job} {oncancel} compact />
				{:else}
					<p class="px-2.5 py-2 text-sm text-muted-foreground">No finished runs yet.</p>
				{/each}
			</div>
		{/if}
	</div>
</aside>
