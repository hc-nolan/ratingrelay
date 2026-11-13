<script lang="ts">
	import { resolve } from '$app/paths';
	import {
		describeInterval,
		formatNextRun,
		statusColor,
		summarizeSource,
		summarizeTargets
	} from '$lib/relay';
	import type { JobData } from '$lib/types/jobs';
	import { cn } from '$lib/utils';

	let { job, oncancel }: { job: JobData; oncancel: (id: string) => void } = $props();

	const cancellable = $derived(
		!job.cancel_requested &&
			(job.status === 'queued' || job.status === 'scheduled' || job.status === 'running')
	);
	const total = $derived(job.tracks_ok + job.tracks_err);
</script>

<div
	class="group relative rounded-md border border-border/70 bg-transparent p-3 transition-colors hover:border-border hover:bg-muted/30"
	data-status={job.status}
>
	<a href={resolve('/jobs/[id]', { id: job.id })} class="block focus-visible:outline-none">
		<div class="flex items-start gap-1.5">
			<span class={cn('mt-1.5 size-2 shrink-0 rounded-full', statusColor(job.status))}></span>
			<span class="min-w-0 flex-1 text-base leading-snug font-medium text-foreground">
				{summarizeSource(job.source_config)}
				{#if job.targets_config.length > 0}
					<span class="text-muted-foreground">→ {summarizeTargets(job.targets_config)}</span>
				{/if}
			</span>
		</div>

		<div
			class="mt-1.5 flex flex-wrap items-center gap-x-2 gap-y-1 pl-3.5 text-sm text-muted-foreground"
		>
			{#if job.dry_run}
				<span
					class="rounded border border-border px-1.5 py-px text-[0.625rem] tracking-wider uppercase"
				>
					Dry
				</span>
			{/if}
			{#if job.recurring && job.interval_minutes}
				<span>{describeInterval(job.interval_minutes)}</span>
				{#if job.run_at && job.status === 'scheduled'}
					<span>· next {formatNextRun(job.run_at)}</span>
				{/if}
			{/if}
			{#if job.status === 'running'}
				<span class="text-foreground/70">
					{#if job.cancel_requested}
						stopping at the next track…
					{:else if job.tracks_fetched > 0}
						{total} / {job.tracks_fetched} tracks
					{:else}
						fetching…
					{/if}
				</span>
			{:else if job.status === 'queued' || job.status === 'scheduled'}
				<span>{job.status}</span>
			{:else}
				{#if job.tracks_ok > 0}<span class="text-emerald-500">{job.tracks_ok} ✓</span>{/if}
				{#if job.tracks_err > 0}<span class="text-destructive">{job.tracks_err} ✗</span>{/if}
				{#if job.tracks_skipped > 0}
					<span>{job.tracks_skipped} skipped</span>
				{/if}
				{#if total === 0}
					<span>{job.status}</span>
				{/if}
			{/if}
		</div>

		{#if job.status === 'running' && job.tracks_fetched > 0}
			<div class="mt-1.5 ml-3 h-0.5 overflow-hidden rounded-full bg-muted">
				<div
					class="h-full bg-primary transition-[width]"
					style:width="{Math.min(100, (total / job.tracks_fetched) * 100)}%"
				></div>
			</div>
		{/if}
	</a>

	{#if cancellable}
		<button
			type="button"
			onclick={() => oncancel(job.id)}
			class="absolute top-2 right-2 rounded px-2 py-1 text-sm text-muted-foreground opacity-0 transition-colors group-hover:opacity-100 hover:bg-destructive/10 hover:text-destructive focus-visible:opacity-100"
		>
			Cancel
		</button>
	{/if}
</div>
