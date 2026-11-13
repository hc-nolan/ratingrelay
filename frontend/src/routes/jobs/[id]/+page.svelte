<script lang="ts">
	import type { PageData } from './$types';
	import type { JobData, TrackEvent } from '$lib/types/jobs';
	import { summarizeSource, summarizeTargets, statusColor, frequencyOptions } from '$lib/relay';

	let { data }: { data: PageData } = $props();

	const TERMINAL = new Set(['completed', 'partial', 'failed', 'cancelled']);

	let job = $state<JobData | null>(data.job ?? null);
	let events = $state<TrackEvent[]>(
		TERMINAL.has(data.job?.status ?? '') ? (data.job?.events ?? []) : []
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

	// ── Display helpers ────────────────────────────────────────────────────────
	function fmtTime(iso: string | undefined): string {
		if (!iso) return '—';
		return new Date(iso).toLocaleString(undefined, { dateStyle: 'short', timeStyle: 'short' });
	}

	function freqLabel(minutes: number | undefined): string {
		return frequencyOptions.find((o) => o.value === minutes)?.label ?? `every ${minutes}m`;
	}
</script>

<svelte:head>
	<title>Job · RatingRelay</title>
</svelte:head>

<div class="page-root">
	<div class="bg-noise" aria-hidden="true"></div>

	<main class="page-main">
		<div class="top-bar">
			<a href="/" class="back-link">
				<svg viewBox="0 0 16 16" fill="currentColor" width="12" height="12" aria-hidden="true">
					<path
						d="M10 3L5 8l5 5"
						stroke="currentColor"
						stroke-width="1.5"
						fill="none"
						stroke-linecap="round"
						stroke-linejoin="round"
					/>
				</svg>
				Home
			</a>
		</div>

		{#if !job}
			<div class="not-found">Job not found.</div>
		{:else}
			<!-- Header card -->
			<div class="job-header-card" data-status={job.status}>
				<div class="job-header-top">
					<div class="job-title-row">
						<span class="job-status-dot {statusColor(job.status)}"></span>
						<h1 class="job-title">
							{summarizeSource(job.source_config)}{job.targets_config.length > 0
								? ` → ${summarizeTargets(job.targets_config)}`
								: ''}
						</h1>
					</div>

					{#if (job.status === 'queued' || job.status === 'scheduled' || job.status === 'running') && !job.cancel_requested}
						<button class="cancel-btn" onclick={cancelJob} disabled={cancelling}>
							{cancelling
								? 'Cancelling…'
								: job.status === 'running'
									? 'Stop after this track'
									: 'Cancel job'}
						</button>
					{:else if job.cancel_requested && job.status === 'running'}
						<span class="cancel-btn" aria-live="polite">Stopping…</span>
					{/if}
				</div>

				<!-- Meta row -->
				<div class="job-meta">
					<span class="meta-chip status-chip" data-status={job.status}>
						{job.status}
					</span>
					{#if job.dry_run}
						<span class="meta-chip meta-chip-dry">DRY RUN</span>
					{/if}
					{#if job.recurring && job.interval_minutes}
						<span class="meta-chip">
							{freqLabel(job.interval_minutes)}
						</span>
					{/if}
					<span class="meta-time">Started {fmtTime(job.started_at)}</span>
					{#if job.completed_at}
						<span class="meta-time">Finished {fmtTime(job.completed_at)}</span>
					{/if}
				</div>

				<!-- Progress stats -->
				<div class="stats-row">
					<div class="stat-block">
						<span class="stat-value">{job.tracks_fetched}</span>
						<span class="stat-label">Fetched</span>
					</div>
					<div class="stat-divider"></div>
					<div class="stat-block">
						<span class="stat-value stat-ok">{job.tracks_ok}</span>
						<span class="stat-label">{job.dry_run ? 'Would sync' : 'Synced'}</span>
					</div>
					<div class="stat-divider"></div>
					<div class="stat-block">
						<span class="stat-value stat-err">{job.tracks_err}</span>
						<span class="stat-label">Errors</span>
					</div>
					<div class="stat-divider"></div>
					<div class="stat-block">
						<span class="stat-value stat-skip">{job.tracks_skipped}</span>
						<span class="stat-label">Skipped</span>
					</div>

					{#if job.status === 'running' && job.tracks_fetched > 0}
						<div class="progress-bar-wrap">
							<div
								class="progress-bar-fill"
								style:width="{Math.min(
									100,
									((job.tracks_ok + job.tracks_err) / job.tracks_fetched) * 100
								)}%"
							></div>
						</div>
					{:else if job.status === 'running'}
						<div class="fetching-hint">
							<span class="status-spinner" aria-hidden="true"></span>
							Fetching tracks…
						</div>
					{/if}
				</div>

				{#if cancelError}
					<div class="cancel-error">{cancelError}</div>
				{/if}
			</div>

			<!-- Event log -->
			<div class="log-card">
				<div class="log-card-label">
					Track log
					{#if events.length > 0}
						<span class="log-count">{events.length}</span>
					{/if}
					{#if job.status === 'running'}
						<span class="status-spinner log-spinner" aria-hidden="true"></span>
					{/if}
				</div>

				{#if events.length === 0}
					<div class="log-empty">
						{#if job.status === 'running' || job.status === 'queued'}
							Waiting for tracks…
						{:else}
							No track events recorded.
						{/if}
					</div>
				{:else}
					<div class="log-list">
						{#each events as evt}
							{@const skipped = !evt.success && !evt.error}
							<div
								class="log-row"
								class:log-err={!evt.success && !!evt.error}
								class:log-skip={skipped}
							>
								<span class="log-icon">{evt.success ? '✓' : skipped ? '–' : '✗'}</span>
								<span class="log-track">{evt.artist} – {evt.title}</span>
								<span class="log-arrow">→</span>
								<span class="log-target">{evt.target}</span>
								{#if skipped}
									<span class="log-skip-msg">(already synced)</span>
								{:else if evt.error}
									<span class="log-error-msg">({evt.error})</span>
								{/if}
							</div>
						{/each}
					</div>
				{/if}
			</div>
		{/if}
	</main>
</div>

<style>
	.page-root {
		min-height: 100dvh;
		display: grid;
		place-items: start center;
		background: oklch(0.12 0.01 40);
		position: relative;
		overflow: hidden;
		padding: 2rem 1rem;
	}

	.bg-noise {
		position: fixed;
		inset: 0;
		background-image: url("data:image/svg+xml,%3Csvg viewBox='0 0 256 256' xmlns='http://www.w3.org/2000/svg'%3E%3Cfilter id='noise'%3E%3CfeTurbulence type='fractalNoise' baseFrequency='0.9' numOctaves='4' stitchTiles='stitch'/%3E%3C/filter%3E%3Crect width='100%25' height='100%25' filter='url(%23noise)' opacity='0.04'/%3E%3C/svg%3E");
		background-size: 200px 200px;
		pointer-events: none;
		z-index: 0;
		opacity: 0.6;
	}

	.page-main {
		position: relative;
		z-index: 1;
		width: 100%;
		max-width: 640px;
		display: flex;
		flex-direction: column;
		gap: 0.75rem;
	}

	.top-bar {
		display: flex;
		align-items: center;
		margin-bottom: 0.5rem;
	}

	.back-link {
		display: inline-flex;
		align-items: center;
		gap: 0.375rem;
		font-size: 0.8rem;
		font-family: var(--font-heading);
		font-weight: 500;
		color: oklch(0.45 0.015 40);
		text-decoration: none;
		transition: color 0.15s;
	}

	.back-link:hover {
		color: oklch(0.7 0.02 40);
	}

	.not-found {
		font-size: 0.9rem;
		color: oklch(0.45 0.015 40);
		padding: 2rem 0;
		text-align: center;
	}

	/* ── Job header card ─────────────────────────────────────────────────────── */
	.job-header-card {
		background: oklch(0.16 0.015 38);
		border: 1px solid oklch(0.22 0.02 38);
		display: flex;
		flex-direction: column;
		gap: 0;
		transition: border-color 0.3s;
	}

	.job-header-card[data-status='running'] {
		border-color: oklch(0.55 0.18 38 / 0.45);
	}
	.job-header-card[data-status='completed'] {
		border-color: oklch(0.45 0.12 140 / 0.4);
	}
	.job-header-card[data-status='partial'] {
		border-color: oklch(0.55 0.16 62 / 0.4);
	}
	.job-header-card[data-status='failed'] {
		border-color: oklch(0.5 0.2 22 / 0.4);
	}
	.job-header-card[data-status='cancelled'] {
		opacity: 0.6;
	}

	.job-header-top {
		display: flex;
		align-items: center;
		justify-content: space-between;
		gap: 1rem;
		padding: 0.875rem 1rem 0.625rem;
		flex-wrap: wrap;
	}

	.job-title-row {
		display: flex;
		align-items: center;
		gap: 0.625rem;
		min-width: 0;
	}

	.job-status-dot {
		width: 9px;
		height: 9px;
		border-radius: 50%;
		flex-shrink: 0;
	}

	.job-title {
		font-size: 0.9375rem;
		font-family: var(--font-heading);
		font-weight: 600;
		color: oklch(0.85 0.01 60);
		margin: 0;
		letter-spacing: -0.01em;
		white-space: nowrap;
		overflow: hidden;
		text-overflow: ellipsis;
	}

	.cancel-btn {
		padding: 0.3rem 0.75rem;
		font-size: 0.75rem;
		font-family: var(--font-heading);
		font-weight: 500;
		background: transparent;
		border: 1px solid oklch(0.3 0.01 40);
		color: oklch(0.48 0.01 40);
		cursor: pointer;
		flex-shrink: 0;
		transition:
			border-color 0.15s,
			color 0.15s;
	}

	.cancel-btn:hover:not(:disabled) {
		border-color: oklch(0.5 0.18 22 / 0.6);
		color: oklch(0.65 0.18 22);
	}

	.cancel-btn:disabled {
		opacity: 0.45;
		cursor: not-allowed;
	}

	.job-meta {
		display: flex;
		align-items: center;
		gap: 0.5rem;
		padding: 0 1rem 0.625rem;
		flex-wrap: wrap;
	}

	.meta-chip {
		display: inline-flex;
		align-items: center;
		padding: 0.15rem 0.5rem;
		font-size: 0.6875rem;
		font-family: var(--font-heading);
		font-weight: 500;
		letter-spacing: 0.04em;
		text-transform: uppercase;
		border: 1px solid oklch(0.28 0.01 40);
		color: oklch(0.48 0.01 40);
	}

	.meta-chip[data-status='running'] {
		border-color: oklch(0.5 0.15 38 / 0.4);
		color: oklch(0.65 0.12 38);
	}
	.meta-chip[data-status='completed'] {
		border-color: oklch(0.45 0.12 140 / 0.4);
		color: oklch(0.62 0.14 140);
	}
	.meta-chip[data-status='partial'] {
		border-color: oklch(0.5 0.14 62 / 0.4);
		color: oklch(0.65 0.14 62);
	}
	.meta-chip[data-status='failed'] {
		border-color: oklch(0.45 0.18 22 / 0.4);
		color: oklch(0.62 0.18 22);
	}
	.meta-chip-dry {
		border-color: oklch(0.52 0.12 240 / 0.5);
		color: oklch(0.62 0.1 240);
	}

	.meta-time {
		font-size: 0.7rem;
		color: oklch(0.4 0.01 40);
	}

	.stats-row {
		display: flex;
		align-items: center;
		gap: 0;
		padding: 0.625rem 1rem;
		border-top: 1px solid oklch(0.2 0.01 40);
		flex-wrap: wrap;
		gap: 0.5rem;
	}

	.stat-block {
		display: flex;
		flex-direction: column;
		align-items: center;
		gap: 0.1rem;
		min-width: 52px;
	}

	.stat-value {
		font-size: 1.25rem;
		font-family: var(--font-heading);
		font-weight: 700;
		font-variant-numeric: tabular-nums;
		color: oklch(0.78 0.015 50);
		line-height: 1;
	}

	.stat-ok {
		color: oklch(0.65 0.14 140);
	}
	.stat-err {
		color: oklch(0.65 0.18 22);
	}
	.stat-skip {
		color: oklch(0.55 0.015 40);
	}

	.stat-label {
		font-size: 0.625rem;
		letter-spacing: 0.08em;
		text-transform: uppercase;
		color: oklch(0.4 0.01 40);
	}

	.stat-divider {
		width: 1px;
		height: 28px;
		background: oklch(0.22 0.01 40);
		margin: 0 0.375rem;
	}

	.progress-bar-wrap {
		flex: 1;
		height: 3px;
		background: oklch(0.22 0.01 40);
		min-width: 80px;
		margin-left: 0.5rem;
	}

	.progress-bar-fill {
		height: 100%;
		background: var(--primary);
		transition: width 0.3s ease;
	}

	.fetching-hint {
		display: flex;
		align-items: center;
		gap: 0.4rem;
		font-size: 0.75rem;
		color: oklch(0.48 0.015 40);
		margin-left: 0.5rem;
	}

	.status-spinner {
		width: 10px;
		height: 10px;
		border: 1.5px solid currentColor;
		border-top-color: transparent;
		border-radius: 50%;
		display: inline-block;
		animation: spin 0.7s linear infinite;
	}

	@keyframes spin {
		to {
			transform: rotate(360deg);
		}
	}

	.cancel-error {
		padding: 0.5rem 1rem;
		font-size: 0.75rem;
		color: oklch(0.65 0.18 22);
		border-top: 1px solid oklch(0.2 0.01 40);
	}

	/* ── Log card ────────────────────────────────────────────────────────────── */
	.log-card {
		background: oklch(0.16 0.015 38);
		border: 1px solid oklch(0.22 0.02 38);
		display: flex;
		flex-direction: column;
	}

	.log-card-label {
		display: flex;
		align-items: center;
		gap: 0.5rem;
		font-size: 0.625rem;
		letter-spacing: 0.12em;
		text-transform: uppercase;
		color: oklch(0.38 0.01 40);
		padding: 0.625rem 1rem;
		font-family: var(--font-heading);
		font-weight: 700;
		border-bottom: 1px solid oklch(0.2 0.01 40);
	}

	.log-count {
		font-size: 0.6875rem;
		font-variant-numeric: tabular-nums;
		color: oklch(0.48 0.01 40);
		font-weight: 400;
		letter-spacing: 0;
		text-transform: none;
	}

	.log-spinner {
		margin-left: auto;
		color: oklch(0.55 0.1 38);
	}

	.log-empty {
		padding: 1.5rem 1rem;
		font-size: 0.8125rem;
		color: oklch(0.38 0.01 40);
		text-align: center;
	}

	.log-list {
		overflow-y: auto;
		max-height: 60vh;
	}

	.log-row {
		display: flex;
		align-items: baseline;
		gap: 0.375rem;
		padding: 0.2rem 1rem;
		font-size: 0.75rem;
		color: oklch(0.55 0.015 40);
		font-variant-numeric: tabular-nums;
		flex-wrap: nowrap;
		border-bottom: 1px solid oklch(0.185 0.01 40);
	}

	.log-row:last-child {
		border-bottom: none;
	}

	.log-row.log-err {
		background: oklch(0.5 0.18 22 / 0.04);
	}
	.log-row.log-skip {
		opacity: 0.5;
	}

	.log-icon {
		font-size: 0.65rem;
		flex-shrink: 0;
		color: oklch(0.6 0.14 140);
		min-width: 10px;
	}

	.log-err .log-icon {
		color: oklch(0.6 0.18 22);
	}
	.log-skip .log-icon {
		color: oklch(0.45 0.01 40);
	}

	.log-track {
		overflow: hidden;
		text-overflow: ellipsis;
		white-space: nowrap;
		flex: 1;
		min-width: 0;
		color: oklch(0.65 0.015 50);
	}

	.log-arrow {
		color: oklch(0.32 0.01 40);
		flex-shrink: 0;
	}

	.log-target {
		flex-shrink: 0;
		color: oklch(0.48 0.01 40);
	}

	.log-error-msg {
		flex-shrink: 0;
		color: oklch(0.55 0.15 22);
		font-style: italic;
		max-width: 200px;
		overflow: hidden;
		text-overflow: ellipsis;
		white-space: nowrap;
	}

	.log-skip-msg {
		flex-shrink: 0;
		color: oklch(0.42 0.01 40);
		font-style: italic;
	}
</style>
