<script lang="ts">
	import { bucketOf } from '$lib/jobs';
	import { serviceLabels } from '$lib/relay';
	import type { JobData, TargetTally, TrackEvent } from '$lib/types/jobs';
	import { cn } from '$lib/utils';

	let { job, events }: { job: JobData; events: TrackEvent[] } = $props();

	/**
	 * One tally per destination. Every fetched track lands once on each target,
	 * so each tile adds up to the fetched count on its own; a run-wide "failed"
	 * cannot say which service it failed on.
	 */
	const tallies = $derived.by((): TargetTally[] => {
		// Live events first: a running job's tallies move as tracks land, and
		// the stream does not re-send the server's snapshot.
		if (events.length > 0) {
			const byService: Record<string, TargetTally> = {};
			for (const evt of events) {
				byService[evt.target] ??= {
					service: evt.target,
					written: 0,
					failed: 0,
					skipped: 0,
					unmatched: 0
				};
				byService[evt.target][bucketOf(evt)] += 1;
			}
			return Object.values(byService).sort((a, b) => a.service.localeCompare(b.service));
		}
		return job.by_target ?? [];
	});

	const MISSED = [
		{ key: 'failed', label: 'failed', tone: 'text-destructive' },
		{ key: 'unmatched', label: 'unmatched', tone: 'text-warn' },
		{ key: 'skipped', label: 'skipped', tone: 'text-muted-foreground' }
	] as const;

	/** Older runs carry no per-target tally: fall back to the run-wide counts. */
	const totals = $derived([
		{
			label: job.dry_run ? 'Would sync' : 'Written',
			value: job.tracks_ok,
			tone: job.tracks_ok > 0 ? 'text-ok' : 'text-foreground'
		},
		{
			label: 'Failed',
			value: job.tracks_err,
			tone: job.tracks_err > 0 ? 'text-destructive' : 'text-muted-foreground'
		},
		{ label: 'Skipped', value: job.tracks_skipped, tone: 'text-muted-foreground' }
	]);
</script>

{#snippet tile(value: number, tone: string, label: string)}
	<div class={cn('font-mono text-xl leading-none tabular-nums', tone)}>{value}</div>
	<div class="mt-1 text-xs text-muted-foreground">{label}</div>
{/snippet}

<div class="mt-4 grid grid-cols-2 gap-2 sm:grid-cols-4">
	<div class="rounded-md border border-border bg-background/40 px-3 py-2">
		{@render tile(job.tracks_fetched, 'text-foreground', 'Fetched')}
	</div>

	{#if tallies.length > 0}
		{#each tallies as tally (tally.service)}
			<div class="rounded-md border border-border bg-background/40 px-3 py-2">
				{@render tile(
					tally.written,
					tally.written > 0 ? 'text-ok' : 'text-foreground',
					serviceLabels[tally.service] ?? tally.service
				)}
				<div class="mt-1.5 flex flex-wrap gap-x-2 text-xs tabular-nums">
					{#each MISSED as missed (missed.key)}
						{#if tally[missed.key] > 0}
							<span class={missed.tone}>{tally[missed.key]} {missed.label}</span>
						{/if}
					{/each}
				</div>
			</div>
		{/each}
	{:else}
		{#each totals as stat (stat.label)}
			<div class="rounded-md border border-border bg-background/40 px-3 py-2">
				{@render tile(stat.value, stat.tone, stat.label)}
			</div>
		{/each}
	{/if}
</div>
