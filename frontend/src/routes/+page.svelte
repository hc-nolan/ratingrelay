<script lang="ts">
	import { untrack } from 'svelte';
	import type { PageData } from './$types';
	import type { PlexAuthState, LastFMAuthState, ListenBrainzAuthState, PlexServerItem, SetupStep } from '$lib/types/auth';

	let { data }: { data: PageData } = $props();

	// ── Service state ─────────────────────────────────────────────────────────
	let plex = $state<PlexAuthState>(untrack(() => ({ ...data.authStatus.plex })));
	let lastfm = $state<LastFMAuthState>(untrack(() => ({ ...data.authStatus.lastfm })));
	let listenbrainz = $state<ListenBrainzAuthState>(untrack(() => ({ ...data.authStatus.listenbrainz })));

	// ── Wizard step (derived) ─────────────────────────────────────────────────
	let step = $derived.by<SetupStep>(() => {
		if (plex.status !== 'connected') return 'plex';
		if (!plex.server_name) return 'server-select';
		const musicOk =
			lastfm.status === 'connected' ||
			listenbrainz.status === 'connected' ||
			lastfm.status === 'disabled' ||
			listenbrainz.status === 'disabled';
		if (!musicOk) return 'music-services';
		return 'done';
	});

	// ── Plex OAuth popup flow ─────────────────────────────────────────────────
	let plexPopup: Window | null = null;

	async function startPlexAuth() {
		plex.status = 'checking';
		try {
			const res = await fetch('/api/plex/auth/init', { method: 'POST' });
			if (!res.ok) throw new Error(await res.text());
			const { oauth_url }: { pin_id: number; oauth_url: string } = await res.json();

			const w = 800, h = 700;
			const left = Math.round(window.screenX + (window.outerWidth - w) / 2);
			const top = Math.round(window.screenY + (window.outerHeight - h) / 2);
			plexPopup = window.open(
				oauth_url,
				'plex-oauth',
				`width=${w},height=${h},left=${left},top=${top},toolbar=0,menubar=0`
			);

			plex.status = 'needs-auth';
		} catch {
			plex.status = 'error';
		}
	}

	function handleMessage(event: MessageEvent) {
		if (event.origin !== window.location.origin) return;
		if (event.data !== 'plex:authed') return;
		plexPopup?.close();
		plexPopup = null;
		refreshPlexStatus();
	}

	async function refreshPlexStatus() {
		plex.status = 'checking';
		try {
			const res = await fetch('/api/plex/auth/status');
			if (!res.ok) { plex.status = 'needs-auth'; return; }
			const body: { status: string; username?: string; server_name?: string; server_url?: string } =
				await res.json();
			plex.status = body.status as PlexAuthState['status'];
			plex.username = body.username;
			plex.server_name = body.server_name;
			plex.server_url = body.server_url;
			if (body.status === 'connected') {
				availableServers = [];
				selectedServer = '';
			}
		} catch {
			plex.status = 'error';
		}
	}

	$effect(() => {
		window.addEventListener('message', handleMessage);
		return () => window.removeEventListener('message', handleMessage);
	});

	// ── Server selection ──────────────────────────────────────────────────────
	let availableServers = $state<PlexServerItem[]>([]);
	let selectedServer = $state('');
	let loadingServers = $state(false);
	let serverError = $state('');

	async function loadServers() {
		loadingServers = true;
		serverError = '';
		try {
			const res = await fetch('/api/plex/servers');
			if (!res.ok) throw new Error(await res.text());
			const body: { servers: PlexServerItem[] } = await res.json();
			availableServers = body.servers;
			if (availableServers.length === 1) selectedServer = availableServers[0].name;
		} catch (e) {
			serverError = e instanceof Error ? e.message : 'Failed to load servers';
		} finally {
			loadingServers = false;
		}
	}

	$effect(() => {
		if (step === 'server-select' && availableServers.length === 0) {
			loadServers();
		}
	});

	async function confirmServer() {
		if (!selectedServer) return;
		loadingServers = true;
		serverError = '';
		try {
			const res = await fetch('/api/plex/server', {
				method: 'POST',
				headers: { 'Content-Type': 'application/json' },
				body: JSON.stringify({ name: selectedServer }),
			});
			if (!res.ok) throw new Error(await res.text());
			const body: { status: string; username?: string; server_name?: string; server_url?: string } =
				await res.json();
			plex.server_name = body.server_name;
			plex.server_url = body.server_url;
		} catch (e) {
			serverError = e instanceof Error ? e.message : 'Failed to connect to server';
		} finally {
			loadingServers = false;
		}
	}

	// ── LastFM credentials form ───────────────────────────────────────────────
	let lastfmForm = $state({ api_key: '', api_secret: '', username: '', password: '' });
	let lastfmError = $state('');

	async function submitLastFMAuth() {
		if (!lastfmForm.api_key || !lastfmForm.api_secret || !lastfmForm.username || !lastfmForm.password) return;
		lastfm.status = 'checking';
		lastfmError = '';
		try {
			const res = await fetch('/api/lastfm/auth', {
				method: 'POST',
				headers: { 'Content-Type': 'application/json' },
				body: JSON.stringify(lastfmForm),
			});
			if (!res.ok) {
				const err = await res.json().catch(() => ({ detail: 'Unknown error' }));
				lastfmError = err.detail ?? 'Authentication failed';
				lastfm.status = 'error';
				return;
			}
			const body: { status: string; username?: string } = await res.json();
			lastfm.status = body.status as LastFMAuthState['status'];
			lastfm.username = body.username;
		} catch {
			lastfm.status = 'error';
			lastfmError = 'Network error';
		}
	}

	// ── ListenBrainz token flow ───────────────────────────────────────────────
	let lbForm = $state({ username: '', token: '' });
	let lbError = $state('');

	async function submitListenBrainzToken() {
		if (!lbForm.username.trim() || !lbForm.token.trim()) return;
		listenbrainz.status = 'checking';
		lbError = '';
		try {
			const res = await fetch('/api/listenbrainz/auth', {
				method: 'POST',
				headers: { 'Content-Type': 'application/json' },
				body: JSON.stringify(lbForm),
			});
			if (!res.ok) {
				const err = await res.json().catch(() => ({ detail: 'Unknown error' }));
				lbError = err.detail ?? 'Authentication failed';
				listenbrainz.status = 'error';
				return;
			}
			const body: { status: string; username?: string } = await res.json();
			listenbrainz.status = body.status as ListenBrainzAuthState['status'];
			listenbrainz.username = body.username;
		} catch {
			listenbrainz.status = 'error';
			lbError = 'Network error';
		}
	}

	// ── Helpers ───────────────────────────────────────────────────────────────
	const statusLabel: Record<string, string> = {
		checking: 'Checking…',
		connected: 'Connected',
		'needs-auth': 'Not connected',
		disabled: 'Disabled',
		error: 'Error',
	};

	// ── Relay home state ──────────────────────────────────────────────────────

	type ServiceId = 'plex' | 'lastfm' | 'listenbrainz';

	let selectedSource = $state<ServiceId>('plex');

	let plexSourceConfig = $state({ ratingThreshold: 8, comparison: 'gte' as 'gte' | 'lte' });
	let lbzSourceConfig = $state({ feedbackType: 'love' as 'love' | 'hate' });

	let plexTargetEnabled = $state(false);
	let plexTargetConfig = $state({ rating: 10 });
	let lastfmTargetEnabled = $state(false);
	let lbzTargetEnabled = $state(false);
	let lbzTargetConfig = $state({ feedbackType: 'love' as 'love' | 'hate' });

	let relaying = $state(false);
	let relayResult = $state<{
		status: string;
		tracks_fetched: number;
		results: { service: string; processed: number; errors: number }[];
	} | null>(null);
	let relayError = $state('');

	let canRelay = $derived(
		!relaying && (plexTargetEnabled || lastfmTargetEnabled || lbzTargetEnabled)
	);

	const plexStarOptions = [
		{ value: 1,  label: '½★ 0.5 stars' },
		{ value: 2,  label: '★ 1 star' },
		{ value: 3,  label: '★½ 1.5 stars' },
		{ value: 4,  label: '★★ 2 stars' },
		{ value: 5,  label: '★★½ 2.5 stars' },
		{ value: 6,  label: '★★★ 3 stars' },
		{ value: 7,  label: '★★★½ 3.5 stars' },
		{ value: 8,  label: '★★★★ 4 stars' },
		{ value: 9,  label: '★★★★½ 4.5 stars' },
		{ value: 10, label: '★★★★★ 5 stars' },
	];

	function buildRelayPayload() {
		let source: object;
		if (selectedSource === 'plex') {
			source = {
				service: 'plex',
				rating_threshold: plexSourceConfig.ratingThreshold,
				comparison: plexSourceConfig.comparison,
			};
		} else if (selectedSource === 'lastfm') {
			source = { service: 'lastfm' };
		} else {
			source = { service: 'listenbrainz', feedback_type: lbzSourceConfig.feedbackType };
		}

		const targets: object[] = [];
		if (plexTargetEnabled && plex.status === 'connected' && plex.server_name) {
			targets.push({ service: 'plex', rating: plexTargetConfig.rating });
		}
		if (lastfmTargetEnabled && lastfm.status === 'connected') {
			targets.push({ service: 'lastfm' });
		}
		if (lbzTargetEnabled && listenbrainz.status === 'connected') {
			targets.push({ service: 'listenbrainz', feedback_type: lbzTargetConfig.feedbackType });
		}

		return { source, targets };
	}

	async function runRelay() {
		if (!canRelay) return;
		relaying = true;
		relayResult = null;
		relayError = '';
		try {
			const res = await fetch('/api/relay/run', {
				method: 'POST',
				headers: { 'Content-Type': 'application/json' },
				body: JSON.stringify(buildRelayPayload()),
			});
			if (!res.ok) {
				const err = await res.json().catch(() => ({ detail: 'Relay failed' }));
				relayError = err.detail ?? 'Relay failed';
				return;
			}
			relayResult = await res.json();
		} catch {
			relayError = 'Network error';
		} finally {
			relaying = false;
		}
	}

	const serviceLabels: Record<ServiceId, string> = {
		plex: 'Plex',
		lastfm: 'Last.fm',
		listenbrainz: 'ListenBrainz',
	};

	// ── Job queue state ───────────────────────────────────────────────────────

	let recurring = $state(false);
	let intervalMinutes = $state(1440);

	const frequencyOptions = [
		{ value: 60, label: 'Every hour' },
		{ value: 360, label: 'Every 6 hours' },
		{ value: 720, label: 'Every 12 hours' },
		{ value: 1440, label: 'Daily' },
		{ value: 10080, label: 'Weekly' },
	];

	interface JobData {
		id: string;
		status: string;
		source_config: Record<string, unknown>;
		targets_config: Record<string, unknown>[];
		recurring: boolean;
		interval_minutes?: number;
		run_at?: string;
		created_at: string;
		started_at?: string;
		completed_at?: string;
		tracks_fetched: number;
		tracks_ok: number;
		tracks_err: number;
	}

	let jobs = $state<JobData[]>([]);

	let canQueue = $derived(
		!relaying && (plexTargetEnabled || lastfmTargetEnabled || lbzTargetEnabled)
	);

	async function queueJob() {
		if (!canQueue) return;
		relaying = true;
		relayResult = null;
		relayError = '';
		try {
			const payload = {
				...buildRelayPayload(),
				recurring,
				interval_minutes: recurring ? intervalMinutes : undefined,
			};
			const res = await fetch('/api/relay/jobs', {
				method: 'POST',
				headers: { 'Content-Type': 'application/json' },
				body: JSON.stringify(payload),
			});
			if (!res.ok) {
				if (res.status === 409) {
					const err = await res.json().catch(() => ({ detail: 'Duplicate job' }));
					relayError = err.detail ?? 'An equivalent job is already active';
					return;
				}
				const err = await res.json().catch(() => ({ detail: 'Queue failed' }));
				relayError = err.detail ?? 'Failed to queue job';
				return;
			}
			const job: JobData = await res.json();
			jobs = [job, ...jobs];
		} catch {
			relayError = 'Network error';
		} finally {
			relaying = false;
		}
	}

	async function loadJobs() {
		try {
			const res = await fetch('/api/relay/jobs');
			if (!res.ok) return;
			jobs = await res.json();
		} catch {
			// silently ignore
		}
	}

	async function cancelJob(jobId: string) {
		try {
			const res = await fetch(`/api/relay/jobs/${jobId}`, { method: 'DELETE' });
			if (!res.ok) return;
			const updated: JobData = await res.json();
			jobs = jobs.map(j => j.id === jobId ? { ...j, ...updated } : j);
		} catch {
			// silently ignore
		}
	}

	// ── Helper functions ──────────────────────────────────────────────────────

	function summarizeSource(cfg: Record<string, unknown>): string {
		const svc = cfg.service as string;
		if (svc === 'plex') {
			const cmp = cfg.comparison === 'gte' ? '≥' : '≤';
			const rating = (cfg.rating_threshold as number) / 2;
			return `Plex ${cmp} ${rating}★`;
		}
		if (svc === 'lastfm') return 'Last.fm loved';
		if (svc === 'listenbrainz') {
			const fb = cfg.feedback_type as string;
			return `ListenBrainz ${fb}d`;
		}
		return svc;
	}

	function summarizeTargets(cfgs: Record<string, unknown>[]): string {
		return cfgs
			.map(c => {
				const s = c.service as string;
				if (s === 'plex') return 'Plex';
				if (s === 'lastfm') return 'Last.fm';
				if (s === 'listenbrainz') return 'ListenBrainz';
				return s;
			})
			.join(' + ');
	}

	function formatNextRun(runAt: string): string {
		const diff = new Date(runAt).getTime() - Date.now();
		if (diff <= 0) return 'soon';
		const h = Math.floor(diff / 3600000);
		const m = Math.floor((diff % 3600000) / 60000);
		if (h > 0) return `in ${h}h ${m}m`;
		return `in ${m}m`;
	}

	function statusColor(status: string): string {
		const map: Record<string, string> = {
			queued: 'status-queued',
			scheduled: 'status-scheduled',
			running: 'status-running',
			completed: 'status-completed',
			partial: 'status-partial',
			failed: 'status-failed',
			cancelled: 'status-cancelled',
		};
		return map[status] ?? 'status-queued';
	}

	// ── Mount: load jobs and poll for status updates ──────────────────────────
	$effect(() => {
		loadJobs();
		const poll = setInterval(loadJobs, 5000);
		return () => clearInterval(poll);
	});
