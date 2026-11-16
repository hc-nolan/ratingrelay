<script lang="ts">
	import { resolve } from '$app/paths';
	import { durationLabel, statusLabel, statusTone, TONE_CHIP, TONE_DOT } from '$lib/format';
	import { describeInterval, formatNextRun, summarizeSource, summarizeTargets } from '$lib/relay';
	import type { JobData } from '$lib/types/jobs';
	import { cn } from '$lib/utils';

	let {
		job,
		oncancel,
		compact = false
	}: {
		job: JobData;
		oncancel?: (id: string) => void;
		/** Dense two-line row for a rail; the full card adds timing and counts. */
		compact?: boolean;
	} = $props();

	const cancellable = $derived(
		!!oncancel &&
			!job.cancel_requested &&
			(job.status === 'queued' || job.status === 'scheduled' || job.status === 'running')
	);
	const total = $derived(job.tracks_ok + job.tracks_err);
	const tone = $derived(statusTone(job.status));
	const active = $derived(job.status === 'running' || job.status === 'queued');
</script>

<div
	class="group relative rounded-md border border-transparent px-2.5 py-2 transition-colors hover:border-border hover:bg-muted/40"
	data-status={job.status}
>
	<a href={resolve('/jobs/[id]', { id: job.id })} class="block focus-visible:outline-none">
		<div class="flex items-start gap-2">
			<span class={cn('mt-1.5 size-2 shrink-0 rounded-full', TONE_DOT[tone])}></span>
			<span class="min-w-0 flex-1 text-sm leading-snug font-medium text-foreground">
				{summarizeSource(job.source_config)}
				{#if job.targets_config.length > 0}
					<span class="text-muted-foreground">→ {summarizeTargets(job.targets_config)}</span>
				{/if}
			</span>
			{#if !active}
				<span
					class={cn(
						'shrink-0 rounded border px-1.5 py-px text-xs font-medium',
						compact ? 'hidden' : '',
						TONE_CHIP[tone]
					)}
				>
					{statusLabel(job.status)}
				</span>
			{/if}
		</div>

		<div class="mt-0.5 flex flex-wrap items-center gap-x-2 pl-4 text-xs text-muted-foreground">
			{#if job.dry_run}
				<span class="rounded border border-border px-1 py-px font-medium tracking-wide uppercase">
					Dry
				</span>
			{/if}
			{#if job.status === 'running'}
				<span class="text-info">
					{#if job.cancel_requested}
						stopping at the next track…
					{:else if job.tracks_fetched > 0}
						{total} / {job.tracks_fetched} tracks
					{:else}
						fetching…
					{/if}
				</span>
			{:else if job.status === 'queued' || job.status === 'scheduled'}
				<span>
					{job.recurring ? describeInterval(job.interval_minutes ?? 0) : 'once'}
					{#if job.run_at}<span>· next {formatNextRun(job.run_at)}</span>{/if}
				</span>
			{:else}
				{#if job.tracks_ok > 0}<span class="text-ok tabular-nums">{job.tracks_ok} written</span
					>{/if}
				{#if job.tracks_err > 0}<span class="text-destructive tabular-nums"
						>{job.tracks_err} failed</span
					>{/if}
				{#if job.tracks_skipped > 0}<span class="tabular-nums">{job.tracks_skipped} skipped</span
					>{/if}
				{#if total === 0}<span>{job.status}</span>{/if}
			{/if}
			{#if !compact && job.started_at}
				<span>· {durationLabel(job.started_at, job.completed_at)}</span>
			{/if}
			{#if job.recurring && job.interval_minutes && !compact}
				<span>· {describeInterval(job.interval_minutes)}</span>
			{/if}
		</div>

		{#if job.status === 'running' && job.tracks_fetched > 0}
			<div class="mt-1.5 ml-4 h-0.5 overflow-hidden rounded-full bg-muted">
				<div
					class="h-full bg-info transition-[width]"
					style:width="{Math.min(100, (total / job.tracks_fetched) * 100)}%"
				></div>
			</div>
		{/if}
	</a>

	{#if cancellable}
		<button
			type="button"
			onclick={() => oncancel?.(job.id)}
			class="absolute top-1.5 right-2 rounded px-1.5 py-0.5 text-xs text-muted-foreground opacity-0 transition-colors group-hover:opacity-100 hover:bg-destructive/10 hover:text-destructive focus-visible:opacity-100"
		>
			{job.status === 'running' ? 'Stop' : 'Cancel'}
		</button>
	{/if}
</div>
