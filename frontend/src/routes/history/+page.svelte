<script lang="ts">
	import { Input } from '$lib/components/ui/input';
	import { Select } from '$lib/components/ui/select';
	import AppHeader from '$lib/components/AppHeader.svelte';
	import JobCard from '$lib/components/jobs/JobCard.svelte';
	import { dayKey, dayLabel } from '$lib/format';
	import type { JobData } from '$lib/types/jobs';
	import { cn } from '$lib/utils';

	const PAGE = 25;

	let jobs = $state<JobData[]>([]);
	let loading = $state(true);
	let error = $state('');
	let statusFilter = $state<'all' | 'live' | 'completed' | 'attention'>('all');
	let serviceFilter = $state('all');
	let search = $state('');
	let limit = $state(PAGE);

	const LIVE = new Set(['running', 'queued', 'scheduled']);

	const statusFilters = [
		{ id: 'all', label: 'All' },
		{ id: 'live', label: 'Live' },
		{ id: 'completed', label: 'Completed' },
		{ id: 'attention', label: 'Needs a look' }
	] as const;

	const services = [
		{ id: 'all', label: 'Any service' },
		{ id: 'plex', label: 'Plex' },
		{ id: 'lastfm', label: 'Last.fm' },
		{ id: 'listenbrainz', label: 'ListenBrainz' }
	];

	const filtered = $derived(
		jobs.filter((j) => {
			if (statusFilter === 'live' && !LIVE.has(j.status)) return false;
			if (statusFilter === 'completed' && j.status !== 'completed') return false;
			if (statusFilter === 'attention' && j.status !== 'partial' && j.status !== 'failed')
				return false;
			if (serviceFilter !== 'all') {
				const svc = j.source_config.service;
				const targets = j.targets_config.map((t) => t.service);
				if (svc !== serviceFilter && !targets.includes(serviceFilter)) return false;
			}
			if (search.trim()) {
				const hay = JSON.stringify([j.source_config, j.targets_config, j.id]).toLowerCase();
				if (!hay.includes(search.trim().toLowerCase())) return false;
			}
			return true;
		})
	);

	/** Runs grouped by the day they were created, newest group first. */
	const groups = $derived.by(() => {
		const out: { key: string; label: string; jobs: JobData[] }[] = [];
		for (const job of filtered.slice(0, limit)) {
			const key = dayKey(job.created_at);
			const last = out[out.length - 1];
			if (last && last.key === key) last.jobs.push(job);
			else out.push({ key, label: dayLabel(job.created_at), jobs: [job] });
		}
		return out;
	});

	async function load() {
		loading = true;
		error = '';
		try {
			const res = await fetch('/api/relay/jobs');
			if (!res.ok) throw new Error('failed');
			jobs = await res.json();
		} catch {
			error = 'Could not load the run history.';
		} finally {
			loading = false;
		}
	}

	async function cancelJob(id: string) {
		const res = await fetch(`/api/relay/jobs/${id}`, { method: 'DELETE' });
		if (!res.ok) return;
		const updated: JobData = await res.json();
		jobs = jobs.map((j) => (j.id === id ? updated : j));
	}

	// Any change of filter or query restarts the list from the first page.
	$effect(() => {
		void [statusFilter, serviceFilter, search];
		limit = PAGE;
	});

	$effect(() => {
		load();
	});

	const counts = $derived({
		total: jobs.length,
		live: jobs.filter((j) => LIVE.has(j.status)).length,
		attention: jobs.filter((j) => j.status === 'partial' || j.status === 'failed').length
	});
</script>

<svelte:head>
	<title>History · RatingRelay</title>
</svelte:head>

<div class="flex h-dvh w-full flex-col overflow-hidden bg-background text-foreground">
	<AppHeader />

	<main class="min-h-0 flex-1 overflow-y-auto">
		<div class="mx-auto w-full max-w-4xl px-8 py-7">
			<div class="flex flex-wrap items-end justify-between gap-3">
				<div>
					<h1 class="text-xl font-semibold tracking-tight">History</h1>
					<p class="mt-1 text-sm text-muted-foreground">
						{counts.total} runs
						{#if counts.live}· {counts.live} live{/if}
						{#if counts.attention}· {counts.attention} need a look{/if}
					</p>
				</div>

				<div class="flex flex-wrap items-center gap-2">
					<div
						class="inline-flex items-center gap-0.5 rounded-md border border-border bg-card p-0.5"
					>
						{#each statusFilters as filter (filter.id)}
							<button
								type="button"
								onclick={() => (statusFilter = filter.id)}
								aria-pressed={statusFilter === filter.id}
								class={cn(
									'rounded px-2.5 py-1 text-sm transition-colors',
									statusFilter === filter.id
										? 'bg-muted text-foreground'
										: 'text-muted-foreground hover:text-foreground'
								)}
							>
								{filter.label}
							</button>
						{/each}
					</div>

					<Select
						bind:value={serviceFilter}
						class="min-w-36 bg-card"
						label="Filter by service"
						options={services.map((svc) => ({ value: svc.id, label: svc.label }))}
					/>

					<Input
						bind:value={search}
						type="search"
						placeholder="Filter runs…"
						aria-label="Filter runs"
						class="h-8 w-44 bg-card"
					/>
				</div>
			</div>

			{#if loading}
				<p class="py-16 text-center text-sm text-muted-foreground">Loading runs…</p>
			{:else if error}
				<p class="py-16 text-center text-sm text-destructive">{error}</p>
			{:else if filtered.length === 0}
				<p class="py-16 text-center text-sm text-muted-foreground">
					{#if jobs.length === 0}
						Nothing has run yet.
					{:else}
						No runs match this filter.
					{/if}
				</p>
			{:else}
				<div class="mt-6 space-y-6">
					{#each groups as group (group.key)}
						<section aria-label={group.label}>
							<div class="flex items-center gap-2 pb-1.5">
								<h2 class="eyebrow">{group.label}</h2>
								<span class="h-px flex-1 bg-border"></span>
								<span class="font-mono text-xs text-muted-foreground tabular-nums">
									{group.jobs.length}
								</span>
							</div>
							<div class="space-y-0.5">
								{#each group.jobs as job (job.id)}
									<JobCard {job} oncancel={cancelJob} />
								{/each}
							</div>
						</section>
					{/each}
				</div>

				{#if filtered.length > limit}
					<button
						type="button"
						onclick={() => (limit += PAGE)}
						class="mt-5 w-full rounded-md border border-border py-2 text-sm text-muted-foreground transition-colors hover:bg-muted/50 hover:text-foreground"
					>
						Show {Math.min(PAGE, filtered.length - limit)} more of {filtered.length - limit}
					</button>
				{/if}
			{/if}
		</div>
	</main>
</div>
