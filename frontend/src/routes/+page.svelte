<script lang="ts">
	import { untrack } from 'svelte';
	import { resolve } from '$app/paths';
	import type { PageData } from './$types';
	import type {
		PlexAuthState,
		LastFMAuthState,
		ListenBrainzAuthState,
		PlexServerItem,
		SetupStep
	} from '$lib/types/auth';
	import type { JobData } from '$lib/types/jobs';
	import type {
		ListenBrainzSourceConfig,
		ListenBrainzTargetConfig,
		PlexSourceConfig,
		PlexTargetConfig,
		SourceId,
		SourceOption,
		TargetOption
	} from '$lib/types/relay';
	import SourceNode from '$lib/components/flow/SourceNode.svelte';
	import TargetNode from '$lib/components/flow/TargetNode.svelte';
	import FlowLink from '$lib/components/flow/FlowLink.svelte';
	import RunDock from '$lib/components/flow/RunDock.svelte';
	import JobsRail from '$lib/components/jobs/JobsRail.svelte';
	import ServiceMark from '$lib/components/flow/ServiceMark.svelte';

	let { data }: { data: PageData } = $props();

	// ── Service state ─────────────────────────────────────────────────────────
	let plex = $state<PlexAuthState>(untrack(() => ({ ...data.authStatus.plex })));
	let lastfm = $state<LastFMAuthState>(untrack(() => ({ ...data.authStatus.lastfm })));
	let listenbrainz = $state<ListenBrainzAuthState>(
		untrack(() => ({ ...data.authStatus.listenbrainz }))
	);

	// True once at least one music service is connected (or deliberately off).
	let musicOk = $derived(
		lastfm.status === 'connected' ||
			listenbrainz.status === 'connected' ||
			lastfm.status === 'disabled' ||
			listenbrainz.status === 'disabled'
	);

	// ── Wizard step (derived) ─────────────────────────────────────────────────
	let step: SetupStep = $derived.by(() => {
		if (plex.status !== 'connected') return 'plex';
		if (!plex.server_name) return 'server-select';
		if (!musicOk) return 'music-services';
		return 'done';
	});

	// ── Plex OAuth popup flow ─────────────────────────────────────────────────
	// The popup redirects to our callback, which stores the token and tells us.
	// We do not rely on that message: in dev the popup is served by the API on a
	// different origin than the Vite UI, so the browser drops it and the wizard
	// sits there until a manual refresh. Polling the status endpoint is the
	// signal, and the message is only a shortcut.
	let plexPopup: Window | null = null;
	let plexTimer: ReturnType<typeof setInterval> | undefined;

	async function startPlexAuth() {
		plex.status = 'checking';
		try {
			const res = await fetch('/api/plex/auth/init', { method: 'POST' });
			if (!res.ok) throw new Error(await res.text());
			const { oauth_url }: { pin_id: number; oauth_url: string } = await res.json();

			const w = 800,
				h = 700;
			const left = Math.round(window.screenX + (window.outerWidth - w) / 2);
			const top = Math.round(window.screenY + (window.outerHeight - h) / 2);
			plexPopup = window.open(
				oauth_url,
				'plex-oauth',
				`width=${w},height=${h},left=${left},top=${top},toolbar=0,menubar=0`
			);

			plex.status = 'needs-auth';
			waitForPlexAuth();
		} catch {
			plex.status = 'error';
		}
	}

	/** Poll until the callback has stored a token, the popup closes, or we give up. */
	function waitForPlexAuth(deadline = Date.now() + 120_000) {
		clearInterval(plexTimer);
		plexTimer = setInterval(async () => {
			const done = !plexPopup || plexPopup.closed;
			const res = await fetch('/api/plex/auth/status').catch(() => null);
			const body = res?.ok ? ((await res.json()) as { status: string; username?: string }) : null;

			if (body?.status === 'connected') {
				clearInterval(plexTimer);
				plexPopup?.close();
				plexPopup = null;
				await refreshPlexStatus();
				return;
			}
			if (done || Date.now() > deadline) {
				clearInterval(plexTimer);
				plexPopup = null;
				plex.status = 'needs-auth';
			}
		}, 2000);
	}

	function handleMessage(event: MessageEvent) {
		if (event.data !== 'plex:authed') return;
		// Any origin will do: the message carries no data, and the status endpoint
		// — not this event — is what decides whether we are connected.
		plexPopup?.close();
		plexPopup = null;
		clearInterval(plexTimer);
		refreshPlexStatus();
	}

	async function refreshPlexStatus() {
		plex.status = 'checking';
		try {
			const res = await fetch('/api/plex/auth/status');
			if (!res.ok) {
				plex.status = 'needs-auth';
				return;
			}
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
		return () => {
			window.removeEventListener('message', handleMessage);
			clearInterval(plexTimer);
		};
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
				body: JSON.stringify({ name: selectedServer })
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
		if (
			!lastfmForm.api_key ||
			!lastfmForm.api_secret ||
			!lastfmForm.username ||
			!lastfmForm.password
		)
			return;
		lastfm.status = 'checking';
		lastfmError = '';
		try {
			const res = await fetch('/api/lastfm/auth', {
				method: 'POST',
				headers: { 'Content-Type': 'application/json' },
				body: JSON.stringify(lastfmForm)
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
				body: JSON.stringify(lbForm)
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
		error: 'Error'
	};

	// ── Relay home state ──────────────────────────────────────────────────────

	let selectedSource = $state<SourceId>('plex');
	let resetTargetService = $state<SourceId>('plex');

	let plexSourceConfig = $state<PlexSourceConfig>({ ratingThreshold: 8, comparison: 'gte' });
	let lbzSourceConfig = $state<ListenBrainzSourceConfig>({ feedbackType: 'love' });

	let plexTargetEnabled = $state(false);
	let plexTargetConfig = $state<PlexTargetConfig>({ rating: 10 });
	let lastfmTargetEnabled = $state(false);
	let lbzTargetEnabled = $state(false);
	let lbzTargetConfig = $state<ListenBrainzTargetConfig>({ feedbackType: 'love' });

	/** Whether the ghost list under the last node is showing. */
	let showTargetPicker = $state(false);

	/** The jobs rail opens itself when a run is queued. */
	let jobsOpen = $state(true);

	let relaying = $state(false);
	let relayError = $state('');

	/** The nodes above the rail: one per connected service, plus the reset. */
	const sourceOptions = $derived<SourceOption[]>([
		...(plex.status === 'connected' && plex.server_name
			? [{ id: 'plex' as const, name: 'Plex' }]
			: []),
		...(lastfm.status === 'connected' ? [{ id: 'lastfm' as const, name: 'Last.fm' }] : []),
		...(listenbrainz.status === 'connected'
			? [{ id: 'listenbrainz' as const, name: 'ListenBrainz' }]
			: []),
		{ id: 'reset' as const, name: 'Reset' }
	]);

	const targetCatalog: TargetOption[] = [
		{ id: 'plex', name: 'Plex', hint: 'rate the library' },
		{ id: 'lastfm', name: 'Last.fm', hint: 'love the track' },
		{ id: 'listenbrainz', name: 'ListenBrainz', hint: 'love or hate' }
	];

	function isAllocated(id: string): boolean {
		if (id === 'plex') return plexTargetEnabled;
		if (id === 'lastfm') return lastfmTargetEnabled;
		return lbzTargetEnabled;
	}

	function setAllocated(id: string, on: boolean) {
		if (id === 'plex') plexTargetEnabled = on;
		else if (id === 'lastfm') lastfmTargetEnabled = on;
		else lbzTargetEnabled = on;
	}

	/** Targets the flow may offer: connected, and never the source itself. */
	const availableTargets = $derived(
		targetCatalog.filter((t) => {
			if (t.id === selectedSource) return false;
			if (t.id === 'plex') return plex.status === 'connected' && !!plex.server_name;
			if (t.id === 'lastfm') return lastfm.status === 'connected';
			return listenbrainz.status === 'connected';
		})
	);

	const allocatedTargets = $derived(availableTargets.filter((t) => isAllocated(t.id)));
	const ghostTargets = $derived(availableTargets.filter((t) => !isAllocated(t.id)));

	/** Services a reset can clear: the ones we hold credentials for. */
	const resetOptions = $derived<SourceOption[]>([
		...(plex.status === 'connected' ? [{ id: 'plex' as const, name: 'Plex' }] : []),
		...(lastfm.status === 'connected' ? [{ id: 'lastfm' as const, name: 'Last.fm' }] : []),
		...(listenbrainz.status === 'connected'
			? [{ id: 'listenbrainz' as const, name: 'ListenBrainz' }]
			: [])
	]);

	// Keep the selection legal when a service disconnects or the source changes.
	$effect(() => {
		if (!sourceOptions.some((o) => o.id === selectedSource)) {
			selectedSource = sourceOptions[0].id;
		}
		if (!resetOptions.some((o) => o.id === resetTargetService) && resetOptions.length > 0) {
			resetTargetService = resetOptions[0].id;
		}
		for (const id of ['plex', 'lastfm', 'listenbrainz']) {
			if (id === selectedSource && isAllocated(id)) setAllocated(id, false);
		}
	});

	let canRelay = $derived(
		!relaying &&
			(selectedSource === 'reset' ? resetOptions.length > 0 : allocatedTargets.length > 0)
	);

	function buildRelayPayload() {
		let source: object;
		if (selectedSource === 'plex') {
			source = {
				service: 'plex',
				rating_threshold: plexSourceConfig.ratingThreshold,
				comparison: plexSourceConfig.comparison
			};
		} else if (selectedSource === 'lastfm') {
			source = { service: 'lastfm' };
		} else if (selectedSource === 'listenbrainz') {
			source = { service: 'listenbrainz', feedback_type: lbzSourceConfig.feedbackType };
		} else {
			source = { service: 'reset', target_service: resetTargetService };
		}

		if (selectedSource === 'reset') {
			return { source, targets: [] };
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

	// ── Job queue state ───────────────────────────────────────────────────────

	/** Minutes between runs; 0 means a one-off. */
	let repeat = $state(0);
	let dryRun = $state(false);

	$effect(() => {
		if (repeat > 0) dryRun = false;
	});

	let jobs = $state<JobData[]>([]);

	let canQueue = $derived(canRelay);

	async function queueJob() {
		if (!canQueue) return;
		relaying = true;
		relayError = '';
		try {
			const payload = {
				...buildRelayPayload(),
				recurring: repeat > 0,
				interval_minutes: repeat > 0 ? repeat : undefined,
				dry_run: dryRun
			};
			const res = await fetch('/api/relay/jobs', {
				method: 'POST',
				headers: { 'Content-Type': 'application/json' },
				body: JSON.stringify(payload)
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
			jobsOpen = true;
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
			jobs = jobs.map((j) => (j.id === jobId ? { ...j, ...updated } : j));
		} catch {
			// silently ignore
		}
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
	<!-- Home: the pipeline as a flow of nodes, the queue as its own rail. -->
	<div class="flex h-dvh w-full flex-col overflow-hidden bg-background text-foreground">
		<header class="flex shrink-0 items-center gap-4 border-b border-border/70 px-5 py-2.5">
			<div class="flex items-center gap-2">
				<span class="grid size-7 place-items-center rounded-md bg-primary text-primary-foreground">
					<svg
						viewBox="0 0 16 16"
						width="15"
						height="15"
						fill="none"
						stroke="currentColor"
						stroke-width="1.7"
						stroke-linecap="round"
						aria-hidden="true"
					>
						<path d="M3 12V8m5 4V3m5 9V6" />
					</svg>
				</span>
				<h1 class="font-heading text-lg font-semibold tracking-tight">RatingRelay</h1>
			</div>

			<div class="flex flex-wrap items-center gap-2">
				{#if plex.status === 'connected'}
					<span class="conn-pill">
						<ServiceMark service="plex" size={13} />
						{plex.server_name ?? 'Plex'}
					</span>
				{/if}
				{#if lastfm.status === 'connected'}
					<span class="conn-pill">
						<ServiceMark service="lastfm" size={13} />
						Last.fm · {lastfm.username}
					</span>
				{/if}
				{#if listenbrainz.status === 'connected'}
					<span class="conn-pill">
						<ServiceMark service="listenbrainz" size={13} />
						ListenBrainz · {listenbrainz.username}
					</span>
				{/if}
			</div>

			<a
				href={resolve('/unmatched')}
				class="ml-auto text-base text-muted-foreground underline-offset-4 hover:text-foreground hover:underline"
			>
				Unmatched tracks
			</a>
		</header>

		{#if relayError}
			<div
				class="border-b border-destructive/30 bg-destructive/10 px-5 py-2 text-base text-destructive"
			>
				{relayError}
			</div>
		{/if}

		<div class="flex min-h-0 flex-1">
			<section class="flex min-h-0 flex-1 flex-col" aria-label="Relay flow">
				<div class="flow-canvas min-h-0 flex-1 overflow-y-auto">
					<div class="mx-auto flex w-full max-w-4xl flex-col px-8 py-10">
						<SourceNode
							options={sourceOptions}
							bind:selected={selectedSource}
							plexConfig={plexSourceConfig}
							lbzConfig={lbzSourceConfig}
							bind:resetService={resetTargetService}
							{resetOptions}
						/>

						{#if selectedSource !== 'reset'}
							<FlowLink />

							{#if allocatedTargets.length > 0}
								<div class="space-y-3.5">
									{#each allocatedTargets as target (target.id)}
										<TargetNode
											option={target}
											allocated={true}
											onadd={() => {}}
											onremove={() => setAllocated(target.id, false)}
											plexConfig={target.id === 'plex' ? plexTargetConfig : undefined}
											lbzConfig={target.id === 'listenbrainz' ? lbzTargetConfig : undefined}
										/>
									{/each}
								</div>
							{/if}

							{#if ghostTargets.length > 0}
								{#if allocatedTargets.length === 0}
									<p class="mb-3 text-center text-base text-muted-foreground">
										Where should these ratings go?
									</p>
								{:else}
									<FlowLink
										onadd={() => (showTargetPicker = !showTargetPicker)}
										active={showTargetPicker}
									/>
								{/if}

								{#if allocatedTargets.length === 0 || showTargetPicker}
									<div class="space-y-2.5">
										{#each ghostTargets as target (target.id)}
											<TargetNode
												option={target}
												allocated={false}
												onadd={() => {
													setAllocated(target.id, true);
													showTargetPicker = false;
												}}
												onremove={() => {}}
											/>
										{/each}
									</div>
								{/if}
							{/if}
						{/if}
					</div>
				</div>

				<RunDock
					bind:repeat
					bind:dryRun
					mode={selectedSource === 'reset' ? 'reset' : 'run'}
					busy={relaying}
					canRun={canQueue}
					onrun={queueJob}
				/>
			</section>

			<JobsRail {jobs} oncancel={cancelJob} bind:open={jobsOpen} />
		</div>
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
					class:done={musicOk}
					role="listitem"
				>
					<span class="step-dot"></span>
					<span class="step-label">Music</span>
				</div>
				<div class="progress-line" class:filled={musicOk}></div>
				<div class="progress-step" class:done={musicOk} role="listitem">
					<span class="step-dot"></span>
					<span class="step-label">Ready</span>
				</div>
			</div>

			<!-- ── Step: Plex ──────────────────────────────────────── -->
			<section
				class="service-card"
				class:card-active={step === 'plex'}
				class:card-done={plex.status === 'connected'}
			>
				<div class="card-header">
					<div class="service-icon plex-icon" aria-hidden="true">
						<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 512 512"
							><rect width="512" height="512" rx="15%" fill="#282a2d" /><path
								d="M256 70H148l108 186-108 186h108l108-186z"
								fill="#e5a00d"
							/></svg
						>
					</div>
					<div class="service-info">
						<h2 class="service-name">Plex</h2>
						<p class="service-desc">Source of your media ratings</p>
					</div>
					<div class="status-badge" data-status={plex.status}>
						{#if plex.status === 'checking'}
							<span class="status-spinner" aria-label="Checking"></span>
						{:else if plex.status === 'connected'}
							<svg viewBox="0 0 16 16" fill="currentColor" width="14" height="14"
								><path d="M13.5 3.5L6 11 2.5 7.5l-1 1L6 13l8.5-8.5z" /></svg
							>
						{:else if plex.status === 'error'}
							<svg viewBox="0 0 16 16" fill="currentColor" width="14" height="14"
								><path
									d="M8 1L1 14h14L8 1zm0 4v4m0 2v1"
									stroke="currentColor"
									stroke-width="1.5"
									fill="none"
								/></svg
							>
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
							<button
								class="btn btn-primary"
								onclick={startPlexAuth}
								disabled={plex.status === 'checking'}
							>
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
						<svg
							viewBox="0 0 24 24"
							fill="none"
							stroke="currentColor"
							stroke-width="1.5"
							xmlns="http://www.w3.org/2000/svg"
						>
							<rect x="2" y="3" width="20" height="5" rx="1" />
							<rect x="2" y="10" width="20" height="5" rx="1" />
							<rect x="2" y="17" width="20" height="5" rx="1" />
							<circle cx="18" cy="5.5" r="1" fill="currentColor" stroke="none" />
							<circle cx="18" cy="12.5" r="1" fill="currentColor" stroke="none" />
							<circle cx="18" cy="19.5" r="1" fill="currentColor" stroke="none" />
						</svg>
					</div>
					<div class="service-info">
						<h2 class="service-name">Plex server</h2>
						<p class="service-desc">Choose which server to relay ratings from</p>
					</div>
					{#if plex.server_name}
						<div class="status-badge" data-status="connected">
							<svg viewBox="0 0 16 16" fill="currentColor" width="14" height="14"
								><path d="M13.5 3.5L6 11 2.5 7.5l-1 1L6 13l8.5-8.5z" /></svg
							>
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
							<p class="card-instructions">
								Select the server RatingRelay should read ratings from.
							</p>
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
								<p class="card-instructions" style:color="oklch(0.65 0.18 22)" role="alert">
									{serverError}
								</p>
							{/if}
						{/if}
					</div>
				{/if}
			</section>

			<!-- ── Step: Music Services ────────────────────────────── -->
			<section class="service-group" class:group-active={step === 'music-services'}>
				<div class="group-label">
					<span>Music services</span>
					<span class="group-hint">Connect at least one</span>
				</div>

				<!-- LastFM -->
				<div
					class="service-card inner-card"
					class:card-done={lastfm.status === 'connected'}
					class:card-disabled={lastfm.status === 'disabled'}
				>
					<div class="card-header">
						<div class="service-icon lastfm-icon" aria-hidden="true">
							<svg viewBox="0 0 24 24" fill="currentColor" xmlns="http://www.w3.org/2000/svg">
								<path
									d="M10.584 17.21l-.88-2.392s-1.43 1.6-3.573 1.6c-1.898 0-3.244-1.65-3.244-4.29 0-3.38 1.703-4.594 3.38-4.594 2.42 0 3.19 1.565 3.85 3.576l.88 2.75c.88 2.673 2.53 4.815 7.315 4.815 3.41 0 5.73-1.045 5.73-3.8 0-2.227-1.265-3.38-3.63-3.93l-1.76-.385c-1.21-.275-1.57-.77-1.57-1.593 0-.935.737-1.483 1.95-1.483 1.318 0 2.03.494 2.14 1.67l2.75-.33c-.22-2.47-1.925-3.48-4.755-3.48-2.49 0-4.82.935-4.82 3.93 0 1.87.907 3.05 3.19 3.6l1.87.44c1.375.33 1.87.88 1.87 1.76 0 1.046-.99 1.483-2.862 1.483-2.75 0-3.9-1.43-4.562-3.38l-.91-2.75c-1.155-3.52-3-4.87-6.655-4.87C1.87 5.528 0 8.278 0 12.238c0 3.82 1.87 6.234 6.04 6.234 3.135 0 4.544-1.262 4.544-1.262z"
								/>
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
								<svg viewBox="0 0 16 16" fill="currentColor" width="14" height="14"
									><path d="M13.5 3.5L6 11 2.5 7.5l-1 1L6 13l8.5-8.5z" /></svg
								>
							{/if}
							<span>{statusLabel[lastfm.status]}</span>
							{#if lastfm.username}<span class="status-user">· {lastfm.username}</span>{/if}
						</div>
					</div>
					{#if lastfm.status !== 'connected' && lastfm.status !== 'disabled'}
						<div class="card-body">
							<p class="card-instructions">
								Get your API key and secret from
								<a href="https://www.last.fm/api/account/create" target="_blank" rel="noopener"
									>last.fm/api/account/create</a
								>, then enter your Last.fm credentials below.
							</p>
							<div class="credentials-form">
								<input
									class="token-input"
									type="text"
									placeholder="API Key"
									bind:value={lastfmForm.api_key}
									autocomplete="off"
									spellcheck={false}
								/>
								<input
									class="token-input"
									type="password"
									placeholder="API Secret"
									bind:value={lastfmForm.api_secret}
									autocomplete="off"
									spellcheck={false}
								/>
								<input
									class="token-input"
									type="text"
									placeholder="Username"
									bind:value={lastfmForm.username}
									autocomplete="off"
									spellcheck={false}
								/>
								<input
									class="token-input"
									type="password"
									placeholder="Password"
									bind:value={lastfmForm.password}
									autocomplete="current-password"
									onkeydown={(e) => e.key === 'Enter' && submitLastFMAuth()}
								/>
							</div>
							{#if lastfmError}
								<p class="form-error" role="alert">{lastfmError}</p>
							{/if}
							<button
								class="btn btn-primary"
								onclick={submitLastFMAuth}
								disabled={lastfm.status === 'checking' ||
									!lastfmForm.api_key ||
									!lastfmForm.api_secret ||
									!lastfmForm.username ||
									!lastfmForm.password}
							>
								{lastfm.status === 'checking' ? 'Connecting…' : 'Connect Last.fm'}
							</button>
						</div>
					{/if}
				</div>

				<!-- ListenBrainz -->
				<div
					class="service-card inner-card"
					class:card-done={listenbrainz.status === 'connected'}
					class:card-disabled={listenbrainz.status === 'disabled'}
				>
					<div class="card-header">
						<div class="service-icon lb-icon" aria-hidden="true">
							<svg
								id="svg1591"
								viewBox="9,0,128,160"
								xmlns="http://www.w3.org/2000/svg"
								xmlns:xlink="http://www.w3.org/1999/xlink"
							>
								<defs id="defs1552">
									<path id="a" d="m75.354 33.239h51.433v90.792h-51.433z" />
									<clipPath id="b">
										<use id="use1554" xlink:href="#a" />
									</clipPath>
								</defs>
								<metadata id="metadata1597">
									<rdf:RDF>
										<cc:Work rdf:about="">
											<dc:format>image/svg+xml</dc:format>
											<dc:type rdf:resource="http://purl.org/dc/dcmitype/StillImage" />
											<dc:title>ListenBrainz 2020 logo (version 2)</dc:title>
										</cc:Work>
									</rdf:RDF>
								</metadata>
								<sodipodi:namedview
									bordercolor="#666666"
									borderopacity="1"
									gridtolerance="10"
									guidetolerance="10"
									inkscape:current-layer="svg1591"
									inkscape:cx="64"
									inkscape:cy="80"
									inkscape:pageopacity="0"
									inkscape:pageshadow="2"
									inkscape:window-height="1014"
									inkscape:window-maximized="1"
									inkscape:window-width="1920"
									inkscape:window-x="0"
									inkscape:window-y="36"
									inkscape:zoom="5.24375"
									objecttolerance="10"
									pagecolor="#ffffff"
									showgrid="false"
								/>
								<path id="path1545" d="m75.354 7.823v144l61-35v-74z" fill="#eb743b" />
								<title id="title1547">ListenBrainz 2020 logo (version 2)</title>
								<path id="path1549" d="m70.354 7.823-61 35v74l61 35z" fill="#353070" />
								<g id="g1563" opacity=".07">
									<path
										id="path1557"
										clip-path="url(#b)"
										d="m92.657 99.557a2.1 2.1 0 0 1 1.819-1.03c.378 0 .752.104 1.078.297a2.12 2.12 0 0 1 .735 2.898 2.097 2.097 0 0 1 -1.817 1.032c-.379 0-.755-.105-1.084-.301a2.092 2.092 0 0 1 -.964-1.294 2.1 2.1 0 0 1 .233-1.602m12.602 22.362a2.121 2.121 0 0 1 -2.981-.225 2.116 2.116 0 0 1 .231-2.981 2.08 2.08 0 0 1 1.373-.512 2.117 2.117 0 0 1 2.109 2.278 2.086 2.086 0 0 1 -.732 1.44m-9.525-58.839a2.128 2.128 0 0 1 2.916-.656 2.12 2.12 0 0 1 .66 2.919 2.115 2.115 0 0 1 -2.919.661 2.128 2.128 0 0 1 -.657-2.924m6.379-20.187c-.6.951-1.937 1.271-2.912.653l-.096-.062a2.107 2.107 0 0 1 -.557-2.851 2.093 2.093 0 0 1 1.784-.98 2.11 2.11 0 0 1 1.781 3.24m16.767 11.598a2.096 2.096 0 0 1 1.783-.98c.4 0 .79.113 1.125.325.481.304.813.775.937 1.33a2.08 2.08 0 0 1 -.28 1.584c-.597.951-1.938 1.273-2.912.653l-.093-.062a2.105 2.105 0 0 1 -.56-2.85m0 23.463a2.096 2.096 0 0 1 1.783-.98c.4 0 .79.112 1.125.326.481.304.813.774.937 1.329a2.083 2.083 0 0 1 -.28 1.585c-.597.95-1.938 1.271-2.912.652l-.093-.062a2.105 2.105 0 0 1 -.56-2.85m0 26.303a2.099 2.099 0 0 1 1.783-.98c.4 0 .79.113 1.125.326.481.304.813.775.937 1.33a2.08 2.08 0 0 1 -.28 1.584c-.597.951-1.938 1.271-2.912.653l-.093-.062a2.107 2.107 0 0 1 -.56-2.851"
									/>
									<path
										id="path1559"
										clip-path="url(#b)"
										d="m108.53 113.927a6.11 6.11 0 0 0 -8.631-.662 6.12 6.12 0 0 0 -2.087 5.396c-1.949.701-4.258 1.366-5.328 1.332-.822-.043-1.302-.345-2.224-.971-1.172-.795-2.418-1.512-4.149-1.847 2.936-2.761 6.009-6.83 8.584-12.834a6.15 6.15 0 0 0 5.039-2.972c1.724-2.896.773-6.659-2.124-8.394a6.133 6.133 0 0 0 -3.134-.864 6.154 6.154 0 0 0 -5.261 2.986 6.079 6.079 0 0 0 -.678 4.636 6.075 6.075 0 0 0 2.302 3.429c-4.053 9.218-9.219 13.08-12.565 14.685a2.016 2.016 0 0 0 -.361.168c-1.186.531-2.05.704-2.558.808v4c.275-.03 1.884-.118 4.247-1.192 5.347-1.357 6.839-.354 8.407.709 1.021.692 2.291 1.554 4.263 1.655.099.006.204.009.308.009 2.11 0 5.172-1.022 7.021-1.723a6.132 6.132 0 0 0 4.285 1.75 6.112 6.112 0 0 0 3.983-1.476 6.072 6.072 0 0 0 2.119-4.176 6.072 6.072 0 0 0 -1.458-4.452m-15.873-16.776a2.114 2.114 0 0 1 3.632 2.165 2.097 2.097 0 0 1 -1.817 1.032c-.379 0-.755-.105-1.084-.301a2.092 2.092 0 0 1 -.964-1.294 2.101 2.101 0 0 1 .233-1.602m12.602 22.363a2.121 2.121 0 0 1 -2.981-.225 2.116 2.116 0 0 1 .231-2.981 2.08 2.08 0 0 1 1.373-.512 2.117 2.117 0 0 1 2.109 2.278 2.086 2.086 0 0 1 -.732 1.44m1.045-81.487a6.088 6.088 0 0 0 -2.708-3.844 6.12 6.12 0 0 0 -3.264-.944 6.076 6.076 0 0 0 -5.174 2.85 6.108 6.108 0 0 0 .144 6.758c-3.646 4.124-8.707 4.314-9.943 4.293-3.692-1.604-7.234-2.164-10.005-2.317v3.996c2.442.144 5.553.686 8.748 2.136.005.002.01.002.015.005 3.445 1.562 6.334 3.927 8.607 7.041a6.364 6.364 0 0 0 -.381.538c-1.796 2.854-.943 6.643 1.903 8.444.98.622 2.111.951 3.269.951a6.105 6.105 0 0 0 5.183-2.851c1.806-2.851.951-6.642-1.908-8.453-1.384-.872-3.092-1.114-4.667-.755-1.538-2.155-3.323-4.017-5.345-5.567 2.517-.778 5.401-2.268 7.78-5.095a6.18 6.18 0 0 0 1.77.266 6.08 6.08 0 0 0 5.172-2.849 6.05 6.05 0 0 0 .804-4.603m-10.57 22.647a2.128 2.128 0 0 1 2.916-.656 2.12 2.12 0 0 1 .66 2.919 2.115 2.115 0 0 1 -2.919.661 2.128 2.128 0 0 1 -.657-2.924m6.379-20.187c-.6.951-1.937 1.271-2.912.653l-.096-.062a2.107 2.107 0 0 1 -.557-2.851 2.093 2.093 0 0 1 1.784-.98 2.11 2.11 0 0 1 1.781 3.24"
									/>
									<path
										id="path1561"
										clip-path="url(#b)"
										d="m115 79.011a6.036 6.036 0 0 0 2.11 2.649 6.098 6.098 0 0 0 3.55 1.14 6.083 6.083 0 0 0 5.173-2.848 6.064 6.064 0 0 0 .805-4.605 6.103 6.103 0 0 0 -2.709-3.844 6.128 6.128 0 0 0 -3.266-.944c-.125 0-.25.008-.375.018-.71-3.158-.919-7.769.035-11.265.113.011.225.024.337.024a6.08 6.08 0 0 0 5.173-2.848 6.062 6.062 0 0 0 .805-4.604 6.103 6.103 0 0 0 -2.709-3.844 6.128 6.128 0 0 0 -3.266-.944 6.079 6.079 0 0 0 -5.173 2.85 6.104 6.104 0 0 0 1.025 7.764c.021.02.048.037.071.059-1.349 4.518-1.145 10.324-.036 14.417-.144.132-.294.254-.426.399a6.327 6.327 0 0 0 -.634.825 6.2 6.2 0 0 0 -.71 1.578c-3.371.488-5.198 1.671-6.824 2.729-2.035 1.324-3.961 2.572-9.636 2.666-1.963-.23-3.552-.178-5.29-.116-1.43.052-2.91.105-4.848.003-1.335-.071-2.104-.882-3.458-2.424-1.803-2.055-4.219-4.648-9.369-5.022v4c3.295.299 4.796 1.887 6.356 3.667 1.487 1.694 3.175 3.615 6.256 3.782 2.118.114 3.76.052 5.208.001 2.491-.092 4.453-.161 7.855.683 2.608.644 12.099 9.674 14.774 14.323-.109.142-.219.282-.314.434-1.743 2.755-1.026 6.385 1.62 8.251.072.052.151.104.283.191.981.621 2.11.948 3.267.948a6.081 6.081 0 0 0 5.173-2.849 6.06 6.06 0 0 0 .805-4.603 6.1 6.1 0 0 0 -2.709-3.845 6.128 6.128 0 0 0 -4.809-.749c-2.56-4.188-8.652-10.455-13.189-13.792 1.813-.628 3.05-1.436 4.212-2.188 1.427-.93 2.614-1.69 4.857-2.067m3.88-26.926a2.096 2.096 0 0 1 1.783-.98c.4 0 .79.113 1.125.325.481.304.813.775.937 1.33a2.08 2.08 0 0 1 -.28 1.584c-.597.951-1.938 1.273-2.912.653l-.093-.062a2.105 2.105 0 0 1 -.56-2.85m0 23.463a2.096 2.096 0 0 1 1.783-.98c.4 0 .79.112 1.125.326.481.304.813.774.937 1.329a2.083 2.083 0 0 1 -.28 1.585c-.597.95-1.938 1.272-2.912.652l-.093-.062a2.105 2.105 0 0 1 -.56-2.85m0 26.304a2.099 2.099 0 0 1 1.783-.98c.4 0 .79.113 1.125.326.481.304.813.775.937 1.33a2.08 2.08 0 0 1 -.28 1.584c-.597.951-1.938 1.271-2.912.653l-.093-.062a2.107 2.107 0 0 1 -.56-2.851"
									/>
								</g>
								<path
									id="path1565"
									d="m108.53 116.927a6.11 6.11 0 0 0 -8.631-.662 6.12 6.12 0 0 0 -2.087 5.396c-1.949.701-4.258 1.366-5.328 1.332-.822-.043-1.302-.345-2.224-.971-1.172-.795-2.418-1.512-4.149-1.847 2.936-2.761 6.009-6.83 8.584-12.834a6.15 6.15 0 0 0 5.039-2.972c1.724-2.896.773-6.659-2.124-8.394a6.133 6.133 0 0 0 -3.134-.864 6.154 6.154 0 0 0 -5.261 2.986 6.079 6.079 0 0 0 -.678 4.636 6.075 6.075 0 0 0 2.302 3.429c-4.053 9.218-9.219 13.08-12.565 14.685a2.016 2.016 0 0 0 -.361.168c-1.186.531-2.05.704-2.558.808v4c.275-.03 1.884-.118 4.247-1.192 5.347-1.357 6.839-.354 8.407.709 1.021.692 2.291 1.554 4.263 1.655.099.006.204.009.308.009 2.11 0 5.172-1.022 7.021-1.723a6.132 6.132 0 0 0 4.285 1.75 6.112 6.112 0 0 0 3.983-1.476 6.072 6.072 0 0 0 2.119-4.176 6.072 6.072 0 0 0 -1.458-4.452m-15.873-16.776a2.114 2.114 0 0 1 3.632 2.165 2.097 2.097 0 0 1 -1.817 1.032c-.379 0-.755-.105-1.084-.301a2.092 2.092 0 0 1 -.964-1.294 2.101 2.101 0 0 1 .233-1.602m12.602 22.363a2.121 2.121 0 0 1 -2.981-.225 2.116 2.116 0 0 1 .231-2.981 2.08 2.08 0 0 1 1.373-.512 2.117 2.117 0 0 1 2.109 2.278 2.086 2.086 0 0 1 -.732 1.44m1.045-81.487a6.088 6.088 0 0 0 -2.708-3.844 6.12 6.12 0 0 0 -3.264-.944 6.076 6.076 0 0 0 -5.174 2.85 6.108 6.108 0 0 0 .144 6.758c-3.646 4.124-8.707 4.314-9.943 4.293-3.692-1.604-7.234-2.164-10.005-2.317v3.996c2.442.144 5.553.686 8.748 2.136.005.002.01.002.015.005 3.445 1.562 6.334 3.927 8.607 7.041a6.364 6.364 0 0 0 -.381.538c-1.796 2.854-.943 6.643 1.903 8.444.98.622 2.111.951 3.269.951a6.105 6.105 0 0 0 5.183-2.851c1.806-2.851.951-6.642-1.908-8.453-1.384-.872-3.092-1.114-4.667-.755-1.538-2.155-3.323-4.017-5.345-5.567 2.517-.778 5.401-2.268 7.78-5.095a6.18 6.18 0 0 0 1.77.266 6.08 6.08 0 0 0 5.172-2.848 6.053 6.053 0 0 0 .804-4.604m-10.57 22.647a2.128 2.128 0 0 1 2.916-.656 2.12 2.12 0 0 1 .66 2.919 2.115 2.115 0 0 1 -2.919.661 2.128 2.128 0 0 1 -.657-2.924m6.379-20.187c-.6.951-1.937 1.271-2.912.653l-.096-.062a2.107 2.107 0 0 1 -.557-2.851 2.093 2.093 0 0 1 1.784-.98 2.11 2.11 0 0 1 1.781 3.24"
									fill="#fffedb"
								/>
								<path id="path1567" d="" />
								<path
									id="path1569"
									d="m106.304 40.031a6.088 6.088 0 0 0 -2.708-3.844 6.12 6.12 0 0 0 -3.264-.944 6.076 6.076 0 0 0 -5.174 2.85 6.108 6.108 0 0 0 .144 6.758c-3.646 4.124-8.707 4.314-9.943 4.293-3.692-1.604-7.234-2.164-10.005-2.317v3.996c2.442.144 5.553.686 8.748 2.136.005.002.01.002.015.005 3.445 1.562 6.334 3.927 8.607 7.041a6.206 6.206 0 0 0 -.381.538c-1.796 2.854-.943 6.643 1.903 8.444.98.622 2.111.951 3.269.951a6.105 6.105 0 0 0 5.183-2.851c1.806-2.851.951-6.642-1.908-8.453-1.384-.872-3.092-1.114-4.667-.755-1.538-2.155-3.323-4.017-5.345-5.567 2.517-.778 5.401-2.268 7.78-5.095a6.18 6.18 0 0 0 1.77.266 6.08 6.08 0 0 0 5.172-2.849 6.05 6.05 0 0 0 .804-4.603m-10.57 22.647a2.128 2.128 0 0 1 2.916-.656 2.12 2.12 0 0 1 .66 2.919 2.115 2.115 0 0 1 -2.919.661 2.128 2.128 0 0 1 -.657-2.924m6.379-20.187c-.6.951-1.937 1.271-2.912.653l-.096-.062a2.107 2.107 0 0 1 -.557-2.851 2.093 2.093 0 0 1 1.784-.98 2.11 2.11 0 0 1 1.781 3.24"
									fill="#d3562c"
								/>
								<path
									id="path1571"
									d="m115 82.011a6.036 6.036 0 0 0 2.11 2.649 6.098 6.098 0 0 0 3.55 1.14 6.081 6.081 0 0 0 5.173-2.849 6.063 6.063 0 0 0 .805-4.604 6.103 6.103 0 0 0 -2.709-3.844 6.128 6.128 0 0 0 -3.266-.944c-.125 0-.25.008-.375.018-.71-3.158-.919-7.769.035-11.265.113.011.225.024.337.024a6.08 6.08 0 0 0 5.173-2.848 6.062 6.062 0 0 0 .805-4.604 6.103 6.103 0 0 0 -2.709-3.844 6.128 6.128 0 0 0 -3.266-.944 6.079 6.079 0 0 0 -5.173 2.85 6.104 6.104 0 0 0 1.025 7.764c.021.02.048.037.071.059-1.349 4.518-1.145 10.324-.036 14.417-.144.132-.294.254-.426.399a6.327 6.327 0 0 0 -.634.825 6.2 6.2 0 0 0 -.71 1.578c-3.371.488-5.198 1.671-6.824 2.729-2.035 1.323-3.961 2.571-9.636 2.665-1.963-.23-3.552-.178-5.29-.116-1.43.052-2.91.105-4.848.003-1.335-.071-2.104-.882-3.458-2.424-1.803-2.055-4.219-4.648-9.369-5.022v4c3.295.298 4.796 1.887 6.356 3.667 1.487 1.694 3.175 3.615 6.256 3.782 2.118.114 3.76.052 5.208.001 2.491-.092 4.453-.161 7.855.683 2.608.644 12.099 9.674 14.774 14.323-.109.142-.219.282-.314.434-1.743 2.755-1.026 6.385 1.62 8.251.072.052.151.104.283.191.981.621 2.11.948 3.267.948a6.081 6.081 0 0 0 5.173-2.849 6.06 6.06 0 0 0 .805-4.603 6.1 6.1 0 0 0 -2.709-3.845 6.128 6.128 0 0 0 -4.809-.749c-2.56-4.188-8.652-10.455-13.189-13.792 1.813-.628 3.05-1.436 4.212-2.188 1.427-.929 2.614-1.689 4.857-2.066m3.88-26.926a2.096 2.096 0 0 1 1.783-.98c.4 0 .79.113 1.125.325.481.304.813.775.937 1.33a2.08 2.08 0 0 1 -.28 1.584c-.597.951-1.938 1.273-2.912.653l-.093-.062a2.105 2.105 0 0 1 -.56-2.85m0 23.463a2.096 2.096 0 0 1 1.783-.98c.4 0 .79.112 1.125.326.481.304.813.774.937 1.329a2.084 2.084 0 0 1 -.28 1.585c-.597.95-1.938 1.271-2.912.652l-.093-.062a2.105 2.105 0 0 1 -.56-2.85m0 26.304a2.099 2.099 0 0 1 1.783-.98c.4 0 .79.113 1.125.326.481.304.813.775.937 1.33a2.08 2.08 0 0 1 -.28 1.584c-.597.951-1.938 1.271-2.912.653l-.093-.062a2.107 2.107 0 0 1 -.56-2.851"
									fill="#d3562c"
								/>
								<path
									id="path1573"
									d="m108.53 113.927a6.11 6.11 0 0 0 -8.631-.662 6.12 6.12 0 0 0 -2.087 5.396c-1.949.701-4.258 1.366-5.328 1.332-.822-.043-1.302-.345-2.224-.971-1.172-.795-2.418-1.512-4.149-1.847 2.936-2.761 6.009-6.83 8.584-12.834a6.15 6.15 0 0 0 5.039-2.972c1.724-2.896.773-6.659-2.124-8.394a6.133 6.133 0 0 0 -3.134-.864 6.154 6.154 0 0 0 -5.261 2.986 6.079 6.079 0 0 0 -.678 4.636 6.075 6.075 0 0 0 2.302 3.429c-4.053 9.218-9.219 13.08-12.565 14.685a2.016 2.016 0 0 0 -.361.168c-1.186.531-2.05.704-2.558.808v4c.275-.03 1.884-.118 4.247-1.192 5.347-1.357 6.839-.354 8.407.709 1.021.692 2.291 1.554 4.263 1.655.099.006.204.009.308.009 2.11 0 5.172-1.022 7.021-1.723a6.132 6.132 0 0 0 4.285 1.75 6.112 6.112 0 0 0 3.983-1.476 6.072 6.072 0 0 0 2.119-4.176 6.072 6.072 0 0 0 -1.458-4.452m-15.873-16.776a2.114 2.114 0 0 1 3.632 2.165 2.097 2.097 0 0 1 -1.817 1.032c-.379 0-.755-.105-1.084-.301a2.092 2.092 0 0 1 -.964-1.294 2.101 2.101 0 0 1 .233-1.602m12.602 22.363a2.121 2.121 0 0 1 -2.981-.225 2.116 2.116 0 0 1 .231-2.981 2.08 2.08 0 0 1 1.373-.512 2.117 2.117 0 0 1 2.109 2.278 2.086 2.086 0 0 1 -.732 1.44m1.045-81.487a6.088 6.088 0 0 0 -2.708-3.844 6.12 6.12 0 0 0 -3.264-.944 6.076 6.076 0 0 0 -5.174 2.85 6.108 6.108 0 0 0 .144 6.758c-3.646 4.124-8.707 4.314-9.943 4.293-3.692-1.604-7.234-2.164-10.005-2.317v3.996c2.442.144 5.553.686 8.748 2.136.005.002.01.002.015.005 3.445 1.562 6.334 3.927 8.607 7.041a6.364 6.364 0 0 0 -.381.538c-1.796 2.854-.943 6.643 1.903 8.444.98.622 2.111.951 3.269.951a6.105 6.105 0 0 0 5.183-2.851c1.806-2.851.951-6.642-1.908-8.453-1.384-.872-3.092-1.114-4.667-.755-1.538-2.155-3.323-4.017-5.345-5.567 2.517-.778 5.401-2.268 7.78-5.095a6.18 6.18 0 0 0 1.77.266 6.08 6.08 0 0 0 5.172-2.848 6.053 6.053 0 0 0 .804-4.604m-10.57 22.647a2.128 2.128 0 0 1 2.916-.656 2.12 2.12 0 0 1 .66 2.919 2.115 2.115 0 0 1 -2.919.661 2.128 2.128 0 0 1 -.657-2.924m6.379-20.187c-.6.951-1.937 1.271-2.912.653l-.096-.062a2.107 2.107 0 0 1 -.557-2.851 2.093 2.093 0 0 1 1.784-.98 2.11 2.11 0 0 1 1.781 3.24"
									fill="#fffedb"
								/>
								<path
									id="path1575"
									d="m115 79.011a6.036 6.036 0 0 0 2.11 2.649 6.098 6.098 0 0 0 3.55 1.14 6.083 6.083 0 0 0 5.173-2.848 6.064 6.064 0 0 0 .805-4.605 6.103 6.103 0 0 0 -2.709-3.844 6.128 6.128 0 0 0 -3.266-.944c-.125 0-.25.008-.375.018-.71-3.158-.919-7.769.035-11.265.113.011.225.024.337.024a6.08 6.08 0 0 0 5.173-2.848 6.062 6.062 0 0 0 .805-4.604 6.103 6.103 0 0 0 -2.709-3.844 6.128 6.128 0 0 0 -3.266-.944 6.079 6.079 0 0 0 -5.173 2.85 6.104 6.104 0 0 0 1.025 7.764c.021.02.048.037.071.059-1.349 4.518-1.145 10.324-.036 14.417-.144.132-.294.254-.426.399a6.327 6.327 0 0 0 -.634.825 6.2 6.2 0 0 0 -.71 1.578c-3.371.488-5.198 1.671-6.824 2.729-2.035 1.324-3.961 2.572-9.636 2.666-1.963-.23-3.552-.178-5.29-.116-1.43.052-2.91.105-4.848.003-1.335-.071-2.104-.882-3.458-2.424-1.803-2.055-4.219-4.648-9.369-5.022v4c3.295.299 4.796 1.887 6.356 3.667 1.487 1.694 3.175 3.615 6.256 3.782 2.118.114 3.76.052 5.208.001 2.491-.092 4.453-.161 7.855.683 2.608.644 12.099 9.674 14.774 14.323-.109.142-.219.282-.314.434-1.743 2.755-1.026 6.385 1.62 8.251.072.052.151.104.283.191.981.621 2.11.948 3.267.948a6.081 6.081 0 0 0 5.173-2.849 6.06 6.06 0 0 0 .805-4.603 6.1 6.1 0 0 0 -2.709-3.845 6.128 6.128 0 0 0 -4.809-.749c-2.56-4.188-8.652-10.455-13.189-13.792 1.813-.628 3.05-1.436 4.212-2.188 1.427-.93 2.614-1.69 4.857-2.067m3.88-26.926a2.096 2.096 0 0 1 1.783-.98c.4 0 .79.113 1.125.325.481.304.813.775.937 1.33a2.08 2.08 0 0 1 -.28 1.584c-.597.951-1.938 1.273-2.912.653l-.093-.062a2.105 2.105 0 0 1 -.56-2.85m0 23.463a2.096 2.096 0 0 1 1.783-.98c.4 0 .79.112 1.125.326.481.304.813.774.937 1.329a2.083 2.083 0 0 1 -.28 1.585c-.597.95-1.938 1.272-2.912.652l-.093-.062a2.105 2.105 0 0 1 -.56-2.85m0 26.304a2.099 2.099 0 0 1 1.783-.98c.4 0 .79.113 1.125.326.481.304.813.775.937 1.33a2.08 2.08 0 0 1 -.28 1.584c-.597.951-1.938 1.271-2.912.653l-.093-.062a2.107 2.107 0 0 1 -.56-2.851"
									fill="#fffedb"
								/>
								<g id="g1579" opacity=".25">
									<path
										id="path1577"
										d="m53.783 104.537c-.239 0-.48-.072-.691-.219a1.21 1.21 0 0 1 -.29-1.684c.273-.387.519-.799.73-1.227a9.028 9.028 0 0 0 -.501-8.961 1.208 1.208 0 1 1 2.032-1.307c2.176 3.389 2.42 7.736.633 11.34a11.833 11.833 0 0 1 -.92 1.547 1.204 1.204 0 0 1 -.993.511m5.556 2.877a1.207 1.207 0 0 1 -.968-1.922c.453-.615.854-1.279 1.198-1.973a13.243 13.243 0 0 0 -1.251-13.846 1.207 1.207 0 1 1 1.937-1.441 15.643 15.643 0 0 1 1.478 16.357c-.404.818-.88 1.604-1.414 2.33a1.213 1.213 0 0 1 -.98.495m-10.994-5.928a1.21 1.21 0 0 1 -1.045-1.802 4.802 4.802 0 0 0 .18-4.369 1.208 1.208 0 0 1 2.193-1.01 7.22 7.22 0 0 1 -.271 6.57 1.209 1.209 0 0 1 -1.057.611"
									/>
								</g>
								<g id="g1583" fill="#eb743b">
									<path
										id="path1581"
										d="m53.783 101.537c-.239 0-.48-.072-.691-.219a1.21 1.21 0 0 1 -.29-1.684c.273-.387.519-.799.73-1.227a9.028 9.028 0 0 0 -.501-8.961 1.208 1.208 0 1 1 2.032-1.307c2.176 3.389 2.42 7.736.633 11.34a11.833 11.833 0 0 1 -.92 1.547 1.204 1.204 0 0 1 -.993.511m5.556 2.877a1.207 1.207 0 0 1 -.968-1.922c.453-.615.854-1.279 1.198-1.973a13.243 13.243 0 0 0 -1.251-13.846 1.207 1.207 0 1 1 1.937-1.441 15.643 15.643 0 0 1 1.478 16.357c-.404.818-.88 1.604-1.414 2.33a1.213 1.213 0 0 1 -.98.495m-10.994-5.928a1.21 1.21 0 0 1 -1.045-1.802 4.802 4.802 0 0 0 .18-4.369 1.208 1.208 0 0 1 2.193-1.01 7.22 7.22 0 0 1 -.271 6.57 1.209 1.209 0 0 1 -1.057.611"
									/>
								</g>
								<g id="g1589">
									<path
										id="path1585"
										d="m66.082 47.24c-10.12 0-20.365 3.656-26.736 9.543-6.886 6.361-10.521 16.245-10.348 27.989a3.99 3.99 0 0 0 -3.41 3.941v18.119a4 4 0 0 0 4 4h2.033c.316 0 .619-.047.914-.115v1.602c0 2.209 1.565 4 3.495 4h1.898c1.93 0 3.495-1.791 3.495-4v-29.669c0-2.209-1.565-4-3.495-4h-1.898c-1.717 0-3.138 1.42-3.432 3.288.065-1.855.204-3.652.435-5.096 1.154-7.232 4.119-13.268 8.688-17.488 5.664-5.232 15.226-8.613 24.361-8.613 1.042 0 3.241.043 4.277.143v-3.49c-1.2-.117-3.163-.154-4.277-.154zm-34.459 59.188a2.652 2.652 0 0 1 -2.651-2.65v-12.01a2.651 2.651 0 0 1 2.651-2.652h.913v17.312z"
										opacity=".25"
									/>
									<path
										id="path1587"
										d="m66.082 44.24c-10.12 0-20.365 3.656-26.736 9.543-6.886 6.361-10.521 16.245-10.348 27.989a3.99 3.99 0 0 0 -3.41 3.941v18.119a4 4 0 0 0 4 4h2.033c.316 0 .619-.047.914-.115v1.602c0 2.209 1.565 4 3.495 4h1.898c1.93 0 3.495-1.791 3.495-4v-29.669c0-2.209-1.565-4-3.495-4h-1.898c-1.717 0-3.138 1.42-3.432 3.287.065-1.854.204-3.651.435-5.096 1.154-7.232 4.119-13.268 8.688-17.488 5.664-5.232 15.226-8.613 24.361-8.613 1.042 0 3.241.043 4.277.143v-3.49c-1.2-.116-3.163-.153-4.277-.153zm-34.459 59.188a2.652 2.652 0 0 1 -2.651-2.65v-12.01a2.651 2.651 0 0 1 2.651-2.652h.913v17.312z"
										fill="#fffedb"
									/>
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
								<svg viewBox="0 0 16 16" fill="currentColor" width="14" height="14"
									><path d="M13.5 3.5L6 11 2.5 7.5l-1 1L6 13l8.5-8.5z" /></svg
								>
							{/if}
							<span>{statusLabel[listenbrainz.status]}</span>
							{#if listenbrainz.username}<span class="status-user">· {listenbrainz.username}</span
								>{/if}
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
								disabled={!lbForm.username.trim() ||
									!lbForm.token.trim() ||
									listenbrainz.status === 'checking'}
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

	.logo-signal:nth-child(1) {
		width: 20px;
		animation-delay: 0s;
	}
	.logo-signal:nth-child(2) {
		width: 14px;
		animation-delay: 0.3s;
	}
	.logo-signal:nth-child(3) {
		width: 8px;
		animation-delay: 0.6s;
	}

	@keyframes signal-pulse {
		0%,
		100% {
			opacity: 0.35;
		}
		50% {
			opacity: 1;
		}
	}

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

	.conn-pill {
		display: inline-flex;
		align-items: center;
		gap: 0.4rem;
		font-size: 0.75rem;
		padding: 0.25rem 0.6rem;
		background: oklch(0.62 0.13 155 / 0.12);
		border: 1px solid oklch(0.62 0.13 155 / 0.35);
		color: oklch(0.8 0.13 155);
		font-variant-numeric: tabular-nums;
	}

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
		transition:
			background 0.3s,
			border-color 0.3s;
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

	.progress-step.active .step-label {
		color: oklch(0.75 0.1 38);
	}
	.progress-step.done .step-label {
		color: oklch(0.6 0.12 140);
	}

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

	.service-card {
		background: oklch(0.16 0.015 38);
		border: 1px solid oklch(0.22 0.02 38);
		margin-bottom: 1px;
		transition:
			border-color 0.3s,
			box-shadow 0.3s;
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

	.plex-icon {
		color: oklch(0.78 0.18 62);
	}
	.lastfm-icon {
		color: oklch(0.62 0.22 28);
	}
	.lb-icon {
		color: oklch(0.65 0.18 320);
	}

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

	.status-badge[data-status='connected'] {
		border-color: oklch(0.45 0.12 140 / 0.5);
		color: oklch(0.65 0.14 140);
		background: oklch(0.55 0.14 140 / 0.08);
	}

	.status-badge[data-status='needs-auth'] {
		border-color: oklch(0.4 0.1 38 / 0.4);
		color: oklch(0.6 0.08 40);
	}

	.status-badge[data-status='checking'] {
		border-color: oklch(0.4 0.12 62 / 0.4);
		color: oklch(0.65 0.12 62);
	}

	.status-badge[data-status='error'] {
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
		to {
			transform: rotate(360deg);
		}
	}

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
		0%,
		100% {
			opacity: 0.3;
		}
		50% {
			opacity: 1;
		}
	}

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
		transition:
			background 0.15s,
			border-color 0.15s,
			opacity 0.15s;
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
		from {
			opacity: 0;
			transform: translateY(8px);
		}
		to {
			opacity: 1;
			transform: translateY(0);
		}
	}

	/* The home canvas: a dotted plane, so the nodes read as placed on a board. */
	.flow-canvas {
		background-image: radial-gradient(
			color-mix(in oklab, var(--color-border, var(--border)) 75%, transparent) 1px,
			transparent 1px
		);
		background-size: 22px 22px;
	}
</style>
