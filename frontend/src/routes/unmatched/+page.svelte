<script lang="ts">
	import { SvelteSet } from 'svelte/reactivity';

	import AppHeader from '$lib/components/AppHeader.svelte';
	import CandidatePicker from '$lib/components/CandidatePicker.svelte';
	import { Button } from '$lib/components/ui/button';
	import { Input } from '$lib/components/ui/input';
	import { Select } from '$lib/components/ui/select';
	import ServiceMark from '$lib/components/flow/ServiceMark.svelte';
	import type { Candidate } from '$lib/types/relay';
	import type { SourceId } from '$lib/types/relay';
	import { cn } from '$lib/utils';

	interface UnmatchedTrack {
		artist: string;
		title: string;
		target_service: string;
		reason: string;
		error: string;
		candidates: Candidate[];
		count: number;
		has_mapping: boolean;
		last_failed: string;
	}

	interface UnrevertableRecord {
		artist: string;
		title: string;
		target_service: string;
		intent: string | null;
		reason: string;
		error: string;
	}

	interface Mapping {
		id: string;
		source_artist: string;
		source_title: string;
		mapped_artist: string;
		mapped_title: string;
		recording_mbid: string | null;
		target_service: string;
		created_at: string;
	}

	/** Rows shown per service group before a "show all" disclosure. */
	const PER_GROUP = 12;

	const SERVICE_LABEL: Record<string, string> = {
		plex: 'Plex',
		lastfm: 'Last.fm',
		listenbrainz: 'ListenBrainz',
		all: 'All services'
	};

	const SERVICE_ORDER = ['listenbrainz', 'lastfm', 'plex', 'all'];

	const REASON_LABEL: Record<string, string> = {
		no_match: 'no match',
		ambiguous_match: 'ambiguous',
		no_recording_mbid: 'no MBID',
		missing_metadata: 'missing title'
	};

	let unmatched = $state<UnmatchedTrack[]>([]);
	let mappings = $state<Mapping[]>([]);
	let unrevertable = $state<UnrevertableRecord[]>([]);
	let loading = $state(true);
	let error = $state('');

	let query = $state('');
	let serviceFilter = $state('all');
	/** Groups whose every row is on screen. */
	const openGroups = new SvelteSet<string>();

	// Per-track form state: keyed by `${artist}||${title}||${target_service}`
	type FormKey = string;
	// SvelteSet is reactive on its own; wrapping it in $state double-tracks it.
	const expanded = new SvelteSet<FormKey>();
	let formState = $state<
		Record<
			FormKey,
			{
				mappedArtist: string;
				mappedTitle: string;
				recordingMbid: string;
				targetService: string;
				saving: boolean;
				saveError: string;
			}
		>
	>({});

	function trackKey(t: UnmatchedTrack): FormKey {
		return `${t.artist}||${t.title}||${t.target_service}`;
	}

	function toggleExpand(t: UnmatchedTrack) {
		const k = trackKey(t);
		if (expanded.has(k)) {
			expanded.delete(k);
		} else {
			expanded.add(k);
			if (!formState[k]) {
				formState = {
					...formState,
					[k]: {
						mappedArtist: t.artist,
						mappedTitle: t.title,
						recordingMbid: '',
						targetService: t.target_service,
						saving: false,
						saveError: ''
					}
				};
			}
		}
	}

	async function load() {
		loading = true;
		error = '';
		try {
			const [ua, um, ur] = await Promise.all([
				fetch('/api/relay/unmatched'),
				fetch('/api/relay/mappings'),
				fetch('/api/relay/unrevertable')
			]);
			if (!ua.ok || !um.ok || !ur.ok) throw new Error('Failed to load');
			unmatched = await ua.json();
			mappings = await um.json();
			unrevertable = await ur.json();
		} catch {
			error = 'Failed to load data.';
		} finally {
			loading = false;
		}
	}

	function pickCandidate(t: UnmatchedTrack, candidate: Candidate) {
		const form = formState[trackKey(t)];
		if (!form) return;
		form.mappedArtist = candidate.artist;
		form.mappedTitle = candidate.title;
		form.recordingMbid = candidate.recording_mbid ?? '';
	}

	async function saveMapping(t: UnmatchedTrack) {
		const k = trackKey(t);
		const form = formState[k];
		if (!form) return;
		form.saving = true;
		form.saveError = '';
		try {
			const res = await fetch('/api/relay/mappings', {
				method: 'POST',
				headers: { 'Content-Type': 'application/json' },
				body: JSON.stringify({
					source_artist: t.artist,
					source_title: t.title,
					mapped_artist: form.mappedArtist,
					mapped_title: form.mappedTitle,
					recording_mbid: form.recordingMbid || null,
					target_service: form.targetService
				})
			});
			if (!res.ok) {
				const err = await res.json().catch(() => ({ detail: 'Save failed' }));
				form.saveError = err.detail ?? 'Save failed';
				return;
			}
			const mapping: Mapping = await res.json();
			mappings = [mapping, ...mappings];
			unmatched = unmatched.map((u) => (trackKey(u) === k ? { ...u, has_mapping: true } : u));
			expanded.delete(k);
		} catch {
			form.saveError = 'Network error';
		} finally {
			form.saving = false;
		}
	}

	async function deleteMapping(id: string) {
		try {
			const res = await fetch(`/api/relay/mappings/${id}`, { method: 'DELETE' });
			if (!res.ok) return;
			mappings = mappings.filter((m) => m.id !== id);
			await load();
		} catch {
			// A failed delete leaves the row on screen; reloading is the repair.
		}
	}

	$effect(() => {
		load();
	});

	// ── Filtering and grouping ────────────────────────────────────────────────
	const pending = $derived(unmatched.filter((u) => !u.has_mapping));
	const resolved = $derived(unmatched.filter((u) => u.has_mapping));

	const counts = $derived.by(() => {
		const out: Record<string, number> = {};
		for (const t of pending) out[t.target_service] = (out[t.target_service] ?? 0) + 1;
		return out;
	});

	const filters = $derived([
		{ id: 'all', label: 'All', count: pending.length },
		...SERVICE_ORDER.filter((svc) => (counts[svc] ?? 0) > 0).map((svc) => ({
			id: svc,
			label: SERVICE_LABEL[svc] ?? svc,
			count: counts[svc] ?? 0
		}))
	]);

	const visibleTracks = $derived.by(() => {
		const needle = query.trim().toLowerCase();
		return pending
			.filter((t) => serviceFilter === 'all' || t.target_service === serviceFilter)
			.filter((t) => !needle || `${t.artist} ${t.title}`.toLowerCase().includes(needle));
	});

	/** One group per target service, biggest first: the queue is a workload. */
	const groups = $derived.by(() => {
		const by: Record<string, UnmatchedTrack[]> = {};
		for (const t of visibleTracks) {
			(by[t.target_service] ??= []).push(t);
		}
		return Object.entries(by)
			.map(([service, rows]) => ({ service, rows }))
			.sort(
				(a, b) =>
					b.rows.length - a.rows.length ||
					SERVICE_ORDER.indexOf(a.service) - SERVICE_ORDER.indexOf(b.service)
			);
	});