</script>

<svelte:head>
	<title>RatingRelay</title>
</svelte:head>

{#if step === 'done'}

<!-- ═══════════════════════════════════════════════════════════════════════ -->
<!-- Relay Home                                                              -->
<!-- ═══════════════════════════════════════════════════════════════════════ -->

<div class="setup-root">
	<div class="bg-noise" aria-hidden="true"></div>

	<main class="relay-main">

		<!-- Header -->
		<header class="setup-header">
			<div class="logo-mark" aria-hidden="true">
				<span class="logo-signal"></span>
				<span class="logo-signal"></span>
				<span class="logo-signal"></span>
			</div>
			<div style="flex:1">
				<div class="title-row">
					<h1 class="setup-title">RatingRelay</h1>
					<a href="/unmatched" class="nav-link">Unmatched tracks</a>
				</div>
				<div class="conn-pills">
					{#if plex.status === 'connected'}
						<span class="conn-pill">
							<svg viewBox="0 0 512 512" width="10" height="10"><path d="M256 70H148l108 186-108 186h108l108-186z" fill="currentColor"/></svg>
							{plex.server_name}
						</span>
					{/if}
					{#if lastfm.status === 'connected'}
						<span class="conn-pill lastfm-pill">Last.fm · {lastfm.username}</span>
					{/if}
					{#if listenbrainz.status === 'connected'}
						<span class="conn-pill lb-pill">ListenBrainz · {listenbrainz.username}</span>
					{/if}
				</div>
			</div>
		</header>

		<!-- ── FROM section ───────────────────────────────────────────────── -->
		<section class="relay-card">
			<div class="relay-card-label">FROM</div>

			<!-- Source service tabs -->
			<div class="source-tabs">
				{#if plex.status === 'connected' && plex.server_name}
					<button
						class="source-tab"
						class:tab-active={selectedSource === 'plex'}
						onclick={() => selectedSource = 'plex'}
					>
						<svg viewBox="0 0 512 512" width="14" height="14" aria-hidden="true"><rect width="512" height="512" rx="15%" fill="#282a2d"/><path d="M256 70H148l108 186-108 186h108l108-186z" fill="#e5a00d"/></svg>
						Plex
					</button>
				{/if}
				{#if lastfm.status === 'connected'}
					<button
						class="source-tab"
						class:tab-active={selectedSource === 'lastfm'}
						onclick={() => selectedSource = 'lastfm'}
					>
						<svg viewBox="0 0 24 24" fill="currentColor" width="14" height="14" aria-hidden="true"><path d="M10.584 17.21l-.88-2.392s-1.43 1.6-3.573 1.6c-1.898 0-3.244-1.65-3.244-4.29 0-3.38 1.703-4.594 3.38-4.594 2.42 0 3.19 1.565 3.85 3.576l.88 2.75c.88 2.673 2.53 4.815 7.315 4.815 3.41 0 5.73-1.045 5.73-3.8 0-2.227-1.265-3.38-3.63-3.93l-1.76-.385c-1.21-.275-1.57-.77-1.57-1.593 0-.935.737-1.483 1.95-1.483 1.318 0 2.03.494 2.14 1.67l2.75-.33c-.22-2.47-1.925-3.48-4.755-3.48-2.49 0-4.82.935-4.82 3.93 0 1.87.907 3.05 3.19 3.6l1.87.44c1.375.33 1.87.88 1.87 1.76 0 1.046-.99 1.483-2.862 1.483-2.75 0-3.9-1.43-4.562-3.38l-.91-2.75c-1.155-3.52-3-4.87-6.655-4.87C1.87 5.528 0 8.278 0 12.238c0 3.82 1.87 6.234 6.04 6.234 3.135 0 4.544-1.262 4.544-1.262z"/></svg>
						Last.fm
					</button>
				{/if}
				{#if listenbrainz.status === 'connected'}
					<button
						class="source-tab"
						class:tab-active={selectedSource === 'listenbrainz'}
						onclick={() => selectedSource = 'listenbrainz'}
					>
						<svg viewBox="9,0,128,160" width="14" height="14" aria-hidden="true"><path d="m75.354 7.823v144l61-35v-74z" fill="#eb743b"/><path d="m70.354 7.823-61 35v74l61 35z" fill="#353070"/></svg>
						ListenBrainz
					</button>
				{/if}
			</div>

			<!-- Source config -->
			<div class="source-config">
				{#if selectedSource === 'plex'}
					<span class="config-label">Fetch tracks rated</span>
					<select class="relay-select" bind:value={plexSourceConfig.comparison}>
						<option value="gte">at least</option>
						<option value="lte">at most</option>
					</select>
					<select class="relay-select" bind:value={plexSourceConfig.ratingThreshold}>
						{#each plexStarOptions as opt}
							<option value={opt.value}>{opt.label}</option>
						{/each}
					</select>
				{:else if selectedSource === 'lastfm'}
					<span class="config-label">Fetch all loved tracks</span>
				{:else if selectedSource === 'listenbrainz'}
					<span class="config-label">Fetch</span>
					<div class="toggle-group">
						<button
							class="toggle-btn"
							class:toggle-active={lbzSourceConfig.feedbackType === 'love'}
							onclick={() => lbzSourceConfig.feedbackType = 'love'}
						>Loved</button>
						<button
							class="toggle-btn"
							class:toggle-active={lbzSourceConfig.feedbackType === 'hate'}
							onclick={() => lbzSourceConfig.feedbackType = 'hate'}
						>Hated</button>
					</div>
					<span class="config-label">recordings</span>
				{/if}
			</div>
		</section>

		<!-- ── TO section ─────────────────────────────────────────────────── -->
		<section class="relay-card">
			<div class="relay-card-label">TO</div>

			<div class="targets-list">

				<!-- Plex target -->
				{#if plex.status === 'connected' && plex.server_name && selectedSource !== 'plex'}
					<div class="target-row" class:target-enabled={plexTargetEnabled}>
						<label class="target-check">
							<input type="checkbox" bind:checked={plexTargetEnabled} />
							<svg viewBox="0 0 512 512" width="14" height="14" aria-hidden="true"><rect width="512" height="512" rx="15%" fill="#282a2d"/><path d="M256 70H148l108 186-108 186h108l108-186z" fill="#e5a00d"/></svg>
							<span>Plex</span>
						</label>
						{#if plexTargetEnabled}
							<div class="target-config">
								<span class="config-label">Rate as</span>
								<select class="relay-select" bind:value={plexTargetConfig.rating}>
									{#each plexStarOptions as opt}
										<option value={opt.value}>{opt.label}</option>
									{/each}
								</select>
							</div>
						{:else}
							<span class="target-hint">Rate tracks in your Plex library</span>
						{/if}
					</div>
				{/if}

				<!-- Last.fm target -->
				{#if lastfm.status === 'connected' && selectedSource !== 'lastfm'}
					<div class="target-row" class:target-enabled={lastfmTargetEnabled}>
						<label class="target-check">
							<input type="checkbox" bind:checked={lastfmTargetEnabled} />
							<svg viewBox="0 0 24 24" fill="currentColor" width="14" height="14" aria-hidden="true"><path d="M10.584 17.21l-.88-2.392s-1.43 1.6-3.573 1.6c-1.898 0-3.244-1.65-3.244-4.29 0-3.38 1.703-4.594 3.38-4.594 2.42 0 3.19 1.565 3.85 3.576l.88 2.75c.88 2.673 2.53 4.815 7.315 4.815 3.41 0 5.73-1.045 5.73-3.8 0-2.227-1.265-3.38-3.63-3.93l-1.76-.385c-1.21-.275-1.57-.77-1.57-1.593 0-.935.737-1.483 1.95-1.483 1.318 0 2.03.494 2.14 1.67l2.75-.33c-.22-2.47-1.925-3.48-4.755-3.48-2.49 0-4.82.935-4.82 3.93 0 1.87.907 3.05 3.19 3.6l1.87.44c1.375.33 1.87.88 1.87 1.76 0 1.046-.99 1.483-2.862 1.483-2.75 0-3.9-1.43-4.562-3.38l-.91-2.75c-1.155-3.52-3-4.87-6.655-4.87C1.87 5.528 0 8.278 0 12.238c0 3.82 1.87 6.234 6.04 6.234 3.135 0 4.544-1.262 4.544-1.262z"/></svg>
							<span>Last.fm</span>
						</label>
						{#if lastfmTargetEnabled}
							<div class="target-config">
								<span class="config-label fixed-action">Love tracks</span>
							</div>
						{:else}
							<span class="target-hint">Love tracks on Last.fm</span>
						{/if}
					</div>
				{/if}

				<!-- ListenBrainz target -->
				{#if listenbrainz.status === 'connected' && selectedSource !== 'listenbrainz'}
					<div class="target-row" class:target-enabled={lbzTargetEnabled}>
						<label class="target-check">
							<input type="checkbox" bind:checked={lbzTargetEnabled} />
							<svg viewBox="9,0,128,160" width="14" height="14" aria-hidden="true"><path d="m75.354 7.823v144l61-35v-74z" fill="#eb743b"/><path d="m70.354 7.823-61 35v74l61 35z" fill="#353070"/></svg>
							<span>ListenBrainz</span>
						</label>
						{#if lbzTargetEnabled}
							<div class="target-config">
								<span class="config-label">Mark as</span>
								<div class="toggle-group">
									<button
										class="toggle-btn"
										class:toggle-active={lbzTargetConfig.feedbackType === 'love'}
										onclick={() => lbzTargetConfig.feedbackType = 'love'}
									>Love</button>
									<button
										class="toggle-btn"
										class:toggle-active={lbzTargetConfig.feedbackType === 'hate'}
										onclick={() => lbzTargetConfig.feedbackType = 'hate'}
									>Hate</button>
								</div>
							</div>
						{:else}
							<span class="target-hint">Submit feedback to ListenBrainz</span>
						{/if}
					</div>
				{/if}

			</div>
		</section>

		<!-- ── Recurring schedule section ─────────────────────────────────── -->
		<section class="relay-card">
			<div class="relay-card-label">SCHEDULE</div>
			<div class="schedule-row">
				<label class="schedule-toggle-label">
					<input type="checkbox" bind:checked={recurring} />
					<span>Run on a schedule</span>
				</label>
				{#if recurring}
					<div class="schedule-freq">
						<span class="config-label">Frequency:</span>
						<select class="relay-select" bind:value={intervalMinutes}>
							{#each frequencyOptions as opt}
								<option value={opt.value}>{opt.label}</option>
							{/each}
						</select>
					</div>
				{/if}
			</div>
		</section>

		<!-- ── Queue button ─────────────────────────────────────────────────── -->
		<button
			class="run-btn"
			onclick={queueJob}
			disabled={!canQueue}
			aria-busy={relaying}
		>
			{#if relaying}
				<span class="status-spinner" aria-hidden="true"></span>
				Queueing…
			{:else}
				<svg viewBox="0 0 20 20" fill="currentColor" width="16" height="16" aria-hidden="true">
					<path fill-rule="evenodd" d="M10 18a8 8 0 100-16 8 8 0 000 16zM9.555 7.168A1 1 0 008 8v4a1 1 0 001.555.832l3-2a1 1 0 000-1.664l-3-2z" clip-rule="evenodd"/>
				</svg>
				Queue Job
			{/if}
		</button>

		<!-- ── Error ───────────────────────────────────────────────────────── -->
		{#if relayError}
			<div class="relay-result result-error" role="alert">
				<svg viewBox="0 0 16 16" fill="currentColor" width="14" height="14" aria-hidden="true">
					<path d="M8 1L1 14h14L8 1zm0 9V6m0 3v1" stroke="currentColor" stroke-width="1.5" fill="none"/>
				</svg>
				{relayError}
			</div>
		{/if}

		<!-- ── Jobs list ────────────────────────────────────────────────────── -->
		{#if jobs.length > 0}
			<div class="jobs-section">
				<div class="jobs-section-label">Jobs</div>

				{#each jobs as job (job.id)}
					<a href="/jobs/{job.id}" class="job-card" data-status={job.status}>
						<div class="job-card-top">
							<div class="job-card-left">
								<span class="job-status-dot {statusColor(job.status)}"></span>
								<div class="job-card-info">
									<span class="job-title">
										{summarizeSource(job.source_config)} → {summarizeTargets(job.targets_config)}
									</span>
									{#if job.recurring && job.interval_minutes}
										<span class="job-recur-hint">
											Every {frequencyOptions.find(o => o.value === job.interval_minutes)?.label?.toLowerCase().replace('every ', '') ?? `${job.interval_minutes}m`}
											{#if job.run_at && job.status === 'scheduled'}
												· Next {formatNextRun(job.run_at)}
											{/if}
										</span>
									{/if}
								</div>
							</div>
							<div class="job-card-right">
								{#if job.status === 'running'}
									<span class="job-progress">
										{#if job.tracks_fetched > 0}
											{job.tracks_ok + job.tracks_err} / {job.tracks_fetched} tracks
										{:else}
											<span class="status-spinner" aria-hidden="true"></span>
											Fetching…
										{/if}
									</span>
								{:else if job.status === 'queued' || job.status === 'scheduled'}
									<span class="job-status-text">{job.status}</span>
								{:else}
									<span class="job-done-stats">
										{#if job.tracks_ok > 0}
											<span class="stat-ok">{job.tracks_ok} ✓</span>
										{/if}
										{#if job.tracks_err > 0}
											<span class="stat-err">{job.tracks_err} ✗</span>
										{/if}
										{#if job.tracks_ok === 0 && job.tracks_err === 0}
											<span class="job-status-text">{job.status}</span>
										{/if}
									</span>
								{/if}
								{#if job.status === 'queued' || job.status === 'scheduled'}
									<button class="job-cancel-btn" onclick={(e) => { e.preventDefault(); e.stopPropagation(); cancelJob(job.id); }}>
										Cancel
									</button>
								{/if}
							</div>
						</div>
					</a>
				{/each}
			</div>
		{/if}

	</main>
</div>

{:else}

<!-- ═══════════════════════════════════════════════════════════════════════ -->
<!-- Setup Wizard                                                            -->
<!-- ═══════════════════════════════════════════════════════════════════════ -->

<div class="setup-root">
	<!-- Background texture / atmosphere -->
	<div class="bg-noise" aria-hidden="true"></div>

	<main class="setup-main">
		<!-- Header -->
		<header class="setup-header">
			<div class="logo-mark" aria-hidden="true">
				<span class="logo-signal"></span>
				<span class="logo-signal"></span>
				<span class="logo-signal"></span>
			</div>
			<div>
				<h1 class="setup-title">RatingRelay</h1>
				<p class="setup-subtitle">Connect your services to begin relaying ratings.</p>
			</div>
		</header>

		<!-- Progress rail -->
		<div class="progress-rail" role="list" aria-label="Setup steps">
			<div
				class="progress-step"
				class:active={step === 'plex'}
				class:done={plex.status === 'connected'}
				role="listitem"
			>
				<span class="step-dot"></span>
				<span class="step-label">Plex</span>
			</div>
			<div class="progress-line" class:filled={plex.status === 'connected'}></div>
			<div
				class="progress-step"
				class:active={step === 'server-select'}
				class:done={!!plex.server_name}
				role="listitem"
			>
				<span class="step-dot"></span>
				<span class="step-label">Server</span>
			</div>
			<div class="progress-line" class:filled={!!plex.server_name}></div>
			<div
				class="progress-step"
				class:active={step === 'music-services'}
				class:done={step === 'done'}
				role="listitem"
			>
				<span class="step-dot"></span>
				<span class="step-label">Music</span>
			</div>
			<div class="progress-line" class:filled={step === 'done'}></div>
			<div
				class="progress-step"
				class:done={step === 'done'}
				role="listitem"
			>
				<span class="step-dot"></span>
				<span class="step-label">Ready</span>
			</div>
		</div>

		<!-- ── Step: Plex ──────────────────────────────────────── -->
		<section class="service-card" class:card-active={step === 'plex'} class:card-done={plex.status === 'connected'}>
			<div class="card-header">
				<div class="service-icon plex-icon" aria-hidden="true">
          <svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 512 512"><rect width="512" height="512" rx="15%" fill="#282a2d"/><path d="M256 70H148l108 186-108 186h108l108-186z" fill="#e5a00d"/></svg>
				</div>
				<div class="service-info">
					<h2 class="service-name">Plex</h2>
					<p class="service-desc">Source of your media ratings</p>
				</div>
				<div class="status-badge" data-status={plex.status}>
					{#if plex.status === 'checking'}
						<span class="status-spinner" aria-label="Checking"></span>
					{:else if plex.status === 'connected'}
						<svg viewBox="0 0 16 16" fill="currentColor" width="14" height="14"><path d="M13.5 3.5L6 11 2.5 7.5l-1 1L6 13l8.5-8.5z"/></svg>
					{:else if plex.status === 'error'}
						<svg viewBox="0 0 16 16" fill="currentColor" width="14" height="14"><path d="M8 1L1 14h14L8 1zm0 4v4m0 2v1" stroke="currentColor" stroke-width="1.5" fill="none"/></svg>
					{/if}
					<span>{statusLabel[plex.status]}</span>
					{#if plex.username}
						<span class="status-user">· {plex.username}</span>
					{/if}
				</div>
			</div>

			{#if plex.status !== 'connected' && plex.status !== 'disabled'}
				<div class="card-body">
					{#if plex.status === 'needs-auth' && plexPopup}
						<p class="card-instructions">
							A Plex login window has opened. Sign in there to continue.
						</p>
						<p class="pin-hint">Waiting for authorisation…</p>
					{:else}
						<p class="card-instructions">
							A Plex login window will open. Sign in with your Plex account to grant access.
						</p>
						<button class="btn btn-primary" onclick={startPlexAuth} disabled={plex.status === 'checking'}>
							{plex.status === 'checking' ? 'Opening Plex login…' : 'Connect Plex'}
						</button>
					{/if}
				</div>
			{/if}
		</section>

		<!-- ── Step: Server selection ──────────────────────────── -->
		<section
			class="service-card"
			class:card-active={step === 'server-select'}
			class:card-done={!!plex.server_name}
			style:opacity={plex.status !== 'connected' ? '0.35' : '1'}
			style:pointer-events={plex.status !== 'connected' ? 'none' : 'auto'}
		>
			<div class="card-header">
				<div class="service-icon plex-icon" aria-hidden="true">
					<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" xmlns="http://www.w3.org/2000/svg">
						<rect x="2" y="3" width="20" height="5" rx="1"/>
						<rect x="2" y="10" width="20" height="5" rx="1"/>
						<rect x="2" y="17" width="20" height="5" rx="1"/>
						<circle cx="18" cy="5.5" r="1" fill="currentColor" stroke="none"/>
						<circle cx="18" cy="12.5" r="1" fill="currentColor" stroke="none"/>
						<circle cx="18" cy="19.5" r="1" fill="currentColor" stroke="none"/>
					</svg>
				</div>
				<div class="service-info">
					<h2 class="service-name">Plex server</h2>
					<p class="service-desc">Choose which server to relay ratings from</p>
				</div>
				{#if plex.server_name}
					<div class="status-badge" data-status="connected">
						<svg viewBox="0 0 16 16" fill="currentColor" width="14" height="14"><path d="M13.5 3.5L6 11 2.5 7.5l-1 1L6 13l8.5-8.5z"/></svg>
						<span>{plex.server_name}</span>
					</div>
				{/if}
			</div>

			{#if !plex.server_name && plex.status === 'connected'}
				<div class="card-body">
					{#if loadingServers && availableServers.length === 0}
						<p class="pin-hint">Loading servers…</p>
					{:else if serverError}
						<p class="card-instructions" style:color="oklch(0.65 0.18 22)">{serverError}</p>
						<button class="btn btn-ghost" onclick={loadServers}>Retry</button>
					{:else if availableServers.length === 0}
						<p class="card-instructions">No Plex Media Servers found on this account.</p>
					{:else}
						<p class="card-instructions">Select the server RatingRelay should read ratings from.</p>
						<div class="server-select-row">
							<select class="server-select" bind:value={selectedServer}>
								{#each availableServers as srv}
									<option value={srv.name}>{srv.name}</option>
								{/each}
							</select>
							<button
								class="btn btn-primary"
								onclick={confirmServer}
								disabled={!selectedServer || loadingServers}
							>
								{loadingServers ? 'Connecting…' : 'Select'}
							</button>
						</div>
						{#if serverError}
							<p class="card-instructions" style:color="oklch(0.65 0.18 22)" role="alert">{serverError}</p>
						{/if}
					{/if}
				</div>
			{/if}
		</section>

		<!-- ── Step: Music Services ────────────────────────────── -->
		<section class="service-group" class:group-active={step === 'music-services' || step === 'done'}>
			<div class="group-label">
				<span>Music services</span>
				<span class="group-hint">Connect at least one</span>
			</div>

			<!-- LastFM -->
			<div class="service-card inner-card" class:card-done={lastfm.status === 'connected'} class:card-disabled={lastfm.status === 'disabled'}>
				<div class="card-header">
					<div class="service-icon lastfm-icon" aria-hidden="true">
						<svg viewBox="0 0 24 24" fill="currentColor" xmlns="http://www.w3.org/2000/svg">
							<path d="M10.584 17.21l-.88-2.392s-1.43 1.6-3.573 1.6c-1.898 0-3.244-1.65-3.244-4.29 0-3.38 1.703-4.594 3.38-4.594 2.42 0 3.19 1.565 3.85 3.576l.88 2.75c.88 2.673 2.53 4.815 7.315 4.815 3.41 0 5.73-1.045 5.73-3.8 0-2.227-1.265-3.38-3.63-3.93l-1.76-.385c-1.21-.275-1.57-.77-1.57-1.593 0-.935.737-1.483 1.95-1.483 1.318 0 2.03.494 2.14 1.67l2.75-.33c-.22-2.47-1.925-3.48-4.755-3.48-2.49 0-4.82.935-4.82 3.93 0 1.87.907 3.05 3.19 3.6l1.87.44c1.375.33 1.87.88 1.87 1.76 0 1.046-.99 1.483-2.862 1.483-2.75 0-3.9-1.43-4.562-3.38l-.91-2.75c-1.155-3.52-3-4.87-6.655-4.87C1.87 5.528 0 8.278 0 12.238c0 3.82 1.87 6.234 6.04 6.234 3.135 0 4.544-1.262 4.544-1.262z"/>
						</svg>
					</div>
					<div class="service-info">
						<h3 class="service-name">Last.fm</h3>
						<p class="service-desc">Scrobble destination</p>
					</div>
					<div class="status-badge" data-status={lastfm.status}>
						{#if lastfm.status === 'checking'}
							<span class="status-spinner" aria-label="Checking"></span>
						{:else if lastfm.status === 'connected'}
							<svg viewBox="0 0 16 16" fill="currentColor" width="14" height="14"><path d="M13.5 3.5L6 11 2.5 7.5l-1 1L6 13l8.5-8.5z"/></svg>
						{/if}
						<span>{statusLabel[lastfm.status]}</span>
						{#if lastfm.username}<span class="status-user">· {lastfm.username}</span>{/if}
					</div>
				</div>
			{#if lastfm.status !== 'connected' && lastfm.status !== 'disabled'}
				<div class="card-body">
					<p class="card-instructions">
						Get your API key and secret from
						<a href="https://www.last.fm/api/account/create" target="_blank" rel="noopener">last.fm/api/account/create</a>,
						then enter your Last.fm credentials below.
					</p>
					<div class="credentials-form">
						<input class="token-input" type="text" placeholder="API Key" bind:value={lastfmForm.api_key} autocomplete="off" spellcheck={false} />
						<input class="token-input" type="password" placeholder="API Secret" bind:value={lastfmForm.api_secret} autocomplete="off" spellcheck={false} />
						<input class="token-input" type="text" placeholder="Username" bind:value={lastfmForm.username} autocomplete="off" spellcheck={false} />
						<input class="token-input" type="password" placeholder="Password" bind:value={lastfmForm.password} autocomplete="current-password"
							onkeydown={(e) => e.key === 'Enter' && submitLastFMAuth()} />
					</div>
					{#if lastfmError}
						<p class="form-error" role="alert">{lastfmError}</p>
					{/if}
					<button
						class="btn btn-primary"
						onclick={submitLastFMAuth}
						disabled={lastfm.status === 'checking' || !lastfmForm.api_key || !lastfmForm.api_secret || !lastfmForm.username || !lastfmForm.password}
					>
						{lastfm.status === 'checking' ? 'Connecting…' : 'Connect Last.fm'}
					</button>
				</div>
			{/if}
			</div>

			<!-- ListenBrainz -->
			<div class="service-card inner-card" class:card-done={listenbrainz.status === 'connected'} class:card-disabled={listenbrainz.status === 'disabled'}>
				<div class="card-header">
					<div class="service-icon lb-icon" aria-hidden="true">
            <svg id="svg1591" viewBox="9,0,128,160" xmlns="http://www.w3.org/2000/svg" xmlns:xlink="http://www.w3.org/1999/xlink" inkscape:version="1.0.2 (e86c870879, 2021-01-15, custom)" sodipodi:docname="ListenBrainz_logo.svg" xmlns:cc="http://creativecommons.org/ns#" xmlns:dc="http://purl.org/dc/elements/1.1/" xmlns:inkscape="http://www.inkscape.org/namespaces/inkscape" xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#" xmlns:sodipodi="http://sodipodi.sourceforge.net/DTD/sodipodi-0.0.dtd">
 <defs id="defs1552">
  <path id="a" d="m75.354 33.239h51.433v90.792h-51.433z"/>
  <clipPath id="b">
   <use id="use1554" xlink:href="#a"/>
  </clipPath>
 </defs>
 <metadata id="metadata1597">
  <rdf:RDF>
   <cc:Work rdf:about="">
    <dc:format>image/svg+xml</dc:format>
    <dc:type rdf:resource="http://purl.org/dc/dcmitype/StillImage"/>
    <dc:title>ListenBrainz 2020 logo (version 2)</dc:title>
   </cc:Work>
  </rdf:RDF>
 </metadata>
 <sodipodi:namedview bordercolor="#666666" borderopacity="1" gridtolerance="10" guidetolerance="10" inkscape:current-layer="svg1591" inkscape:cx="64" inkscape:cy="80" inkscape:pageopacity="0" inkscape:pageshadow="2" inkscape:window-height="1014" inkscape:window-maximized="1" inkscape:window-width="1920" inkscape:window-x="0" inkscape:window-y="36" inkscape:zoom="5.24375" objecttolerance="10" pagecolor="#ffffff" showgrid="false"/>
 <path id="path1545" d="m75.354 7.823v144l61-35v-74z" fill="#eb743b"/>
 <title id="title1547">ListenBrainz 2020 logo (version 2)</title>
 <path id="path1549" d="m70.354 7.823-61 35v74l61 35z" fill="#353070"/>
 <g id="g1563" opacity=".07">
  <path id="path1557" clip-path="url(#b)" d="m92.657 99.557a2.1 2.1 0 0 1 1.819-1.03c.378 0 .752.104 1.078.297a2.12 2.12 0 0 1 .735 2.898 2.097 2.097 0 0 1 -1.817 1.032c-.379 0-.755-.105-1.084-.301a2.092 2.092 0 0 1 -.964-1.294 2.1 2.1 0 0 1 .233-1.602m12.602 22.362a2.121 2.121 0 0 1 -2.981-.225 2.116 2.116 0 0 1 .231-2.981 2.08 2.08 0 0 1 1.373-.512 2.117 2.117 0 0 1 2.109 2.278 2.086 2.086 0 0 1 -.732 1.44m-9.525-58.839a2.128 2.128 0 0 1 2.916-.656 2.12 2.12 0 0 1 .66 2.919 2.115 2.115 0 0 1 -2.919.661 2.128 2.128 0 0 1 -.657-2.924m6.379-20.187c-.6.951-1.937 1.271-2.912.653l-.096-.062a2.107 2.107 0 0 1 -.557-2.851 2.093 2.093 0 0 1 1.784-.98 2.11 2.11 0 0 1 1.781 3.24m16.767 11.598a2.096 2.096 0 0 1 1.783-.98c.4 0 .79.113 1.125.325.481.304.813.775.937 1.33a2.08 2.08 0 0 1 -.28 1.584c-.597.951-1.938 1.273-2.912.653l-.093-.062a2.105 2.105 0 0 1 -.56-2.85m0 23.463a2.096 2.096 0 0 1 1.783-.98c.4 0 .79.112 1.125.326.481.304.813.774.937 1.329a2.083 2.083 0 0 1 -.28 1.585c-.597.95-1.938 1.271-2.912.652l-.093-.062a2.105 2.105 0 0 1 -.56-2.85m0 26.303a2.099 2.099 0 0 1 1.783-.98c.4 0 .79.113 1.125.326.481.304.813.775.937 1.33a2.08 2.08 0 0 1 -.28 1.584c-.597.951-1.938 1.271-2.912.653l-.093-.062a2.107 2.107 0 0 1 -.56-2.851"/>
  <path id="path1559" clip-path="url(#b)" d="m108.53 113.927a6.11 6.11 0 0 0 -8.631-.662 6.12 6.12 0 0 0 -2.087 5.396c-1.949.701-4.258 1.366-5.328 1.332-.822-.043-1.302-.345-2.224-.971-1.172-.795-2.418-1.512-4.149-1.847 2.936-2.761 6.009-6.83 8.584-12.834a6.15 6.15 0 0 0 5.039-2.972c1.724-2.896.773-6.659-2.124-8.394a6.133 6.133 0 0 0 -3.134-.864 6.154 6.154 0 0 0 -5.261 2.986 6.079 6.079 0 0 0 -.678 4.636 6.075 6.075 0 0 0 2.302 3.429c-4.053 9.218-9.219 13.08-12.565 14.685a2.016 2.016 0 0 0 -.361.168c-1.186.531-2.05.704-2.558.808v4c.275-.03 1.884-.118 4.247-1.192 5.347-1.357 6.839-.354 8.407.709 1.021.692 2.291 1.554 4.263 1.655.099.006.204.009.308.009 2.11 0 5.172-1.022 7.021-1.723a6.132 6.132 0 0 0 4.285 1.75 6.112 6.112 0 0 0 3.983-1.476 6.072 6.072 0 0 0 2.119-4.176 6.072 6.072 0 0 0 -1.458-4.452m-15.873-16.776a2.114 2.114 0 0 1 3.632 2.165 2.097 2.097 0 0 1 -1.817 1.032c-.379 0-.755-.105-1.084-.301a2.092 2.092 0 0 1 -.964-1.294 2.101 2.101 0 0 1 .233-1.602m12.602 22.363a2.121 2.121 0 0 1 -2.981-.225 2.116 2.116 0 0 1 .231-2.981 2.08 2.08 0 0 1 1.373-.512 2.117 2.117 0 0 1 2.109 2.278 2.086 2.086 0 0 1 -.732 1.44m1.045-81.487a6.088 6.088 0 0 0 -2.708-3.844 6.12 6.12 0 0 0 -3.264-.944 6.076 6.076 0 0 0 -5.174 2.85 6.108 6.108 0 0 0 .144 6.758c-3.646 4.124-8.707 4.314-9.943 4.293-3.692-1.604-7.234-2.164-10.005-2.317v3.996c2.442.144 5.553.686 8.748 2.136.005.002.01.002.015.005 3.445 1.562 6.334 3.927 8.607 7.041a6.364 6.364 0 0 0 -.381.538c-1.796 2.854-.943 6.643 1.903 8.444.98.622 2.111.951 3.269.951a6.105 6.105 0 0 0 5.183-2.851c1.806-2.851.951-6.642-1.908-8.453-1.384-.872-3.092-1.114-4.667-.755-1.538-2.155-3.323-4.017-5.345-5.567 2.517-.778 5.401-2.268 7.78-5.095a6.18 6.18 0 0 0 1.77.266 6.08 6.08 0 0 0 5.172-2.849 6.05 6.05 0 0 0 .804-4.603m-10.57 22.647a2.128 2.128 0 0 1 2.916-.656 2.12 2.12 0 0 1 .66 2.919 2.115 2.115 0 0 1 -2.919.661 2.128 2.128 0 0 1 -.657-2.924m6.379-20.187c-.6.951-1.937 1.271-2.912.653l-.096-.062a2.107 2.107 0 0 1 -.557-2.851 2.093 2.093 0 0 1 1.784-.98 2.11 2.11 0 0 1 1.781 3.24"/>
  <path id="path1561" clip-path="url(#b)" d="m115 79.011a6.036 6.036 0 0 0 2.11 2.649 6.098 6.098 0 0 0 3.55 1.14 6.083 6.083 0 0 0 5.173-2.848 6.064 6.064 0 0 0 .805-4.605 6.103 6.103 0 0 0 -2.709-3.844 6.128 6.128 0 0 0 -3.266-.944c-.125 0-.25.008-.375.018-.71-3.158-.919-7.769.035-11.265.113.011.225.024.337.024a6.08 6.08 0 0 0 5.173-2.848 6.062 6.062 0 0 0 .805-4.604 6.103 6.103 0 0 0 -2.709-3.844 6.128 6.128 0 0 0 -3.266-.944 6.079 6.079 0 0 0 -5.173 2.85 6.104 6.104 0 0 0 1.025 7.764c.021.02.048.037.071.059-1.349 4.518-1.145 10.324-.036 14.417-.144.132-.294.254-.426.399a6.327 6.327 0 0 0 -.634.825 6.2 6.2 0 0 0 -.71 1.578c-3.371.488-5.198 1.671-6.824 2.729-2.035 1.324-3.961 2.572-9.636 2.666-1.963-.23-3.552-.178-5.29-.116-1.43.052-2.91.105-4.848.003-1.335-.071-2.104-.882-3.458-2.424-1.803-2.055-4.219-4.648-9.369-5.022v4c3.295.299 4.796 1.887 6.356 3.667 1.487 1.694 3.175 3.615 6.256 3.782 2.118.114 3.76.052 5.208.001 2.491-.092 4.453-.161 7.855.683 2.608.644 12.099 9.674 14.774 14.323-.109.142-.219.282-.314.434-1.743 2.755-1.026 6.385 1.62 8.251.072.052.151.104.283.191.981.621 2.11.948 3.267.948a6.081 6.081 0 0 0 5.173-2.849 6.06 6.06 0 0 0 .805-4.603 6.1 6.1 0 0 0 -2.709-3.845 6.128 6.128 0 0 0 -4.809-.749c-2.56-4.188-8.652-10.455-13.189-13.792 1.813-.628 3.05-1.436 4.212-2.188 1.427-.93 2.614-1.69 4.857-2.067m3.88-26.926a2.096 2.096 0 0 1 1.783-.98c.4 0 .79.113 1.125.325.481.304.813.775.937 1.33a2.08 2.08 0 0 1 -.28 1.584c-.597.951-1.938 1.273-2.912.653l-.093-.062a2.105 2.105 0 0 1 -.56-2.85m0 23.463a2.096 2.096 0 0 1 1.783-.98c.4 0 .79.112 1.125.326.481.304.813.774.937 1.329a2.083 2.083 0 0 1 -.28 1.585c-.597.95-1.938 1.272-2.912.652l-.093-.062a2.105 2.105 0 0 1 -.56-2.85m0 26.304a2.099 2.099 0 0 1 1.783-.98c.4 0 .79.113 1.125.326.481.304.813.775.937 1.33a2.08 2.08 0 0 1 -.28 1.584c-.597.951-1.938 1.271-2.912.653l-.093-.062a2.107 2.107 0 0 1 -.56-2.851"/>
 </g>
 <path id="path1565" d="m108.53 116.927a6.11 6.11 0 0 0 -8.631-.662 6.12 6.12 0 0 0 -2.087 5.396c-1.949.701-4.258 1.366-5.328 1.332-.822-.043-1.302-.345-2.224-.971-1.172-.795-2.418-1.512-4.149-1.847 2.936-2.761 6.009-6.83 8.584-12.834a6.15 6.15 0 0 0 5.039-2.972c1.724-2.896.773-6.659-2.124-8.394a6.133 6.133 0 0 0 -3.134-.864 6.154 6.154 0 0 0 -5.261 2.986 6.079 6.079 0 0 0 -.678 4.636 6.075 6.075 0 0 0 2.302 3.429c-4.053 9.218-9.219 13.08-12.565 14.685a2.016 2.016 0 0 0 -.361.168c-1.186.531-2.05.704-2.558.808v4c.275-.03 1.884-.118 4.247-1.192 5.347-1.357 6.839-.354 8.407.709 1.021.692 2.291 1.554 4.263 1.655.099.006.204.009.308.009 2.11 0 5.172-1.022 7.021-1.723a6.132 6.132 0 0 0 4.285 1.75 6.112 6.112 0 0 0 3.983-1.476 6.072 6.072 0 0 0 2.119-4.176 6.072 6.072 0 0 0 -1.458-4.452m-15.873-16.776a2.114 2.114 0 0 1 3.632 2.165 2.097 2.097 0 0 1 -1.817 1.032c-.379 0-.755-.105-1.084-.301a2.092 2.092 0 0 1 -.964-1.294 2.101 2.101 0 0 1 .233-1.602m12.602 22.363a2.121 2.121 0 0 1 -2.981-.225 2.116 2.116 0 0 1 .231-2.981 2.08 2.08 0 0 1 1.373-.512 2.117 2.117 0 0 1 2.109 2.278 2.086 2.086 0 0 1 -.732 1.44m1.045-81.487a6.088 6.088 0 0 0 -2.708-3.844 6.12 6.12 0 0 0 -3.264-.944 6.076 6.076 0 0 0 -5.174 2.85 6.108 6.108 0 0 0 .144 6.758c-3.646 4.124-8.707 4.314-9.943 4.293-3.692-1.604-7.234-2.164-10.005-2.317v3.996c2.442.144 5.553.686 8.748 2.136.005.002.01.002.015.005 3.445 1.562 6.334 3.927 8.607 7.041a6.364 6.364 0 0 0 -.381.538c-1.796 2.854-.943 6.643 1.903 8.444.98.622 2.111.951 3.269.951a6.105 6.105 0 0 0 5.183-2.851c1.806-2.851.951-6.642-1.908-8.453-1.384-.872-3.092-1.114-4.667-.755-1.538-2.155-3.323-4.017-5.345-5.567 2.517-.778 5.401-2.268 7.78-5.095a6.18 6.18 0 0 0 1.77.266 6.08 6.08 0 0 0 5.172-2.848 6.053 6.053 0 0 0 .804-4.604m-10.57 22.647a2.128 2.128 0 0 1 2.916-.656 2.12 2.12 0 0 1 .66 2.919 2.115 2.115 0 0 1 -2.919.661 2.128 2.128 0 0 1 -.657-2.924m6.379-20.187c-.6.951-1.937 1.271-2.912.653l-.096-.062a2.107 2.107 0 0 1 -.557-2.851 2.093 2.093 0 0 1 1.784-.98 2.11 2.11 0 0 1 1.781 3.24" fill="#fffedb"/>
 <path id="path1567" d=""/>
 <path id="path1569" d="m106.304 40.031a6.088 6.088 0 0 0 -2.708-3.844 6.12 6.12 0 0 0 -3.264-.944 6.076 6.076 0 0 0 -5.174 2.85 6.108 6.108 0 0 0 .144 6.758c-3.646 4.124-8.707 4.314-9.943 4.293-3.692-1.604-7.234-2.164-10.005-2.317v3.996c2.442.144 5.553.686 8.748 2.136.005.002.01.002.015.005 3.445 1.562 6.334 3.927 8.607 7.041a6.206 6.206 0 0 0 -.381.538c-1.796 2.854-.943 6.643 1.903 8.444.98.622 2.111.951 3.269.951a6.105 6.105 0 0 0 5.183-2.851c1.806-2.851.951-6.642-1.908-8.453-1.384-.872-3.092-1.114-4.667-.755-1.538-2.155-3.323-4.017-5.345-5.567 2.517-.778 5.401-2.268 7.78-5.095a6.18 6.18 0 0 0 1.77.266 6.08 6.08 0 0 0 5.172-2.849 6.05 6.05 0 0 0 .804-4.603m-10.57 22.647a2.128 2.128 0 0 1 2.916-.656 2.12 2.12 0 0 1 .66 2.919 2.115 2.115 0 0 1 -2.919.661 2.128 2.128 0 0 1 -.657-2.924m6.379-20.187c-.6.951-1.937 1.271-2.912.653l-.096-.062a2.107 2.107 0 0 1 -.557-2.851 2.093 2.093 0 0 1 1.784-.98 2.11 2.11 0 0 1 1.781 3.24" fill="#d3562c"/>
 <path id="path1571" d="m115 82.011a6.036 6.036 0 0 0 2.11 2.649 6.098 6.098 0 0 0 3.55 1.14 6.081 6.081 0 0 0 5.173-2.849 6.063 6.063 0 0 0 .805-4.604 6.103 6.103 0 0 0 -2.709-3.844 6.128 6.128 0 0 0 -3.266-.944c-.125 0-.25.008-.375.018-.71-3.158-.919-7.769.035-11.265.113.011.225.024.337.024a6.08 6.08 0 0 0 5.173-2.848 6.062 6.062 0 0 0 .805-4.604 6.103 6.103 0 0 0 -2.709-3.844 6.128 6.128 0 0 0 -3.266-.944 6.079 6.079 0 0 0 -5.173 2.85 6.104 6.104 0 0 0 1.025 7.764c.021.02.048.037.071.059-1.349 4.518-1.145 10.324-.036 14.417-.144.132-.294.254-.426.399a6.327 6.327 0 0 0 -.634.825 6.2 6.2 0 0 0 -.71 1.578c-3.371.488-5.198 1.671-6.824 2.729-2.035 1.323-3.961 2.571-9.636 2.665-1.963-.23-3.552-.178-5.29-.116-1.43.052-2.91.105-4.848.003-1.335-.071-2.104-.882-3.458-2.424-1.803-2.055-4.219-4.648-9.369-5.022v4c3.295.298 4.796 1.887 6.356 3.667 1.487 1.694 3.175 3.615 6.256 3.782 2.118.114 3.76.052 5.208.001 2.491-.092 4.453-.161 7.855.683 2.608.644 12.099 9.674 14.774 14.323-.109.142-.219.282-.314.434-1.743 2.755-1.026 6.385 1.62 8.251.072.052.151.104.283.191.981.621 2.11.948 3.267.948a6.081 6.081 0 0 0 5.173-2.849 6.06 6.06 0 0 0 .805-4.603 6.1 6.1 0 0 0 -2.709-3.845 6.128 6.128 0 0 0 -4.809-.749c-2.56-4.188-8.652-10.455-13.189-13.792 1.813-.628 3.05-1.436 4.212-2.188 1.427-.929 2.614-1.689 4.857-2.066m3.88-26.926a2.096 2.096 0 0 1 1.783-.98c.4 0 .79.113 1.125.325.481.304.813.775.937 1.33a2.08 2.08 0 0 1 -.28 1.584c-.597.951-1.938 1.273-2.912.653l-.093-.062a2.105 2.105 0 0 1 -.56-2.85m0 23.463a2.096 2.096 0 0 1 1.783-.98c.4 0 .79.112 1.125.326.481.304.813.774.937 1.329a2.084 2.084 0 0 1 -.28 1.585c-.597.95-1.938 1.271-2.912.652l-.093-.062a2.105 2.105 0 0 1 -.56-2.85m0 26.304a2.099 2.099 0 0 1 1.783-.98c.4 0 .79.113 1.125.326.481.304.813.775.937 1.33a2.08 2.08 0 0 1 -.28 1.584c-.597.951-1.938 1.271-2.912.653l-.093-.062a2.107 2.107 0 0 1 -.56-2.851" fill="#d3562c"/>
 <path id="path1573" d="m108.53 113.927a6.11 6.11 0 0 0 -8.631-.662 6.12 6.12 0 0 0 -2.087 5.396c-1.949.701-4.258 1.366-5.328 1.332-.822-.043-1.302-.345-2.224-.971-1.172-.795-2.418-1.512-4.149-1.847 2.936-2.761 6.009-6.83 8.584-12.834a6.15 6.15 0 0 0 5.039-2.972c1.724-2.896.773-6.659-2.124-8.394a6.133 6.133 0 0 0 -3.134-.864 6.154 6.154 0 0 0 -5.261 2.986 6.079 6.079 0 0 0 -.678 4.636 6.075 6.075 0 0 0 2.302 3.429c-4.053 9.218-9.219 13.08-12.565 14.685a2.016 2.016 0 0 0 -.361.168c-1.186.531-2.05.704-2.558.808v4c.275-.03 1.884-.118 4.247-1.192 5.347-1.357 6.839-.354 8.407.709 1.021.692 2.291 1.554 4.263 1.655.099.006.204.009.308.009 2.11 0 5.172-1.022 7.021-1.723a6.132 6.132 0 0 0 4.285 1.75 6.112 6.112 0 0 0 3.983-1.476 6.072 6.072 0 0 0 2.119-4.176 6.072 6.072 0 0 0 -1.458-4.452m-15.873-16.776a2.114 2.114 0 0 1 3.632 2.165 2.097 2.097 0 0 1 -1.817 1.032c-.379 0-.755-.105-1.084-.301a2.092 2.092 0 0 1 -.964-1.294 2.101 2.101 0 0 1 .233-1.602m12.602 22.363a2.121 2.121 0 0 1 -2.981-.225 2.116 2.116 0 0 1 .231-2.981 2.08 2.08 0 0 1 1.373-.512 2.117 2.117 0 0 1 2.109 2.278 2.086 2.086 0 0 1 -.732 1.44m1.045-81.487a6.088 6.088 0 0 0 -2.708-3.844 6.12 6.12 0 0 0 -3.264-.944 6.076 6.076 0 0 0 -5.174 2.85 6.108 6.108 0 0 0 .144 6.758c-3.646 4.124-8.707 4.314-9.943 4.293-3.692-1.604-7.234-2.164-10.005-2.317v3.996c2.442.144 5.553.686 8.748 2.136.005.002.01.002.015.005 3.445 1.562 6.334 3.927 8.607 7.041a6.364 6.364 0 0 0 -.381.538c-1.796 2.854-.943 6.643 1.903 8.444.98.622 2.111.951 3.269.951a6.105 6.105 0 0 0 5.183-2.851c1.806-2.851.951-6.642-1.908-8.453-1.384-.872-3.092-1.114-4.667-.755-1.538-2.155-3.323-4.017-5.345-5.567 2.517-.778 5.401-2.268 7.78-5.095a6.18 6.18 0 0 0 1.77.266 6.08 6.08 0 0 0 5.172-2.848 6.053 6.053 0 0 0 .804-4.604m-10.57 22.647a2.128 2.128 0 0 1 2.916-.656 2.12 2.12 0 0 1 .66 2.919 2.115 2.115 0 0 1 -2.919.661 2.128 2.128 0 0 1 -.657-2.924m6.379-20.187c-.6.951-1.937 1.271-2.912.653l-.096-.062a2.107 2.107 0 0 1 -.557-2.851 2.093 2.093 0 0 1 1.784-.98 2.11 2.11 0 0 1 1.781 3.24" fill="#fffedb"/>
 <path id="path1575" d="m115 79.011a6.036 6.036 0 0 0 2.11 2.649 6.098 6.098 0 0 0 3.55 1.14 6.083 6.083 0 0 0 5.173-2.848 6.064 6.064 0 0 0 .805-4.605 6.103 6.103 0 0 0 -2.709-3.844 6.128 6.128 0 0 0 -3.266-.944c-.125 0-.25.008-.375.018-.71-3.158-.919-7.769.035-11.265.113.011.225.024.337.024a6.08 6.08 0 0 0 5.173-2.848 6.062 6.062 0 0 0 .805-4.604 6.103 6.103 0 0 0 -2.709-3.844 6.128 6.128 0 0 0 -3.266-.944 6.079 6.079 0 0 0 -5.173 2.85 6.104 6.104 0 0 0 1.025 7.764c.021.02.048.037.071.059-1.349 4.518-1.145 10.324-.036 14.417-.144.132-.294.254-.426.399a6.327 6.327 0 0 0 -.634.825 6.2 6.2 0 0 0 -.71 1.578c-3.371.488-5.198 1.671-6.824 2.729-2.035 1.324-3.961 2.572-9.636 2.666-1.963-.23-3.552-.178-5.29-.116-1.43.052-2.91.105-4.848.003-1.335-.071-2.104-.882-3.458-2.424-1.803-2.055-4.219-4.648-9.369-5.022v4c3.295.299 4.796 1.887 6.356 3.667 1.487 1.694 3.175 3.615 6.256 3.782 2.118.114 3.76.052 5.208.001 2.491-.092 4.453-.161 7.855.683 2.608.644 12.099 9.674 14.774 14.323-.109.142-.219.282-.314.434-1.743 2.755-1.026 6.385 1.62 8.251.072.052.151.104.283.191.981.621 2.11.948 3.267.948a6.081 6.081 0 0 0 5.173-2.849 6.06 6.06 0 0 0 .805-4.603 6.1 6.1 0 0 0 -2.709-3.845 6.128 6.128 0 0 0 -4.809-.749c-2.56-4.188-8.652-10.455-13.189-13.792 1.813-.628 3.05-1.436 4.212-2.188 1.427-.93 2.614-1.69 4.857-2.067m3.88-26.926a2.096 2.096 0 0 1 1.783-.98c.4 0 .79.113 1.125.325.481.304.813.775.937 1.33a2.08 2.08 0 0 1 -.28 1.584c-.597.951-1.938 1.273-2.912.653l-.093-.062a2.105 2.105 0 0 1 -.56-2.85m0 23.463a2.096 2.096 0 0 1 1.783-.98c.4 0 .79.112 1.125.326.481.304.813.774.937 1.329a2.083 2.083 0 0 1 -.28 1.585c-.597.95-1.938 1.272-2.912.652l-.093-.062a2.105 2.105 0 0 1 -.56-2.85m0 26.304a2.099 2.099 0 0 1 1.783-.98c.4 0 .79.113 1.125.326.481.304.813.775.937 1.33a2.08 2.08 0 0 1 -.28 1.584c-.597.951-1.938 1.271-2.912.653l-.093-.062a2.107 2.107 0 0 1 -.56-2.851" fill="#fffedb"/>
 <g id="g1579" opacity=".25">
  <path id="path1577" d="m53.783 104.537c-.239 0-.48-.072-.691-.219a1.21 1.21 0 0 1 -.29-1.684c.273-.387.519-.799.73-1.227a9.028 9.028 0 0 0 -.501-8.961 1.208 1.208 0 1 1 2.032-1.307c2.176 3.389 2.42 7.736.633 11.34a11.833 11.833 0 0 1 -.92 1.547 1.204 1.204 0 0 1 -.993.511m5.556 2.877a1.207 1.207 0 0 1 -.968-1.922c.453-.615.854-1.279 1.198-1.973a13.243 13.243 0 0 0 -1.251-13.846 1.207 1.207 0 1 1 1.937-1.441 15.643 15.643 0 0 1 1.478 16.357c-.404.818-.88 1.604-1.414 2.33a1.213 1.213 0 0 1 -.98.495m-10.994-5.928a1.21 1.21 0 0 1 -1.045-1.802 4.802 4.802 0 0 0 .18-4.369 1.208 1.208 0 0 1 2.193-1.01 7.22 7.22 0 0 1 -.271 6.57 1.209 1.209 0 0 1 -1.057.611"/>
 </g>
 <g id="g1583" fill="#eb743b">
  <path id="path1581" d="m53.783 101.537c-.239 0-.48-.072-.691-.219a1.21 1.21 0 0 1 -.29-1.684c.273-.387.519-.799.73-1.227a9.028 9.028 0 0 0 -.501-8.961 1.208 1.208 0 1 1 2.032-1.307c2.176 3.389 2.42 7.736.633 11.34a11.833 11.833 0 0 1 -.92 1.547 1.204 1.204 0 0 1 -.993.511m5.556 2.877a1.207 1.207 0 0 1 -.968-1.922c.453-.615.854-1.279 1.198-1.973a13.243 13.243 0 0 0 -1.251-13.846 1.207 1.207 0 1 1 1.937-1.441 15.643 15.643 0 0 1 1.478 16.357c-.404.818-.88 1.604-1.414 2.33a1.213 1.213 0 0 1 -.98.495m-10.994-5.928a1.21 1.21 0 0 1 -1.045-1.802 4.802 4.802 0 0 0 .18-4.369 1.208 1.208 0 0 1 2.193-1.01 7.22 7.22 0 0 1 -.271 6.57 1.209 1.209 0 0 1 -1.057.611"/>
 </g>
 <g id="g1589">
  <path id="path1585" d="m66.082 47.24c-10.12 0-20.365 3.656-26.736 9.543-6.886 6.361-10.521 16.245-10.348 27.989a3.99 3.99 0 0 0 -3.41 3.941v18.119a4 4 0 0 0 4 4h2.033c.316 0 .619-.047.914-.115v1.602c0 2.209 1.565 4 3.495 4h1.898c1.93 0 3.495-1.791 3.495-4v-29.669c0-2.209-1.565-4-3.495-4h-1.898c-1.717 0-3.138 1.42-3.432 3.288.065-1.855.204-3.652.435-5.096 1.154-7.232 4.119-13.268 8.688-17.488 5.664-5.232 15.226-8.613 24.361-8.613 1.042 0 3.241.043 4.277.143v-3.49c-1.2-.117-3.163-.154-4.277-.154zm-34.459 59.188a2.652 2.652 0 0 1 -2.651-2.65v-12.01a2.651 2.651 0 0 1 2.651-2.652h.913v17.312z" opacity=".25"/>
  <path id="path1587" d="m66.082 44.24c-10.12 0-20.365 3.656-26.736 9.543-6.886 6.361-10.521 16.245-10.348 27.989a3.99 3.99 0 0 0 -3.41 3.941v18.119a4 4 0 0 0 4 4h2.033c.316 0 .619-.047.914-.115v1.602c0 2.209 1.565 4 3.495 4h1.898c1.93 0 3.495-1.791 3.495-4v-29.669c0-2.209-1.565-4-3.495-4h-1.898c-1.717 0-3.138 1.42-3.432 3.287.065-1.854.204-3.651.435-5.096 1.154-7.232 4.119-13.268 8.688-17.488 5.664-5.232 15.226-8.613 24.361-8.613 1.042 0 3.241.043 4.277.143v-3.49c-1.2-.116-3.163-.153-4.277-.153zm-34.459 59.188a2.652 2.652 0 0 1 -2.651-2.65v-12.01a2.651 2.651 0 0 1 2.651-2.652h.913v17.312z" fill="#fffedb"/>
 </g>
</svg>

					</div>
					<div class="service-info">
						<h3 class="service-name">ListenBrainz</h3>
						<p class="service-desc">Scrobble destination</p>
					</div>
					<div class="status-badge" data-status={listenbrainz.status}>
						{#if listenbrainz.status === 'checking'}
							<span class="status-spinner" aria-label="Checking"></span>
						{:else if listenbrainz.status === 'connected'}
							<svg viewBox="0 0 16 16" fill="currentColor" width="14" height="14"><path d="M13.5 3.5L6 11 2.5 7.5l-1 1L6 13l8.5-8.5z"/></svg>
						{/if}
						<span>{statusLabel[listenbrainz.status]}</span>
						{#if listenbrainz.username}<span class="status-user">· {listenbrainz.username}</span>{/if}
					</div>
				</div>
				{#if listenbrainz.status !== 'connected' && listenbrainz.status !== 'disabled'}
					<div class="card-body">
						<p class="card-instructions">
							Find your User Token at
							<a href="https://listenbrainz.org/profile/" target="_blank" rel="noopener">
								listenbrainz.org/profile
							</a>
							and paste it below.
						</p>
					<div class="credentials-form">
						<input
							class="token-input"
							type="text"
							placeholder="Username"
							bind:value={lbForm.username}
							autocomplete="off"
							spellcheck={false}
						/>
						<input
							class="token-input"
							type="password"
							placeholder="User token"
							bind:value={lbForm.token}
							onkeydown={(e) => e.key === 'Enter' && submitListenBrainzToken()}
							autocomplete="off"
							spellcheck={false}
						/>
					</div>
					{#if lbError}
						<p class="form-error" role="alert">{lbError}</p>
					{/if}
					<button
						class="btn btn-primary"
						onclick={submitListenBrainzToken}
						disabled={!lbForm.username.trim() || !lbForm.token.trim() || listenbrainz.status === 'checking'}
					>
						{listenbrainz.status === 'checking' ? 'Verifying…' : 'Connect'}
					</button>
					</div>
				{/if}
			</div>
		</section>

	</main>
</div>

{/if}

<style>
	/* ── Root & background ─────────────────────────────────────────────────── */
	.setup-root {
		min-height: 100dvh;
		display: grid;
		place-items: center;
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

	.setup-main {
		position: relative;
		z-index: 1;
		width: 100%;
		max-width: 520px;
		display: flex;
		flex-direction: column;
		gap: 0;
	}

	/* ── Relay main layout ─────────────────────────────────────────────────── */
	.relay-main {
		position: relative;
		z-index: 1;
		width: 100%;
		max-width: 560px;
		display: flex;
		flex-direction: column;
		gap: 1rem;
	}

	/* ── Header ────────────────────────────────────────────────────────────── */
	.setup-header {
		display: flex;
		align-items: center;
		gap: 1rem;
		margin-bottom: 2.5rem;
	}

	.logo-mark {
		display: flex;
		flex-direction: column;
		gap: 4px;
		padding: 10px 8px;
		border: 1px solid oklch(0.5 0.18 38 / 0.4);
		background: oklch(0.16 0.02 38 / 0.8);
		flex-shrink: 0;
	}

	.logo-signal {
		display: block;
		height: 3px;
		background: var(--primary);
		border-radius: 2px;
		animation: signal-pulse 1.8s ease-in-out infinite;
	}

	.logo-signal:nth-child(1) { width: 20px; animation-delay: 0s; }
	.logo-signal:nth-child(2) { width: 14px; animation-delay: 0.3s; }
	.logo-signal:nth-child(3) { width: 8px; animation-delay: 0.6s; }

	@keyframes signal-pulse {
		0%, 100% { opacity: 0.35; }
		50% { opacity: 1; }
	}

	.title-row {
		display: flex;
		align-items: baseline;
		gap: 1rem;
		flex-wrap: wrap;
	}

	.nav-link {
		font-size: 0.75rem;
		font-family: var(--font-heading);
		font-weight: 500;
		color: oklch(0.45 0.015 40);
		text-decoration: none;
		transition: color 0.15s;
	}
	.nav-link:hover { color: oklch(0.65 0.02 40); }

	.setup-title {
		font-family: var(--font-heading);
		font-size: 1.5rem;
		font-weight: 700;
		letter-spacing: -0.02em;
		color: oklch(0.95 0.01 60);
		line-height: 1;
		margin: 0 0 0.25rem;
	}

	.setup-subtitle {
		font-size: 0.8125rem;
		color: oklch(0.55 0.02 40);
		margin: 0;
	}

	/* ── Connected service pills ───────────────────────────────────────────── */
	.conn-pills {
		display: flex;
		flex-wrap: wrap;
		gap: 0.375rem;
		margin-top: 0.375rem;
	}

	.conn-pill {
		display: inline-flex;
		align-items: center;
		gap: 0.3rem;
		font-size: 0.6875rem;
		padding: 0.2rem 0.5rem;
		background: oklch(0.5 0.12 140 / 0.1);
		border: 1px solid oklch(0.5 0.12 140 / 0.3);
		color: oklch(0.65 0.12 140);
		font-variant-numeric: tabular-nums;
	}

	.lastfm-pill {
		background: oklch(0.5 0.18 28 / 0.1);
		border-color: oklch(0.5 0.18 28 / 0.3);
		color: oklch(0.65 0.18 28);
	}

	.lb-pill {
		background: oklch(0.4 0.15 280 / 0.1);
		border-color: oklch(0.4 0.15 280 / 0.3);
		color: oklch(0.6 0.12 280);
	}

	/* ── Relay cards ───────────────────────────────────────────────────────── */
	.relay-card {
		background: oklch(0.16 0.015 38);
		border: 1px solid oklch(0.22 0.02 38);
		display: flex;
		flex-direction: column;
	}

	.relay-card-label {
		font-size: 0.625rem;
		letter-spacing: 0.12em;
		text-transform: uppercase;
		color: oklch(0.38 0.01 40);
		padding: 0.625rem 1rem 0;
		font-family: var(--font-heading);
		font-weight: 700;
	}

	/* ── Source tabs ───────────────────────────────────────────────────────── */
	.source-tabs {
		display: flex;
		gap: 0;
		padding: 0.5rem 0.75rem 0;
		border-bottom: 1px solid oklch(0.2 0.01 40);
	}

	.source-tab {
		display: inline-flex;
		align-items: center;
		gap: 0.375rem;
		padding: 0.4rem 0.75rem;
		font-size: 0.8rem;
		font-family: var(--font-heading);
		font-weight: 500;
		color: oklch(0.45 0.015 40);
		background: transparent;
		border: none;
		border-bottom: 2px solid transparent;
		margin-bottom: -1px;
		cursor: pointer;
		transition: color 0.15s, border-color 0.15s;
	}

	.source-tab:hover {
		color: oklch(0.7 0.02 40);
	}

	.source-tab.tab-active {
		color: oklch(0.85 0.01 60);
		border-bottom-color: var(--primary);
	}

	/* ── Source config row ─────────────────────────────────────────────────── */
	.source-config {
		display: flex;
		align-items: center;
		gap: 0.625rem;
		padding: 0.875rem 1rem;
		flex-wrap: wrap;
	}

	.config-label {
		font-size: 0.8125rem;
		color: oklch(0.52 0.015 40);
		white-space: nowrap;
	}

	/* ── Relay select ──────────────────────────────────────────────────────── */
	.relay-select {
		background: oklch(0.12 0.01 38);
		border: 1px solid oklch(0.28 0.02 38);
		color: oklch(0.85 0.01 60);
		padding: 0.35rem 0.625rem;
		font-size: 0.8rem;
		font-family: var(--font-sans);
		outline: none;
		cursor: pointer;
		transition: border-color 0.2s;
	}

	.relay-select:focus {
		border-color: oklch(0.5 0.15 38 / 0.7);
	}

	/* ── Toggle group ──────────────────────────────────────────────────────── */
	.toggle-group {
		display: flex;
	}

	.toggle-btn {
		padding: 0.3rem 0.625rem;
		font-size: 0.8rem;
		font-family: var(--font-heading);
		font-weight: 500;
		background: oklch(0.12 0.01 38);
		border: 1px solid oklch(0.28 0.02 38);
		color: oklch(0.48 0.015 40);
		cursor: pointer;
		transition: background 0.15s, color 0.15s, border-color 0.15s;
	}

	.toggle-btn + .toggle-btn {
		border-left: none;
	}

	.toggle-btn.toggle-active {
		background: oklch(0.553 0.195 38 / 0.2);
		border-color: oklch(0.553 0.195 38 / 0.5);
		color: oklch(0.78 0.12 38);
	}

	/* ── Targets list ──────────────────────────────────────────────────────── */
	.targets-list {
		display: flex;
		flex-direction: column;
	}

	.target-row {
		display: flex;
		align-items: center;
		gap: 1rem;
		padding: 0.75rem 1rem;
		border-top: 1px solid oklch(0.2 0.01 40);
		transition: background 0.15s;
		flex-wrap: wrap;
	}

	.target-row.target-enabled {
		background: oklch(0.553 0.195 38 / 0.04);
	}

	.target-check {
		display: flex;
		align-items: center;
		gap: 0.5rem;
		cursor: pointer;
		font-size: 0.8125rem;
		font-family: var(--font-heading);
		font-weight: 500;
		color: oklch(0.6 0.015 40);
		user-select: none;
		min-width: 120px;
	}

	.target-check input[type="checkbox"] {
		accent-color: var(--primary);
		width: 14px;
		height: 14px;
		cursor: pointer;
	}

	.target-enabled .target-check {
		color: oklch(0.85 0.01 60);
	}

	.target-config {
		display: flex;
		align-items: center;
		gap: 0.5rem;
	}

	.target-hint {
		font-size: 0.75rem;
		color: oklch(0.35 0.01 40);
		font-style: italic;
	}

	.fixed-action {
		color: oklch(0.65 0.14 140);
		font-style: normal;
	}

	/* ── Run button ────────────────────────────────────────────────────────── */
	.run-btn {
		display: inline-flex;
		align-items: center;
		justify-content: center;
		gap: 0.5rem;
		padding: 0.75rem 1.75rem;
		font-size: 0.9375rem;
		font-weight: 700;
		font-family: var(--font-heading);
		letter-spacing: 0.01em;
		background: var(--primary);
		color: var(--primary-foreground);
		border: 1px solid var(--primary);
		cursor: pointer;
		transition: background 0.15s, opacity 0.15s;
		align-self: center;
		margin-top: 0.25rem;
	}

	.run-btn:hover:not(:disabled) {
		background: oklch(from var(--primary) calc(l + 0.05) c h);
	}

	.run-btn:disabled {
		opacity: 0.4;
		cursor: not-allowed;
	}

	/* ── Result panel ──────────────────────────────────────────────────────── */
	.relay-result {
		border: 1px solid oklch(0.28 0.01 40);
		background: oklch(0.16 0.015 38);
		padding: 0.875rem 1rem;
		display: flex;
		flex-direction: column;
		gap: 0.5rem;
		font-size: 0.8125rem;
		animation: slide-up 0.3s ease both;
	}

	.result-ok {
		border-color: oklch(0.45 0.12 140 / 0.5);
		background: oklch(0.55 0.14 140 / 0.06);
	}

	.result-partial {
		border-color: oklch(0.6 0.16 62 / 0.5);
		background: oklch(0.6 0.16 62 / 0.06);
	}

	.result-error {
		border-color: oklch(0.5 0.2 22 / 0.5);
		background: oklch(0.5 0.2 22 / 0.06);
		color: oklch(0.65 0.18 22);
		flex-direction: row;
		align-items: center;
		gap: 0.5rem;
	}

	.result-summary {
		display: flex;
		align-items: center;
		gap: 0.5rem;
		color: oklch(0.7 0.015 40);
	}

	.result-ok .result-summary { color: oklch(0.65 0.14 140); }
	.result-partial .result-summary { color: oklch(0.7 0.14 62); }
	.result-error .result-summary { color: oklch(0.65 0.18 22); }

	.result-service {
		display: flex;
		align-items: center;
		gap: 0.75rem;
		color: oklch(0.55 0.015 40);
		padding-left: 1.5rem;
	}

	.result-svc-name {
		font-family: var(--font-heading);
		font-weight: 600;
		color: oklch(0.68 0.015 40);
		min-width: 90px;
	}

	.result-stat {
		font-variant-numeric: tabular-nums;
		font-size: 0.75rem;
	}

	.result-ok-stat { color: oklch(0.65 0.14 140); }
	.result-err-stat { color: oklch(0.65 0.18 22); }

	/* ── Progress rail ─────────────────────────────────────────────────────── */
	.progress-rail {
		display: flex;
		align-items: center;
		gap: 0;
		margin-bottom: 2rem;
	}

	.progress-step {
		display: flex;
		flex-direction: column;
		align-items: center;
		gap: 6px;
		flex-shrink: 0;
	}

	.step-dot {
		width: 10px;
		height: 10px;
		border-radius: 50%;
		background: oklch(0.25 0.02 40);
		border: 1.5px solid oklch(0.32 0.02 40);
		transition: background 0.3s, border-color 0.3s;
	}

	.progress-step.active .step-dot {
		background: var(--primary);
		border-color: var(--primary);
		box-shadow: 0 0 8px var(--primary);
	}

	.progress-step.done .step-dot {
		background: oklch(0.6 0.15 140);
		border-color: oklch(0.6 0.15 140);
	}

	.step-label {
		font-size: 0.6875rem;
		letter-spacing: 0.05em;
		text-transform: uppercase;
		color: oklch(0.4 0.015 40);
		transition: color 0.3s;
	}

	.progress-step.active .step-label { color: oklch(0.75 0.1 38); }
	.progress-step.done .step-label { color: oklch(0.6 0.12 140); }

	.progress-line {
		flex: 1;
		height: 1px;
		background: oklch(0.25 0.01 40);
		margin-bottom: 16px;
		transition: background 0.4s;
	}

	.progress-line.filled {
		background: oklch(0.5 0.15 140);
	}

	/* ── Service card ──────────────────────────────────────────────────────── */
	.service-card {
		background: oklch(0.16 0.015 38);
		border: 1px solid oklch(0.22 0.02 38);
		margin-bottom: 1px;
		transition: border-color 0.3s, box-shadow 0.3s;
	}

	.service-card.card-active {
		border-color: oklch(0.45 0.15 38 / 0.6);
		box-shadow: 0 0 0 1px oklch(0.45 0.15 38 / 0.15);
	}

	.service-card.card-done {
		border-color: oklch(0.45 0.12 140 / 0.5);
	}

	.service-card.card-disabled {
		opacity: 0.45;
	}

	.card-header {
		display: flex;
		align-items: center;
		gap: 0.875rem;
		padding: 1rem 1.125rem;
	}

	.service-icon {
		width: 36px;
		height: 36px;
		display: grid;
		place-items: center;
		flex-shrink: 0;
		color: oklch(0.7 0.02 40);
	}

	.service-icon svg {
		width: 22px;
		height: 22px;
	}

	.plex-icon { color: oklch(0.78 0.18 62); }
	.lastfm-icon { color: oklch(0.62 0.22 28); }
	.lb-icon { color: oklch(0.65 0.18 320); }

	.service-info {
		flex: 1;
		min-width: 0;
	}

	.service-name {
		font-family: var(--font-heading);
		font-size: 0.9375rem;
		font-weight: 600;
		color: oklch(0.88 0.01 60);
		margin: 0 0 0.125rem;
		letter-spacing: -0.01em;
	}

	.service-desc {
		font-size: 0.75rem;
		color: oklch(0.48 0.015 40);
		margin: 0;
	}

	.status-badge {
		display: flex;
		align-items: center;
		gap: 0.375rem;
		font-size: 0.75rem;
		padding: 0.25rem 0.625rem;
		border: 1px solid oklch(0.28 0.01 40);
		color: oklch(0.5 0.015 40);
		flex-shrink: 0;
		font-variant-numeric: tabular-nums;
	}

	.status-badge[data-status="connected"] {
		border-color: oklch(0.45 0.12 140 / 0.5);
		color: oklch(0.65 0.14 140);
		background: oklch(0.55 0.14 140 / 0.08);
	}

	.status-badge[data-status="needs-auth"] {
		border-color: oklch(0.4 0.1 38 / 0.4);
		color: oklch(0.6 0.08 40);
	}

	.status-badge[data-status="checking"] {
		border-color: oklch(0.4 0.12 62 / 0.4);
		color: oklch(0.65 0.12 62);
	}

	.status-badge[data-status="error"] {
		border-color: oklch(0.5 0.2 22 / 0.4);
		color: oklch(0.65 0.18 22);
	}

	.status-user {
		color: oklch(0.45 0.01 40);
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
		to { transform: rotate(360deg); }
	}

	/* ── Card body ─────────────────────────────────────────────────────────── */
	.card-body {
		border-top: 1px solid oklch(0.2 0.01 40);
		padding: 1rem 1.125rem;
		display: flex;
		flex-direction: column;
		gap: 0.75rem;
	}

	.card-instructions {
		font-size: 0.8125rem;
		color: oklch(0.52 0.015 40);
		line-height: 1.55;
		margin: 0;
	}

	.card-instructions a {
		color: var(--primary);
		text-decoration: underline;
		text-underline-offset: 2px;
	}

	.pin-hint {
		font-size: 0.75rem;
		color: oklch(0.45 0.015 40);
		margin: 0;
		display: flex;
		align-items: center;
		gap: 0.4rem;
	}

	.pin-hint::before {
		content: '';
		display: inline-block;
		width: 6px;
		height: 6px;
		border-radius: 50%;
		background: oklch(0.65 0.15 62);
		animation: blink 1.2s ease-in-out infinite;
	}

	@keyframes blink {
		0%, 100% { opacity: 0.3; }
		50% { opacity: 1; }
	}

	/* ── Credentials form ──────────────────────────────────────────────────── */
	.credentials-form {
		display: flex;
		flex-direction: column;
		gap: 0.5rem;
		margin-bottom: 0.75rem;
	}

	.form-error {
		font-size: 0.8rem;
		color: oklch(0.65 0.18 25);
		margin: 0 0 0.5rem;
	}

	.token-input {
		flex: 1;
		min-width: 0;
		background: oklch(0.12 0.01 38);
		border: 1px solid oklch(0.28 0.02 38);
		color: oklch(0.85 0.01 60);
		padding: 0.5rem 0.75rem;
		font-size: 0.8125rem;
		font-family: ui-monospace, 'Cascadia Code', 'Fira Code', monospace;
		outline: none;
		transition: border-color 0.2s;
	}

	.token-input::placeholder {
		color: oklch(0.38 0.01 40);
	}

	.token-input:focus {
		border-color: oklch(0.5 0.15 38 / 0.7);
		box-shadow: 0 0 0 2px oklch(0.5 0.15 38 / 0.12);
	}

	/* ── Buttons ───────────────────────────────────────────────────────────── */
	.btn {
		display: inline-flex;
		align-items: center;
		justify-content: center;
		gap: 0.375rem;
		padding: 0.5rem 1rem;
		font-size: 0.8125rem;
		font-weight: 600;
		font-family: var(--font-heading);
		letter-spacing: 0.01em;
		cursor: pointer;
		border: 1px solid transparent;
		transition: background 0.15s, border-color 0.15s, opacity 0.15s;
		white-space: nowrap;
		align-self: flex-start;
	}

	.btn:disabled {
		opacity: 0.45;
		cursor: not-allowed;
	}

	.btn-primary {
		background: var(--primary);
		color: var(--primary-foreground);
		border-color: var(--primary);
	}

	.btn-primary:hover:not(:disabled) {
		background: oklch(from var(--primary) calc(l + 0.05) c h);
	}

	.btn-ghost {
		background: transparent;
		color: oklch(0.45 0.015 40);
		border-color: oklch(0.25 0.01 40);
		font-weight: 400;
		font-size: 0.75rem;
	}

	.btn-ghost:hover:not(:disabled) {
		background: oklch(0.2 0.01 38);
		color: oklch(0.6 0.02 40);
	}

	/* ── Music service group ───────────────────────────────────────────────── */
	.service-group {
		display: flex;
		flex-direction: column;
		gap: 0;
		opacity: 0.35;
		transition: opacity 0.4s;
		pointer-events: none;
		margin-top: 1.5rem;
	}

	.service-group.group-active {
		opacity: 1;
		pointer-events: auto;
	}

	.group-label {
		display: flex;
		align-items: baseline;
		gap: 0.5rem;
		margin-bottom: 0.625rem;
		font-size: 0.6875rem;
		letter-spacing: 0.08em;
		text-transform: uppercase;
		color: oklch(0.4 0.012 40);
	}

	.group-hint {
		color: oklch(0.32 0.01 40);
		font-size: 0.625rem;
	}

	.inner-card {
		margin-bottom: 1px;
	}

	/* ── Server select ─────────────────────────────────────────────────────── */
	.server-select-row {
		display: flex;
		gap: 0.5rem;
	}

	.server-select {
		flex: 1;
		min-width: 0;
		background: oklch(0.12 0.01 38);
		border: 1px solid oklch(0.28 0.02 38);
		color: oklch(0.85 0.01 60);
		padding: 0.5rem 0.75rem;
		font-size: 0.8125rem;
		font-family: var(--font-sans);
		outline: none;
		cursor: pointer;
		transition: border-color 0.2s;
	}

	.server-select:focus {
		border-color: oklch(0.5 0.15 38 / 0.7);
		box-shadow: 0 0 0 2px oklch(0.5 0.15 38 / 0.12);
	}

	@keyframes slide-up {
		from { opacity: 0; transform: translateY(8px); }
		to { opacity: 1; transform: translateY(0); }
	}

	/* ── Schedule section ──────────────────────────────────────────────────── */
	.schedule-row {
		display: flex;
		align-items: center;
		gap: 1rem;
		padding: 0.75rem 1rem;
		flex-wrap: wrap;
	}

	.schedule-toggle-label {
		display: flex;
		align-items: center;
		gap: 0.5rem;
		font-size: 0.8125rem;
		color: oklch(0.62 0.015 40);
		cursor: pointer;
		user-select: none;
	}

	.schedule-toggle-label input[type="checkbox"] {
		accent-color: var(--primary);
		width: 14px;
		height: 14px;
		cursor: pointer;
	}

	.schedule-freq {
		display: flex;
		align-items: center;
		gap: 0.5rem;
	}

	/* ── Jobs section ──────────────────────────────────────────────────────── */
	.jobs-section {
		display: flex;
		flex-direction: column;
		gap: 0;
		margin-top: 0.5rem;
	}

	.jobs-section-label {
		font-size: 0.625rem;
		letter-spacing: 0.12em;
		text-transform: uppercase;
		color: oklch(0.38 0.01 40);
		font-family: var(--font-heading);
		font-weight: 700;
		padding: 0.5rem 0 0.375rem;
		border-top: 1px solid oklch(0.2 0.01 40);
	}

	.job-card {
		display: block;
		text-decoration: none;
		color: inherit;
		background: oklch(0.16 0.015 38);
		border: 1px solid oklch(0.22 0.02 38);
		margin-bottom: 1px;
		animation: slide-up 0.25s ease both;
		transition: border-color 0.15s;
		cursor: pointer;
	}

	.job-card:hover {
		border-color: oklch(0.32 0.04 40);
	}

	.job-card[data-status="running"] {
		border-color: oklch(0.55 0.18 38 / 0.45);
	}

	.job-card[data-status="completed"] {
		border-color: oklch(0.45 0.12 140 / 0.4);
	}

	.job-card[data-status="partial"] {
		border-color: oklch(0.55 0.16 62 / 0.4);
	}

	.job-card[data-status="failed"] {
		border-color: oklch(0.5 0.2 22 / 0.4);
	}

	.job-card[data-status="cancelled"] {
		opacity: 0.5;
	}

	.job-card-top {
		display: flex;
		align-items: center;
		justify-content: space-between;
		gap: 1rem;
		padding: 0.7rem 1rem;
		flex-wrap: wrap;
	}

	.job-card-left {
		display: flex;
		align-items: center;
		gap: 0.625rem;
		min-width: 0;
		flex: 1;
	}

	.job-status-dot {
		width: 8px;
		height: 8px;
		border-radius: 50%;
		flex-shrink: 0;
	}

	.status-queued    { background: oklch(0.55 0.14 55); }
	.status-scheduled { background: oklch(0.52 0.12 240); }
	.status-running   {
		background: var(--primary);
		box-shadow: 0 0 6px var(--primary);
		animation: blink 1.2s ease-in-out infinite;
	}
	.status-completed { background: oklch(0.6 0.15 140); }
	.status-partial   { background: oklch(0.65 0.16 62); }
	.status-failed    { background: oklch(0.55 0.2 22); }
	.status-cancelled { background: oklch(0.35 0.01 40); }

	.job-card-info {
		display: flex;
		flex-direction: column;
		gap: 0.15rem;
		min-width: 0;
	}

	.job-title {
		font-size: 0.8125rem;
		font-family: var(--font-heading);
		font-weight: 500;
		color: oklch(0.78 0.015 50);
		white-space: nowrap;
		overflow: hidden;
		text-overflow: ellipsis;
	}

	.job-recur-hint {
		font-size: 0.7rem;
		color: oklch(0.45 0.012 40);
	}

	.job-card-right {
		display: flex;
		align-items: center;
		gap: 0.625rem;
		flex-shrink: 0;
	}

	.job-progress {
		display: flex;
		align-items: center;
		gap: 0.375rem;
		font-size: 0.75rem;
		font-variant-numeric: tabular-nums;
		color: oklch(0.62 0.015 40);
	}

	.job-status-text {
		font-size: 0.7rem;
		letter-spacing: 0.06em;
		text-transform: uppercase;
		color: oklch(0.42 0.01 40);
	}

	.job-done-stats {
		display: flex;
		align-items: center;
		gap: 0.5rem;
		font-size: 0.8rem;
		font-variant-numeric: tabular-nums;
	}

	.stat-ok  { color: oklch(0.65 0.14 140); }
	.stat-err { color: oklch(0.65 0.18 22); }

	.job-cancel-btn {
		padding: 0.2rem 0.5rem;
		font-size: 0.7rem;
		font-family: var(--font-heading);
		font-weight: 500;
		background: transparent;
		border: 1px solid oklch(0.3 0.01 40);
		color: oklch(0.45 0.01 40);
		cursor: pointer;
		transition: border-color 0.15s, color 0.15s;
	}

	.job-cancel-btn:hover {
		border-color: oklch(0.5 0.18 22 / 0.6);
		color: oklch(0.65 0.18 22);
	}

	/* ── Live event list ───────────────────────────────────────────────────── */
	.job-events-list {
		border-top: 1px solid oklch(0.2 0.01 40);
		max-height: 200px;
		overflow-y: auto;
		padding: 0.375rem 0;
	}

	.job-event-row {
		display: flex;
		align-items: baseline;
		gap: 0.375rem;
		padding: 0.175rem 1rem;
		font-size: 0.75rem;
		color: oklch(0.55 0.015 40);
		font-variant-numeric: tabular-nums;
		flex-wrap: nowrap;
		white-space: nowrap;
		overflow: hidden;
		text-overflow: ellipsis;
	}

	.job-event-row.event-err {
		color: oklch(0.52 0.015 40);
	}

	.event-icon {
		font-size: 0.65rem;
		flex-shrink: 0;
		color: oklch(0.6 0.14 140);
	}

	.event-err .event-icon {
		color: oklch(0.6 0.18 22);
	}

	.event-track {
		overflow: hidden;
		text-overflow: ellipsis;
		min-width: 0;
		flex: 1;
		color: oklch(0.65 0.015 50);
	}

	.event-arrow {
		color: oklch(0.35 0.01 40);
		flex-shrink: 0;
	}

	.event-target {
		flex-shrink: 0;
		color: oklch(0.48 0.01 40);
	}

	.event-error-msg {
		flex-shrink: 0;
		color: oklch(0.55 0.15 22);
		font-style: italic;
		max-width: 160px;
		overflow: hidden;
		text-overflow: ellipsis;
	}
</style>
