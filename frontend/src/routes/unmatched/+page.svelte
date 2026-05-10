<script lang="ts">
	interface UnmatchedTrack {
		artist: string;
		title: string;
		target_service: string;
		error: string;
		count: number;
		has_mapping: boolean;
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

	let unmatched = $state<UnmatchedTrack[]>([]);
	let mappings = $state<Mapping[]>([]);
	let loading = $state(true);
	let error = $state('');

	// Per-track form state: keyed by `${artist}||${title}||${target_service}`
	type FormKey = string;
	let expanded = $state<Set<FormKey>>(new Set());
	let formState = $state<Record<FormKey, {
		mappedArtist: string;
		mappedTitle: string;
		recordingMbid: string;
		targetService: string;
		saving: boolean;
		saveError: string;
	}>>({});

	function trackKey(t: UnmatchedTrack): FormKey {
		return `${t.artist}||${t.title}||${t.target_service}`;
	}

	function toggleExpand(t: UnmatchedTrack) {
		const k = trackKey(t);
		const next = new Set(expanded);
		if (next.has(k)) {
			next.delete(k);
		} else {
			next.add(k);
			if (!formState[k]) {
				formState = {
					...formState,
					[k]: {
						mappedArtist: t.artist,
						mappedTitle: t.title,
						recordingMbid: '',
						targetService: t.target_service === 'all' ? 'all' : t.target_service,
						saving: false,
						saveError: '',
					},
				};
			}
		}
		expanded = next;
	}

	async function load() {
		loading = true;
		error = '';
		try {
			const [ua, um] = await Promise.all([
				fetch('/api/relay/unmatched'),
				fetch('/api/relay/mappings'),
			]);
			if (!ua.ok || !um.ok) throw new Error('Failed to load');
			unmatched = await ua.json();
			mappings = await um.json();
		} catch {
			error = 'Failed to load data.';
		} finally {
			loading = false;
		}
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
					target_service: form.targetService,
				}),
			});
			if (!res.ok) {
				const err = await res.json().catch(() => ({ detail: 'Save failed' }));
				form.saveError = err.detail ?? 'Save failed';
				return;
			}
			const mapping: Mapping = await res.json();
			mappings = [mapping, ...mappings];
			// Mark the unmatched track as mapped
			unmatched = unmatched.map(u =>
				trackKey(u) === k ? { ...u, has_mapping: true } : u
			);
			// Collapse the form
			const next = new Set(expanded);
			next.delete(k);
			expanded = next;
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
			mappings = mappings.filter(m => m.id !== id);
			// Re-check unmatched tracks
			await load();
		} catch {
			// silently ignore
		}
	}

	$effect(() => { load(); });

	const serviceLabel: Record<string, string> = {
		plex: 'Plex', lastfm: 'Last.fm', listenbrainz: 'ListenBrainz', all: 'All services'
	};
</script>

<svelte:head>
	<title>Unmatched Tracks · RatingRelay</title>
</svelte:head>