</script>

<svelte:head>
	<title>Unmatched · RatingRelay</title>
</svelte:head>

<div class="flex h-dvh w-full flex-col overflow-hidden bg-background text-foreground">
	<AppHeader />

	<main class="min-h-0 flex-1 overflow-y-auto">
		<div class="mx-auto w-full max-w-4xl space-y-4 px-8 py-7">
			<div>
				<h1 class="text-xl font-semibold tracking-tight">Unmatched</h1>
				<p class="mt-1 max-w-2xl text-sm leading-relaxed text-muted-foreground">
					Tracks that could not be matched in a target are held here rather than guessed at. A
					mapping fixes the search for every future run.
				</p>
			</div>

			{#if loading}
				<p class="py-16 text-center text-sm text-muted-foreground">Loading…</p>
			{:else if error}
				<p class="py-16 text-center text-sm text-destructive">{error}</p>
			{:else}
				<!-- ── The queue ────────────────────────────────────────────────── -->
				<div class="flex flex-wrap items-center gap-2">
					<div
						class="inline-flex items-center gap-0.5 rounded-md border border-border bg-card p-0.5"
					>
						{#each filters as f (f.id)}
							<button
								type="button"
								onclick={() => (serviceFilter = f.id)}
								aria-pressed={serviceFilter === f.id}
								class={cn(
									'inline-flex items-center gap-1.5 rounded px-2.5 py-1 text-sm transition-colors',
									serviceFilter === f.id
										? 'bg-muted text-foreground'
										: 'text-muted-foreground hover:text-foreground'
								)}
							>
								{f.label}
								<span class="font-mono text-xs tabular-nums opacity-60">{f.count}</span>
							</button>
						{/each}
					</div>

					<Input
						bind:value={query}
						type="search"
						placeholder="Search artist or title…"
						aria-label="Search unmatched tracks"
						class="h-8 w-64 bg-card"
					/>
				</div>

				{#if pending.length === 0}
					<div class="panel px-4 py-10 text-center">
						<p class="text-sm text-foreground">Nothing is waiting.</p>
						<p class="mt-1 text-sm text-muted-foreground">
							Every track the relay has seen resolved to exactly one recording.
						</p>
					</div>
				{:else if groups.length === 0}
					<p class="panel px-4 py-10 text-center text-sm text-muted-foreground">
						No unmatched tracks match this filter.
					</p>
				{:else}
					{#each groups as group (group.service)}
						{@const isOpen = openGroups.has(group.service)}
						{@const rows = isOpen ? group.rows : group.rows.slice(0, PER_GROUP)}
						<section class="panel overflow-hidden">
							<div class="flex items-center gap-2 border-b border-border px-3 py-2">
								<span class="grid size-4 place-items-center">
									{#if group.service === 'all'}
										<span class="size-2 rounded-full bg-muted-foreground"></span>
									{:else}
										<ServiceMark service={group.service as SourceId} size={14} />
									{/if}
								</span>
								<h2 class="text-sm font-medium text-foreground">
									{SERVICE_LABEL[group.service] ?? group.service}
								</h2>
								<span class="font-mono text-xs text-muted-foreground tabular-nums">
									{group.rows.length}
								</span>
							</div>

							<ul class="divide-y divide-border/60">
								{#each rows as track (trackKey(track))}
									{@const k = trackKey(track)}
									{@const form = formState[k]}
									{@const open = expanded.has(k)}
									<li>
										<button
											type="button"
											onclick={() => toggleExpand(track)}
											aria-expanded={open}
											class="flex w-full items-center gap-2.5 px-3 py-2 text-left transition-colors hover:bg-muted/40"
										>
											<span class="min-w-0 flex-1">
												<span class="block truncate text-sm text-foreground">
													{track.artist}
													<span class="text-muted-foreground">– {track.title}</span>
												</span>
												<span class="mt-0.5 flex flex-wrap items-center gap-x-2 text-xs">
													{#if REASON_LABEL[track.reason]}
														<span class="text-warn">{REASON_LABEL[track.reason]}</span>
													{/if}
													<span class="text-muted-foreground" title={track.last_failed}>
														{track.count}× failed
													</span>
													{#if track.candidates.length > 0}
														<span class="text-muted-foreground">
															· {track.candidates.length} candidates
														</span>
													{/if}
												</span>
											</span>
											<span
												class={cn(
													'shrink-0 text-muted-foreground transition-transform',
													open && 'rotate-90'
												)}
												aria-hidden="true"
											>
												›
											</span>
										</button>

										{#if open && form}
											<div class="space-y-3 border-t border-border/60 bg-background/40 px-3 py-3">
												{#if track.candidates.length > 0}
													<CandidatePicker
														candidates={track.candidates}
														onpick={(candidate) => pickCandidate(track, candidate)}
													/>
												{/if}

												<p class="text-xs text-muted-foreground">
													Override the artist and title to search for in {SERVICE_LABEL[
														track.target_service
													] ?? track.target_service}.
												</p>

												<div class="grid gap-2 sm:grid-cols-2">
													<label class="block">
														<span class="text-xs text-muted-foreground">Search artist</span>
														<input class="control mt-1 w-full" bind:value={form.mappedArtist} />
													</label>
													<label class="block">
														<span class="text-xs text-muted-foreground">Search title</span>
														<input class="control mt-1 w-full" bind:value={form.mappedTitle} />
													</label>
													{#if track.target_service === 'listenbrainz'}
														<label class="block sm:col-span-2">
															<span class="text-xs text-muted-foreground">
																Recording MBID
																<span class="opacity-70">(optional, skips the search)</span>
															</span>
															<input
																class="control mt-1 w-full font-mono"
																bind:value={form.recordingMbid}
																placeholder="3a1f4c2e-…"
															/>
														</label>
													{/if}
													<label class="block">
														<span class="text-xs text-muted-foreground">Apply to</span>
														<Select
															class="mt-1 w-full"
															bind:value={form.targetService}
															label="Apply the mapping to"
															options={[
																{ value: 'all', label: 'All services' },
																{ value: 'plex', label: 'Plex only' },
																{ value: 'lastfm', label: 'Last.fm only' },
																{ value: 'listenbrainz', label: 'ListenBrainz only' }
															]}
														/>
													</label>
												</div>

												{#if form.saveError}
													<p class="text-sm text-destructive">{form.saveError}</p>
												{/if}

												<div class="flex items-center gap-2">
													<Button
														variant="outline"
														class="h-8 px-3 text-sm"
														onclick={() => toggleExpand(track)}
														disabled={form.saving}
													>
														Cancel
													</Button>
													<Button
														class="h-8 px-3 text-sm"
														onclick={() => saveMapping(track)}
														disabled={form.saving}
													>
														{form.saving ? 'Saving…' : 'Save mapping'}
													</Button>
												</div>
											</div>
										{/if}
									</li>
								{/each}
							</ul>

							{#if group.rows.length > PER_GROUP}
								<div class="border-t border-border px-3 py-1.5">
									<button
										type="button"
										onclick={() =>
											isOpen ? openGroups.delete(group.service) : openGroups.add(group.service)}
										class="text-xs text-muted-foreground transition-colors hover:text-foreground"
									>
										{isOpen
											? 'Show fewer'
											: `Show all ${group.rows.length} (${group.rows.length - PER_GROUP} more)`}
									</button>
								</div>
							{/if}
						</section>
					{/each}
				{/if}

				<!-- ── Resolved, mappings, and the writes a reset must leave be ─── -->
				{#if resolved.length > 0}
					<details class="panel group">
						<summary
							class="flex cursor-pointer list-none items-center gap-2 px-3 py-2 text-sm text-foreground"
						>
							<span class="eyebrow">Resolved by a mapping</span>
							<span class="font-mono text-xs text-muted-foreground tabular-nums">
								{resolved.length}
							</span>
							<span class="ml-auto text-xs text-muted-foreground group-open:hidden">show</span>
							<span class="ml-auto hidden text-xs text-muted-foreground group-open:inline"
								>hide</span
							>
						</summary>
						<ul class="divide-y divide-border/60 border-t border-border">
							{#each resolved as track (trackKey(track))}
								<li class="flex items-center gap-2 px-3 py-1.5 text-sm">
									<span class="min-w-0 flex-1 truncate">
										{track.artist}
										<span class="text-muted-foreground">– {track.title}</span>
									</span>
									<span class="shrink-0 text-xs text-ok">mapped</span>
								</li>
							{/each}
						</ul>
					</details>
				{/if}

				<details class="panel group">
					<summary
						class="flex cursor-pointer list-none items-center gap-2 px-3 py-2 text-sm text-foreground"
					>
						<span class="eyebrow">Mappings</span>
						<span class="font-mono text-xs text-muted-foreground tabular-nums">
							{mappings.length}
						</span>
						<span class="ml-auto text-xs text-muted-foreground group-open:hidden">show</span>
						<span class="ml-auto hidden text-xs text-muted-foreground group-open:inline">hide</span>
					</summary>
					{#if mappings.length === 0}
						<p class="border-t border-border px-3 py-6 text-center text-sm text-muted-foreground">
							No mappings defined yet.
						</p>
					{:else}
						<ul class="divide-y divide-border/60 border-t border-border">
							{#each mappings as m (m.id)}
								<li class="flex items-start gap-2 px-3 py-2">
									<div class="min-w-0 flex-1 text-sm">
										<div class="truncate text-muted-foreground">
											{m.source_artist} – {m.source_title}
										</div>
										<div class="truncate text-foreground">
											→ {m.mapped_artist} – {m.mapped_title}
										</div>
										{#if m.recording_mbid}
											<div class="truncate font-mono text-xs text-muted-foreground">
												{m.recording_mbid}
											</div>
										{/if}
									</div>
									<span class="shrink-0 text-xs text-muted-foreground">
										{SERVICE_LABEL[m.target_service] ?? m.target_service}
									</span>
									<button
										type="button"
										onclick={() => deleteMapping(m.id)}
										title="Delete mapping"
										aria-label="Delete mapping"
										class="shrink-0 rounded p-1 text-muted-foreground transition-colors hover:bg-destructive/10 hover:text-destructive"
									>
										<svg
											viewBox="0 0 16 16"
											fill="none"
											stroke="currentColor"
											stroke-width="1.5"
											width="12"
											height="12"
										>
											<path d="M3 4h10M6 4V2h4v2M5 4l.5 9h5L11 4" />
										</svg>
									</button>
								</li>
							{/each}
						</ul>
					{/if}
				</details>

				{#if unrevertable.length > 0}
					<details class="panel group">
						<summary
							class="flex cursor-pointer list-none items-center gap-2 px-3 py-2 text-sm text-foreground"
						>
							<span class="eyebrow text-warn">A reset cannot undo these</span>
							<span class="font-mono text-xs text-warn tabular-nums">{unrevertable.length}</span>
							<span class="ml-auto text-xs text-muted-foreground group-open:hidden">show</span>
							<span class="ml-auto hidden text-xs text-muted-foreground group-open:inline"
								>hide</span
							>
						</summary>
						<div
							class="border-t border-border px-3 py-2 text-xs leading-relaxed text-muted-foreground"
						>
							Written before RatingRelay recorded where it wrote. A reset left these alone rather
							than guess, because clearing the wrong name removes somebody else's rating. Re-syncing
							the track records them properly; names below are as stored (lowercase).
						</div>
						<ul class="divide-y divide-border/60 border-t border-border">
							{#each unrevertable as r (`${r.target_service}::${r.artist}::${r.title}`)}
								<li class="flex items-start gap-2 px-3 py-1.5 text-sm">
									<span class="min-w-0 flex-1 truncate">
										{r.artist}
										<span class="text-muted-foreground">– {r.title}</span>
									</span>
									<span class="shrink-0 text-xs text-muted-foreground">
										{SERVICE_LABEL[r.target_service] ?? r.target_service}
									</span>
								</li>
							{/each}
						</ul>
					</details>
				{/if}
			{/if}
		</div>
	</main>
</div>
