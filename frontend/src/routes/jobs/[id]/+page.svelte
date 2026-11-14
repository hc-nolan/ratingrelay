<script lang="ts">
	import { untrack } from 'svelte';
	import { resolve } from '$app/paths';
	import type { PageData } from './$types';
	import AppHeader from '$lib/components/AppHeader.svelte';
	import {
		durationLabel,
		statusLabel,
		statusTone,
		TONE_CHIP,
		TONE_DOT,
		whenLabel
	} from '$lib/format';
	import { frequencyOptions, summarizeSource, summarizeTargets, serviceLabels } from '$lib/relay';
	import type { JobData, TrackEvent } from '$lib/types/jobs';
	import { cn } from '$lib/utils';

	let { data }: { data: PageData } = $props();

	const TERMINAL = new Set(['completed', 'partial', 'failed', 'cancelled']);

	/** How many log rows are on screen before "show all". */
	const PAGE = 200;

	// The server sends the job as it stands; after this the stream owns it.
	let job = $state<JobData | null>(untrack(() => data.job ?? null));
	let events = $state<TrackEvent[]>(
		untrack(() => (TERMINAL.has(data.job?.status ?? '') ? (data.job?.events ?? []) : []))
	);

	$effect(() => {
		const id = data.jobId as string;
		const initialStatus = data.job?.status as string | undefined;
		if (!id || !initialStatus || TERMINAL.has(initialStatus)) return;

		const es = new EventSource(`/api/relay/jobs/${id}/stream`);

		es.addEventListener('status', (e) => {
			const d = JSON.parse(e.data);
			if (job) job = { ...job, ...d };
		});
		es.addEventListener('track', (e) => {
			const d: TrackEvent = JSON.parse(e.data);
			events = [...events, d];
		});
		es.addEventListener('done', (e) => {
			const d = JSON.parse(e.data);
			if (job) job = { ...job, ...d };
			es.close();
		});
		es.onerror = () => es.close();

		return () => es.close();
	});

	// ── Cancel ────────────────────────────────────────────────────────────────
	let cancelling = $state(false);
	let cancelError = $state('');

	async function cancelJob() {
		if (!job) return;
		cancelling = true;
		cancelError = '';
		try {
			const res = await fetch(`/api/relay/jobs/${job.id}`, { method: 'DELETE' });
			if (!res.ok) {
				const err = await res.json().catch(() => ({ detail: 'Cancel failed' }));
				cancelError = err.detail ?? 'Cancel failed';
				return;
			}
			const updated: JobData = await res.json();
			job = { ...job, ...updated };
		} catch {
			cancelError = 'Network error';
		} finally {
			cancelling = false;
		}
	}

	// ── Log filtering ─────────────────────────────────────────────────────────
	type Bucket = 'all' | 'written' | 'skipped' | 'failed' | 'unmatched';

	let bucket = $state<Bucket>('all');
	let search = $state('');
	let showAll = $state(false);

	const REASON_LABEL: Record<string, string> = {
		already_synced: 'already in sync',
		withdrawn: 'rating withdrawn',
		no_match: 'no match found',
		ambiguous_match: 'ambiguous match',
		no_recording_mbid: 'no recording MBID',
		missing_metadata: 'missing artist or title',
		not_configured: 'service not configured',
		write_failed: 'write failed',
		unrecorded_target: 'no recorded write'
	};

	function describe(evt: TrackEvent): string {
		if (evt.error) return evt.error;
		if (evt.reason && REASON_LABEL[evt.reason]) return REASON_LABEL[evt.reason];
		if (evt.outcome === 'written') return 'written';
		if (evt.outcome === 'reverted') return 'reverted';
		return evt.reason ?? evt.outcome ?? '';
	}

	function bucketOf(evt: TrackEvent): Exclude<Bucket, 'all'> {
		if (evt.success) return 'written';
		if (evt.outcome === 'failed' || evt.error) return 'failed';
		if (evt.outcome === 'unmatched') return 'unmatched';
		return 'skipped';
	}

	const buckets = $derived.by(() => {
		const counts = { written: 0, skipped: 0, failed: 0, unmatched: 0 };
		for (const evt of events) counts[bucketOf(evt)] += 1;
		return [
			{ id: 'all' as const, label: 'All', count: events.length },
			{ id: 'written' as const, label: 'Written', count: counts.written },
			{ id: 'skipped' as const, label: 'Skipped', count: counts.skipped },
			{ id: 'failed' as const, label: 'Failed', count: counts.failed },
			{ id: 'unmatched' as const, label: 'Unmatched', count: counts.unmatched }
		];
	});

	/** Newest first: a log is read from the tail, live or after the fact. */
	const filtered = $derived.by(() => {
		const needle = search.trim().toLowerCase();
		const rows = events
			.filter((evt) => bucket === 'all' || bucketOf(evt) === bucket)
			.filter((evt) => !needle || `${evt.artist} ${evt.title}`.toLowerCase().includes(needle));
		return rows.slice().reverse();
	});

	const visible = $derived(showAll ? filtered : filtered.slice(0, PAGE));

	const tone = $derived(statusTone(job?.status ?? ''));
	const total = $derived((job?.tracks_ok ?? 0) + (job?.tracks_err ?? 0));

	/**
	 * One tile per destination, because "720 written" is two numbers added
	 * together and neither of them is checkable. Falls back to the aggregate
	 * when the job carries no per-target tally (older runs, no events).
	 */
	const writeTiles = $derived.by(() => {
		// Live events first: a running job's tallies move as tracks land, and
		// the stream does not re-send the server's snapshot.
		if (events.length > 0) {
			const counts: Record<string, number> = {};
			for (const evt of events) {
				if (bucketOf(evt) === 'written') counts[evt.target] = (counts[evt.target] ?? 0) + 1;
			}
			return Object.entries(counts)
				.sort(([a], [b]) => a.localeCompare(b))
				.map(([service, value]) => ({
					label: serviceLabels[service] ?? service,
					value,
					tone: value > 0 ? 'text-ok' : 'text-foreground'
				}));
		}

		const tally = job?.by_target ?? [];
		if (tally.length > 0) {
			return tally.map((t) => ({
				label: serviceLabels[t.service] ?? t.service,
				value: t.written,
				tone: t.written > 0 ? 'text-ok' : 'text-foreground'
			}));
		}

		return [
			{
				label: job?.dry_run ? 'Would sync' : 'Written',
				value: job?.tracks_ok ?? 0,
				tone: (job?.tracks_ok ?? 0) > 0 ? 'text-ok' : 'text-foreground'
			}
		];
	});

	const stats = $derived([
		{ label: 'Fetched', value: job?.tracks_fetched ?? 0, tone: 'text-foreground' },
		...writeTiles,
		{
			label: 'Failed',
			value: job?.tracks_err ?? 0,
			// A zero is not an error: only a real failure is painted red.
			tone: (job?.tracks_err ?? 0) > 0 ? 'text-destructive' : 'text-muted-foreground'
		},
		{ label: 'Skipped', value: job?.tracks_skipped ?? 0, tone: 'text-muted-foreground' }
	]);