<div class="page-root">
	<div class="bg-noise" aria-hidden="true"></div>

	<main class="page-main">

		<div class="top-bar">
			<a href="/" class="back-link">
				<svg viewBox="0 0 16 16" fill="currentColor" width="12" height="12" aria-hidden="true">
					<path d="M10 3L5 8l5 5" stroke="currentColor" stroke-width="1.5" fill="none" stroke-linecap="round" stroke-linejoin="round"/>
				</svg>
				Home
			</a>
		</div>

		<div class="page-heading">
			<h1 class="page-title">Unmatched Tracks</h1>
			<p class="page-subtitle">Tracks that couldn't be matched in a target service. Define mappings to correct the search.</p>
		</div>

		{#if loading}
			<div class="empty-state">Loading…</div>
		{:else if error}
			<div class="error-state">{error}</div>
		{:else}

		<!-- Unmatched section -->
		<div class="section-card">
			<div class="section-label">
				Needs attention
				{#if unmatched.filter(u => !u.has_mapping).length > 0}
					<span class="badge">{unmatched.filter(u => !u.has_mapping).length}</span>
				{/if}
			</div>

			{#if unmatched.filter(u => !u.has_mapping).length === 0}
				<div class="empty-state-inner">No unresolved unmatched tracks. Nice!</div>
			{:else}
				{#each unmatched.filter(u => !u.has_mapping) as track}
					{@const k = trackKey(track)}
					{@const form = formState[k]}
					{@const isOpen = expanded.has(k)}
					<div class="track-row">
						<button class="track-summary" onclick={() => toggleExpand(track)}>
							<span class="track-info">
								<span class="track-name">{track.artist} – {track.title}</span>
								<span class="track-meta">
									<span class="service-chip">{serviceLabel[track.target_service] ?? track.target_service}</span>
									<span class="fail-count">{track.count}× failed</span>
								</span>
							</span>
							<span class="expand-arrow" class:rotated={isOpen} aria-hidden="true">›</span>
						</button>

						{#if isOpen && form}
							<div class="mapping-form">
								<div class="form-hint">Override the artist and title to search for in {serviceLabel[track.target_service] ?? track.target_service}.</div>

								<div class="form-row">
									<label class="form-label" for="ma-{k}">Search artist</label>
									<input id="ma-{k}" class="form-input" bind:value={form.mappedArtist} placeholder={track.artist} />
								</div>
								<div class="form-row">
									<label class="form-label" for="mt-{k}">Search title</label>
									<input id="mt-{k}" class="form-input" bind:value={form.mappedTitle} placeholder={track.title} />
								</div>

								{#if track.target_service === 'listenbrainz'}
									<div class="form-row">
										<label class="form-label" for="mbid-{k}">Recording MBID <span class="optional">(optional, skips search)</span></label>
										<input id="mbid-{k}" class="form-input" bind:value={form.recordingMbid} placeholder="e.g. 3a1f4c2e-…" />
									</div>
								{/if}

								<div class="form-row">
									<label class="form-label" for="svc-{k}">Apply to</label>
									<select id="svc-{k}" class="form-select" bind:value={form.targetService}>
										<option value="all">All services</option>
										<option value="plex">Plex only</option>
										<option value="lastfm">Last.fm only</option>
										<option value="listenbrainz">ListenBrainz only</option>
									</select>
								</div>

								{#if form.saveError}
									<div class="form-error">{form.saveError}</div>
								{/if}

								<div class="form-actions">
									<button class="btn-secondary" onclick={() => toggleExpand(track)} disabled={form.saving}>
										Cancel
									</button>
									<button class="btn-primary" onclick={() => saveMapping(track)} disabled={form.saving}>
										{form.saving ? 'Saving…' : 'Save mapping'}
									</button>
								</div>
							</div>
						{/if}
					</div>
				{/each}
			{/if}
		</div>

		<!-- Already-mapped section -->
		{#if unmatched.filter(u => u.has_mapping).length > 0}
			<div class="section-card resolved-section">
				<div class="section-label">Resolved (mapping exists)</div>
				{#each unmatched.filter(u => u.has_mapping) as track}
					<div class="track-row track-row-resolved">
						<span class="track-info">
							<span class="track-name resolved-name">{track.artist} – {track.title}</span>
							<span class="track-meta">
								<span class="service-chip">{serviceLabel[track.target_service] ?? track.target_service}</span>
							</span>
						</span>
						<span class="resolved-badge">mapped ✓</span>
					</div>
				{/each}
			</div>
		{/if}

		<!-- Mappings section -->
		<div class="section-card">
			<div class="section-label">
				All mappings
				{#if mappings.length > 0}
					<span class="badge badge-muted">{mappings.length}</span>
				{/if}
			</div>

			{#if mappings.length === 0}
				<div class="empty-state-inner">No mappings defined yet.</div>
			{:else}
				{#each mappings as m}
					<div class="mapping-row">
						<div class="mapping-info">
							<div class="mapping-original">{m.source_artist} – {m.source_title}</div>
							<div class="mapping-arrow">↓</div>
							<div class="mapping-override">
								{m.mapped_artist} – {m.mapped_title}
								{#if m.recording_mbid}
									<span class="mbid-hint">mbid: {m.recording_mbid}</span>
								{/if}
							</div>
						</div>
						<div class="mapping-right">
							<span class="service-chip">{serviceLabel[m.target_service] ?? m.target_service}</span>
							<button class="delete-btn" onclick={() => deleteMapping(m.id)} title="Delete mapping">
								<svg viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.5" width="13" height="13">
									<path d="M3 4h10M6 4V2h4v2M5 4l.5 9h5L11 4"/>
								</svg>
							</button>
						</div>
					</div>
				{/each}
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
		max-width: 680px;
		display: flex;
		flex-direction: column;
		gap: 0.75rem;
	}

	.top-bar {
		display: flex;
		align-items: center;
		margin-bottom: 0.25rem;
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
	.back-link:hover { color: oklch(0.7 0.02 40); }

	.page-heading { margin-bottom: 0.5rem; }

	.page-title {
		font-size: 1.125rem;
		font-family: var(--font-heading);
		font-weight: 700;
		color: oklch(0.82 0.015 50);
		margin: 0 0 0.25rem;
		letter-spacing: -0.02em;
	}

	.page-subtitle {
		font-size: 0.8rem;
		color: oklch(0.42 0.01 40);
		margin: 0;
	}

	.empty-state, .error-state {
		padding: 3rem 0;
		text-align: center;
		font-size: 0.875rem;
		color: oklch(0.4 0.01 40);
	}
	.error-state { color: oklch(0.6 0.18 22); }

	/* ── Section cards ────────────────────────────────────────────────────── */
	.section-card {
		background: oklch(0.16 0.015 38);
		border: 1px solid oklch(0.22 0.02 38);
	}

	.section-label {
		display: flex;
		align-items: center;
		gap: 0.5rem;
		font-size: 0.6rem;
		letter-spacing: 0.12em;
		text-transform: uppercase;
		color: oklch(0.38 0.01 40);
		padding: 0.625rem 1rem;
		font-family: var(--font-heading);
		font-weight: 700;
		border-bottom: 1px solid oklch(0.2 0.01 40);
	}

	.badge {
		font-size: 0.6875rem;
		font-variant-numeric: tabular-nums;
		background: oklch(0.55 0.16 38 / 0.18);
		color: oklch(0.65 0.12 38);
		padding: 0 0.35rem;
		letter-spacing: 0;
		text-transform: none;
		font-weight: 600;
	}
	.badge-muted {
		background: oklch(0.25 0.01 40);
		color: oklch(0.48 0.01 40);
	}

	.empty-state-inner {
		padding: 1.5rem 1rem;
		font-size: 0.8125rem;
		color: oklch(0.38 0.01 40);
		text-align: center;
	}

	/* ── Track rows ───────────────────────────────────────────────────────── */
	.track-row {
		border-bottom: 1px solid oklch(0.185 0.01 40);
	}
	.track-row:last-child { border-bottom: none; }

	.track-summary {
		display: flex;
		align-items: center;
		justify-content: space-between;
		width: 100%;
		padding: 0.6rem 1rem;
		background: none;
		border: none;
		cursor: pointer;
		color: inherit;
		text-align: left;
		gap: 0.75rem;
		transition: background 0.1s;
	}
	.track-summary:hover { background: oklch(0.18 0.015 38); }

	.track-row-resolved .track-summary,
	.track-row-resolved {
		display: flex;
		align-items: center;
		justify-content: space-between;
		padding: 0.5rem 1rem;
	}

	.track-info {
		display: flex;
		flex-direction: column;
		gap: 0.2rem;
		min-width: 0;
	}

	.track-name {
		font-size: 0.8125rem;
		color: oklch(0.72 0.015 50);
		white-space: nowrap;
		overflow: hidden;
		text-overflow: ellipsis;
	}
	.resolved-name { color: oklch(0.48 0.01 40); }

	.track-meta {
		display: flex;
		align-items: center;
		gap: 0.5rem;
	}

	.service-chip {
		font-size: 0.6rem;
		letter-spacing: 0.06em;
		text-transform: uppercase;
		font-family: var(--font-heading);
		font-weight: 600;
		color: oklch(0.48 0.015 240);
		border: 1px solid oklch(0.3 0.01 240 / 0.5);
		padding: 0.1rem 0.35rem;
	}

	.fail-count {
		font-size: 0.7rem;
		color: oklch(0.42 0.01 40);
	}

	.expand-arrow {
		font-size: 1rem;
		color: oklch(0.35 0.01 40);
		flex-shrink: 0;
		transition: transform 0.15s;
		line-height: 1;
	}
	.expand-arrow.rotated { transform: rotate(90deg); }

	.resolved-badge {
		font-size: 0.65rem;
		color: oklch(0.58 0.14 140);
		flex-shrink: 0;
	}

	/* ── Mapping form ─────────────────────────────────────────────────────── */
	.mapping-form {
		padding: 0.75rem 1rem 1rem;
		background: oklch(0.14 0.012 38);
		border-top: 1px solid oklch(0.2 0.01 40);
		display: flex;
		flex-direction: column;
		gap: 0.6rem;
	}

	.form-hint {
		font-size: 0.75rem;
		color: oklch(0.42 0.01 40);
	}

	.form-row {
		display: flex;
		flex-direction: column;
		gap: 0.2rem;
	}

	.form-label {
		font-size: 0.65rem;
		letter-spacing: 0.07em;
		text-transform: uppercase;
		font-family: var(--font-heading);
		font-weight: 600;
		color: oklch(0.42 0.01 40);
	}

	.optional {
		font-size: 0.6rem;
		letter-spacing: 0;
		text-transform: none;
		font-weight: 400;
		color: oklch(0.38 0.01 40);
	}

	.form-input, .form-select {
		background: oklch(0.18 0.015 38);
		border: 1px solid oklch(0.26 0.02 38);
		color: oklch(0.78 0.015 50);
		font-size: 0.8125rem;
		padding: 0.4rem 0.6rem;
		font-family: inherit;
		width: 100%;
		outline: none;
		transition: border-color 0.15s;
	}
	.form-input:focus, .form-select:focus {
		border-color: oklch(0.55 0.16 38 / 0.6);
	}
	.form-select { cursor: pointer; }

	.form-error {
		font-size: 0.75rem;
		color: oklch(0.65 0.18 22);
	}

	.form-actions {
		display: flex;
		gap: 0.5rem;
		justify-content: flex-end;
		margin-top: 0.25rem;
	}

	.btn-primary {
		padding: 0.375rem 0.875rem;
		font-size: 0.75rem;
		font-family: var(--font-heading);
		font-weight: 600;
		background: oklch(0.55 0.16 38 / 0.2);
		border: 1px solid oklch(0.55 0.16 38 / 0.5);
		color: oklch(0.78 0.12 38);
		cursor: pointer;
		transition: background 0.15s, color 0.15s;
	}
	.btn-primary:hover:not(:disabled) {
		background: oklch(0.55 0.16 38 / 0.3);
		color: oklch(0.88 0.12 38);
	}
	.btn-primary:disabled { opacity: 0.45; cursor: not-allowed; }

	.btn-secondary {
		padding: 0.375rem 0.75rem;
		font-size: 0.75rem;
		font-family: var(--font-heading);
		font-weight: 500;
		background: transparent;
		border: 1px solid oklch(0.26 0.01 40);
		color: oklch(0.45 0.01 40);
		cursor: pointer;
		transition: border-color 0.15s, color 0.15s;
	}
	.btn-secondary:hover:not(:disabled) {
		border-color: oklch(0.38 0.01 40);
		color: oklch(0.6 0.01 40);
	}
	.btn-secondary:disabled { opacity: 0.45; cursor: not-allowed; }

	/* ── Mapping rows ─────────────────────────────────────────────────────── */
	.mapping-row {
		display: flex;
		align-items: center;
		justify-content: space-between;
		gap: 1rem;
		padding: 0.625rem 1rem;
		border-bottom: 1px solid oklch(0.185 0.01 40);
	}
	.mapping-row:last-child { border-bottom: none; }

	.mapping-info {
		display: flex;
		flex-direction: column;
		gap: 0.1rem;
		min-width: 0;
	}

	.mapping-original {
		font-size: 0.8rem;
		color: oklch(0.52 0.01 40);
		white-space: nowrap;
		overflow: hidden;
		text-overflow: ellipsis;
	}

	.mapping-arrow {
		font-size: 0.7rem;
		color: oklch(0.35 0.01 40);
		line-height: 1;
	}

	.mapping-override {
		font-size: 0.8rem;
		color: oklch(0.72 0.015 50);
		display: flex;
		align-items: center;
		gap: 0.5rem;
		flex-wrap: wrap;
	}

	.mbid-hint {
		font-size: 0.65rem;
		color: oklch(0.42 0.01 40);
		font-family: monospace;
	}

	.mapping-right {
		display: flex;
		align-items: center;
		gap: 0.5rem;
		flex-shrink: 0;
	}

	.delete-btn {
		background: none;
		border: none;
		cursor: pointer;
		color: oklch(0.4 0.01 40);
		padding: 0.25rem;
		display: flex;
		align-items: center;
		transition: color 0.15s;
	}
	.delete-btn:hover { color: oklch(0.65 0.18 22); }

	.resolved-section { opacity: 0.7; }
</style>