</script>

<svelte:head>
	<title>Run · RatingRelay</title>
</svelte:head>

<div class="flex h-dvh w-full flex-col overflow-hidden bg-background text-foreground">
	<AppHeader />

	<main class="min-h-0 flex-1 overflow-y-auto">
		<div class="mx-auto w-full max-w-5xl space-y-4 px-8 py-6">
			<a
				href={resolve('/history')}
				class="inline-flex items-center gap-1.5 text-xs text-muted-foreground transition-colors hover:text-foreground"
			>
				<svg viewBox="0 0 16 16" width="11" height="11" aria-hidden="true">
					<path
						d="M10 3L5 8l5 5"
						stroke="currentColor"
						stroke-width="1.6"
						fill="none"
						stroke-linecap="round"
						stroke-linejoin="round"
					/>
				</svg>
				History
			</a>

			{#if !job}
				<p class="py-20 text-center text-sm text-muted-foreground">This run does not exist.</p>
			{:else}
				<!-- ── What ran ─────────────────────────────────────────────────── -->
				<section class="panel px-4 pt-3.5 pb-4">
					<div class="flex flex-wrap items-start gap-3">
						<div class="min-w-0 flex-1">
							<div class="flex items-center gap-2">
								<span class={cn('size-2 shrink-0 rounded-full', TONE_DOT[tone])}></span>
								<span
									class={cn('rounded border px-1.5 py-px text-xs font-medium', TONE_CHIP[tone])}
								>
									{statusLabel(job.status)}
								</span>
								{#if job.dry_run}
									<span
										class="rounded border border-border px-1.5 py-px text-xs font-medium tracking-wide text-muted-foreground uppercase"
									>
										Dry run
									</span>
								{/if}
								{#if job.recurring && job.interval_minutes}
									<span class="text-xs text-muted-foreground">
										{frequencyOptions.find((o) => o.value === job?.interval_minutes)?.label ??
											`every ${job.interval_minutes}m`}
									</span>
								{/if}
							</div>

							<h1 class="mt-1.5 text-lg font-semibold tracking-tight">
								{summarizeSource(job.source_config)}
								{#if job.targets_config.length > 0}
									<span class="font-normal text-muted-foreground">
										→ {summarizeTargets(job.targets_config)}
									</span>
								{/if}
							</h1>

							<div
								class="mt-1 flex flex-wrap items-center gap-x-3 gap-y-1 text-xs text-muted-foreground"
							>
								{#if job.started_at}
									<span>started {whenLabel(job.started_at)}</span>
								{/if}
								<span class="font-mono">{job.id.slice(0, 8)}</span>
								{#if job.started_at}
									<span>took {durationLabel(job.started_at, job.completed_at) || '—'}</span>
								{/if}
								{#if job.completed_at}
									<span>finished {whenLabel(job.completed_at)}</span>
								{/if}
							</div>
						</div>

						{#if (job.status === 'queued' || job.status === 'scheduled' || job.status === 'running') && !job.cancel_requested}
							<button
								type="button"
								onclick={cancelJob}
								disabled={cancelling}
								class="rounded-md border border-border px-2.5 py-1.5 text-sm text-muted-foreground transition-colors hover:border-destructive/50 hover:text-destructive disabled:opacity-50"
							>
								{cancelling
									? 'Stopping…'
									: job.status === 'running'
										? 'Stop after this track'
										: 'Cancel run'}
							</button>
						{:else if job.cancel_requested && job.status === 'running'}
							<span class="text-sm text-warn" aria-live="polite">Stopping…</span>
						{/if}
					</div>

					<div class="mt-4 grid grid-cols-2 gap-2 sm:grid-cols-3 lg:grid-cols-5">
						{#each stats as stat (stat.label)}
							<div class="rounded-md border border-border bg-background/40 px-3 py-2">
								<div class={cn('font-mono text-xl leading-none tabular-nums', stat.tone)}>
									{stat.value}
								</div>
								<div class="mt-1 text-xs text-muted-foreground">{stat.label}</div>
							</div>
						{/each}
					</div>

					{#if job.status === 'running'}
						<div class="mt-3">
							{#if job.tracks_fetched > 0}
								<div class="h-0.5 overflow-hidden rounded-full bg-muted">
									<div
										class="h-full bg-info transition-[width]"
										style:width="{Math.min(100, (total / job.tracks_fetched) * 100)}%"
									></div>
								</div>
							{:else}
								<span class="text-xs text-muted-foreground">Fetching the source…</span>
							{/if}
						</div>
					{/if}

					{#if cancelError}
						<p class="mt-2 text-sm text-destructive">{cancelError}</p>
					{/if}
				</section>

				<!-- ── What it did ──────────────────────────────────────────────── -->
				<section class="panel overflow-hidden">
					<div class="flex flex-wrap items-center gap-2 border-b border-border px-3 py-2">
						<span class="eyebrow">Track log</span>
						<div class="flex flex-wrap items-center gap-0.5">
							{#each buckets as b (b.id)}
								{#if b.id === 'all' || b.count > 0}
									<button
										type="button"
										onclick={() => {
											bucket = b.id;
											showAll = false;
										}}
										aria-pressed={bucket === b.id}
										class={cn(
											'rounded px-2 py-0.5 text-xs transition-colors',
											bucket === b.id
												? 'bg-muted text-foreground'
												: 'text-muted-foreground hover:text-foreground'
										)}
									>
										{b.label}
										<span class="font-mono tabular-nums opacity-60">{b.count}</span>
									</button>
								{/if}
							{/each}
						</div>
						<input
							bind:value={search}
							type="search"
							placeholder="Filter tracks…"
							aria-label="Filter tracks"
							class="ml-auto h-7 w-44 rounded-md border border-border bg-background px-2 text-xs text-foreground placeholder:text-muted-foreground/70 focus:border-primary/60 focus:outline-none"
						/>
					</div>

					{#if events.length === 0}
						<p class="px-4 py-10 text-center text-sm text-muted-foreground">
							{#if job.status === 'running' || job.status === 'queued'}
								Waiting for the first track…
							{:else}
								This run did not record any tracks.
							{/if}
						</p>
					{:else if visible.length === 0}
						<p class="px-4 py-10 text-center text-sm text-muted-foreground">
							No tracks match this filter.
						</p>
					{:else}
						<ul class="divide-y divide-border/60">
							{#each visible as evt, i (`${visible.length - i}`)}
								{@const b = bucketOf(evt)}
								<li class="flex items-center gap-2.5 px-3 py-1">
									<span
										class={cn(
											'w-3 shrink-0 font-mono text-sm',
											b === 'written'
												? 'text-ok'
												: b === 'failed'
													? 'text-destructive'
													: b === 'unmatched'
														? 'text-warn'
														: 'text-muted-foreground/70'
										)}
										aria-hidden="true"
									>
										{b === 'written' ? '✓' : b === 'failed' ? '✗' : b === 'unmatched' ? '?' : '–'}
									</span>
									<span class="min-w-0 flex-1 truncate text-sm">
										<span class="text-foreground">{evt.artist}</span>
										<span class="text-muted-foreground"> – {evt.title}</span>
									</span>
									<span class="shrink-0 text-xs text-muted-foreground">{evt.target}</span>
									{#if describe(evt)}
										<span
											class={cn(
												'max-w-72 shrink-0 truncate text-xs',
												b === 'failed'
													? 'text-destructive'
													: b === 'unmatched'
														? 'text-warn'
														: 'text-muted-foreground'
											)}
											title={describe(evt)}
										>
											{describe(evt)}
										</span>
									{/if}
								</li>
							{/each}
						</ul>

						{#if filtered.length > PAGE && !showAll}
							<div class="border-t border-border px-3 py-2">
								<button
									type="button"
									onclick={() => (showAll = true)}
									class="text-xs text-muted-foreground transition-colors hover:text-foreground"
								>
									Showing the latest {PAGE} of {filtered.length} — show all
								</button>
							</div>
						{/if}
					{/if}
				</section>
			{/if}
		</div>
	</main>
</div>
