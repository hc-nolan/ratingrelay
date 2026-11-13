<script lang="ts">
	import { untrack } from 'svelte';
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
	import ActivityRail from '$lib/components/jobs/ActivityRail.svelte';
	import AppHeader from '$lib/components/AppHeader.svelte';
	import ServiceMark from '$lib/components/flow/ServiceMark.svelte';
	import { Button } from '$lib/components/ui/button';
	import { NativeSelect } from '$lib/components/ui/native-select';
	import ConnectionChip from '$lib/components/services/ConnectionChip.svelte';
	import LastFmForm from '$lib/components/services/LastFmForm.svelte';
	import ListenBrainzForm from '$lib/components/services/ListenBrainzForm.svelte';
	import { cn } from '$lib/utils';
	import { TONE_CHIP, statusLabel, statusTone } from '$lib/format';

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
	// Setup is not finished the moment a service answers: the user may still want
	// to connect the other one, and being thrown onto the relay screen mid-setup
	// is how you end up with one target and no way back to the form. Pressing
	// "Start relaying" is what moves the page on — except for a returning user,
	// who was already set up before this page loaded.
	let started = $state(untrack(() => plex.status === 'connected' && !!plex.server_name && musicOk));

	let step: SetupStep = $derived.by(() => {
		if (plex.status !== 'connected') return 'plex';
		if (!plex.server_name) return 'server-select';
		if (!musicOk || !started) return 'music-services';
		return 'done';
	});

	// ── Plex OAuth popup flow ─────────────────────────────────────────────────
	// The popup redirects to our callback, which stores the token and tells us.
	// We do not rely on that message: in dev the popup is served by the API on a
	// different origin than the Vite UI, so the browser drops it and the wizard
	// sits there until a manual refresh. Polling the status endpoint is the
	// signal, and the message is only a shortcut.
	let plexPopup = $state<Window | null>(null);
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

	const wizardSteps = $derived([
		{ label: 'Plex', done: plex.status === 'connected', active: step === 'plex' },
		{ label: 'Server', done: !!plex.server_name, active: step === 'server-select' },
		{ label: 'Music', done: musicOk, active: step === 'music-services' },
		{ label: 'Ready', done: musicOk, active: step === 'done' }
	]);

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
		<AppHeader>
			{#snippet status()}
				<div class="flex flex-wrap items-center gap-1.5">
					{#if plex.status === 'connected'}
						<span
							class="inline-flex items-center gap-1 rounded-full border border-border px-1.5 py-0.5 text-xs text-muted-foreground"
						>
							<ServiceMark service="plex" size={12} />
							{plex.server_name ?? 'Plex'}
						</span>
					{/if}
					{#if lastfm.status === 'connected'}
						<span
							class="inline-flex items-center gap-1 rounded-full border border-border px-1.5 py-0.5 text-xs text-muted-foreground"
						>
							<ServiceMark service="lastfm" size={12} />
							{lastfm.username}
						</span>
					{/if}
					{#if listenbrainz.status === 'connected'}
						<span
							class="inline-flex items-center gap-1 rounded-full border border-border px-1.5 py-0.5 text-xs text-muted-foreground"
						>
							<ServiceMark service="listenbrainz" size={12} />
							{listenbrainz.username}
						</span>
					{/if}
				</div>
			{/snippet}
		</AppHeader>

		{#if relayError}
			<div
				class="shrink-0 border-b border-destructive/30 bg-destructive/10 px-5 py-2 text-sm text-destructive"
			>
				{relayError}
			</div>
		{/if}

		<div class="flex min-h-0 flex-1">
			<section class="min-h-0 flex-1 overflow-y-auto" aria-label="Relay flow">
				<div class="mx-auto flex min-h-full w-full max-w-4xl flex-col justify-center px-8 py-8">
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
							<div class="space-y-2.5">
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
								<p class="-mt-1 mb-3 text-center text-xs text-muted-foreground">
									Where should these ratings go?
								</p>
							{:else}
								<FlowLink
									onadd={() => (showTargetPicker = !showTargetPicker)}
									active={showTargetPicker}
								/>
							{/if}

							{#if allocatedTargets.length === 0 || showTargetPicker}
								<div class="space-y-2">
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

					<FlowLink />

					<RunDock
						bind:repeat
						bind:dryRun
						mode={selectedSource === 'reset' ? 'reset' : 'run'}
						busy={relaying}
						canRun={canQueue}
						onrun={queueJob}
					/>
				</div>
			</section>

			<ActivityRail {jobs} oncancel={cancelJob} />
		</div>
	</div>
{:else}
	<!-- ═══════════════════════════════════════════════════════════════════════ -->
	<!-- Setup Wizard                                                            -->
	<!-- ═══════════════════════════════════════════════════════════════════════ -->

	<div class="flex min-h-dvh flex-col items-center justify-center px-6 py-10">
		<div class="w-full max-w-xl">
			<!-- Header -->
			<header class="mb-7 flex items-center gap-3">
				<div
					class="grid size-9 shrink-0 place-items-center rounded-md bg-primary"
					aria-hidden="true"
				>
					<span class="flex items-end gap-0.5">
						<span class="h-1.5 w-1 rounded-[1px] bg-primary-foreground/60"></span>
						<span class="h-3 w-1 rounded-[1px] bg-primary-foreground"></span>
						<span class="h-2 w-1 rounded-[1px] bg-primary-foreground/80"></span>
					</span>
				</div>
				<div class="min-w-0">
					<h1 class="text-lg font-semibold tracking-tight">RatingRelay</h1>
					<p class="text-sm text-muted-foreground">
						Connect your services to begin relaying ratings.
					</p>
				</div>
			</header>

			<!-- Progress rail -->
			<ol class="mb-6 flex flex-wrap items-center gap-2" aria-label="Setup steps">
				{#each wizardSteps as s, i (s.label)}
					<li class="flex items-center gap-2">
						{#if i > 0}
							<span
								class={cn('h-px w-5', wizardSteps[i - 1].done ? 'bg-ok/60' : 'bg-border')}
								aria-hidden="true"
							></span>
						{/if}
						<span class="flex items-center gap-1.5">
							<span
								class={cn(
									'grid size-5 place-items-center rounded-full border font-mono text-xs tabular-nums',
									s.done
										? 'border-ok/40 bg-ok/15 text-ok'
										: s.active
											? 'border-primary/60 text-primary'
											: 'border-border text-muted-foreground'
								)}
							>
								{s.done ? '✓' : i + 1}
							</span>
							<span class={cn('text-xs', s.active ? 'text-foreground' : 'text-muted-foreground')}>
								{s.label}
							</span>
						</span>
					</li>
				{/each}
			</ol>

			<!-- ── Step: Plex ────────────────────────────────────────────── -->
			<section
				class={cn(
					'panel px-4 py-3.5',
					step === 'plex' && 'ring-1 ring-primary/25',
					plex.status === 'connected' && 'bg-card'
				)}
			>
				<div class="flex items-center gap-3">
					<ServiceMark service="plex" size={26} />
					<div class="min-w-0 flex-1">
						<h2 class="text-sm font-medium text-foreground">Plex</h2>
						<p class="text-xs text-muted-foreground">Source of your media ratings</p>
					</div>
					<span
						class={cn(
							'inline-flex shrink-0 items-center gap-1.5 rounded-full border px-2 py-0.5 text-xs',
							TONE_CHIP[statusTone(plex.status)]
						)}
					>
						{#if plex.status === 'checking'}
							<span class="size-1.5 animate-pulse rounded-full bg-current" aria-hidden="true"
							></span>
						{:else if plex.status === 'connected'}
							<svg viewBox="0 0 16 16" fill="currentColor" width="10" height="10" aria-hidden="true"
								><path d="M13.5 3.5L6 11 2.5 7.5l-1 1L6 13l8.5-8.5z" /></svg
							>
						{/if}
						{statusLabel(plex.status)}
						{#if plex.username}
							<span class="opacity-70">· {plex.username}</span>
						{/if}
					</span>
				</div>

				{#if plex.status !== 'connected' && plex.status !== 'disabled'}
					<div class="mt-3 border-t border-border pt-3">
						{#if plex.status === 'needs-auth' && plexPopup}
							<p class="text-sm text-foreground">
								A Plex login window has opened. Sign in there to continue.
							</p>
							<p class="mt-1 text-xs text-muted-foreground">Waiting for authorisation…</p>
						{:else}
							<p class="text-sm text-muted-foreground">
								A Plex login window will open. Sign in with your Plex account to grant access.
							</p>
							<Button
								class="mt-3 h-8 px-3 text-sm"
								onclick={startPlexAuth}
								disabled={plex.status === 'checking'}
							>
								{plex.status === 'checking' ? 'Opening Plex login…' : 'Connect Plex'}
							</Button>
						{/if}
					</div>
				{/if}
			</section>

			<!-- ── Step: Server selection ────────────────────────────────── -->
			<section
				class={cn(
					'panel mt-2 px-4 py-3.5',
					step === 'server-select' && 'ring-1 ring-primary/25',
					plex.status !== 'connected' && 'pointer-events-none opacity-40'
				)}
			>
				<div class="flex items-center gap-3">
					<ServiceMark service="plex" size={26} />
					<div class="min-w-0 flex-1">
						<h2 class="text-sm font-medium text-foreground">Plex server</h2>
						<p class="text-xs text-muted-foreground">Which server to relay ratings from</p>
					</div>
					{#if plex.server_name}
						<span
							class={cn(
								'inline-flex shrink-0 items-center gap-1.5 rounded-full border px-2 py-0.5 text-xs',
								TONE_CHIP.ok
							)}
						>
							<svg viewBox="0 0 16 16" fill="currentColor" width="10" height="10" aria-hidden="true"
								><path d="M13.5 3.5L6 11 2.5 7.5l-1 1L6 13l8.5-8.5z" /></svg
							>
							{plex.server_name}
						</span>
					{/if}
				</div>

				{#if !plex.server_name && plex.status === 'connected'}
					<div class="mt-3 border-t border-border pt-3">
						{#if loadingServers && availableServers.length === 0}
							<p class="text-xs text-muted-foreground">Loading servers…</p>
						{:else if serverError}
							<p class="text-sm text-destructive" role="alert">{serverError}</p>
							<Button variant="outline" class="mt-2 h-8 px-3 text-sm" onclick={loadServers}>
								Retry
							</Button>
						{:else if availableServers.length === 0}
							<p class="text-sm text-muted-foreground">
								No Plex Media Servers found on this account.
							</p>
						{:else}
							<p class="text-sm text-muted-foreground">
								Select the server RatingRelay should read ratings from.
							</p>
							<div class="mt-3 flex items-center gap-2">
								<NativeSelect bind:value={selectedServer} class="min-w-56">
									{#each availableServers as srv (srv.name)}
										<option value={srv.name}>{srv.name}</option>
									{/each}
								</NativeSelect>
								<Button
									class="h-8 px-3 text-sm"
									onclick={confirmServer}
									disabled={!selectedServer || loadingServers}
								>
									{loadingServers ? 'Connecting…' : 'Select'}
								</Button>
							</div>
						{/if}
					</div>
				{/if}
			</section>

			<!-- ── Step: Music Services ──────────────────────────────────── -->
			<section class="mt-6">
				<div class="flex items-baseline justify-between px-1 pb-2">
					<span class="eyebrow">Music services</span>
					<span class="text-xs text-muted-foreground">Connect at least one</span>
				</div>

				<!-- LastFM -->
				<div
					class={cn(
						'panel px-4 py-3.5',
						step === 'music-services' && 'ring-1 ring-primary/25',
						lastfm.status === 'disabled' && 'opacity-50'
					)}
				>
					<div class="flex items-center gap-3">
						<ServiceMark service="lastfm" size={26} />
						<div class="min-w-0 flex-1">
							<h3 class="text-sm font-medium text-foreground">Last.fm</h3>
							<p class="text-xs text-muted-foreground">Scrobble destination</p>
						</div>
						<ConnectionChip status={lastfm.status} username={lastfm.username} />
					</div>
					{#if lastfm.status !== 'connected' && lastfm.status !== 'disabled'}
						<div class="mt-3 border-t border-border pt-3">
							<LastFmForm
								onsubmitted={(username) => {
									lastfm.status = 'connected';
									lastfm.username = username;
								}}
							/>
						</div>
					{/if}
				</div>

				<!-- ListenBrainz -->
				<div
					class={cn(
						'panel mt-2 px-4 py-3.5',
						step === 'music-services' && 'ring-1 ring-primary/25',
						listenbrainz.status === 'disabled' && 'opacity-50'
					)}
				>
					<div class="flex items-center gap-3">
						<ServiceMark service="listenbrainz" size={26} />
						<div class="min-w-0 flex-1">
							<h3 class="text-sm font-medium text-foreground">ListenBrainz</h3>
							<p class="text-xs text-muted-foreground">Scrobble destination</p>
						</div>
						<ConnectionChip status={listenbrainz.status} username={listenbrainz.username} />
					</div>
					{#if listenbrainz.status !== 'connected' && listenbrainz.status !== 'disabled'}
						<div class="mt-3 border-t border-border pt-3">
							<ListenBrainzForm
								onsubmitted={(username) => {
									listenbrainz.status = 'connected';
									listenbrainz.username = username;
								}}
							/>
						</div>
					{/if}
				</div>
				{#if musicOk}
					<Button class="mt-6 h-8 w-full px-3 text-sm" onclick={() => (started = true)}>
						Start relaying
					</Button>
					<p class="mt-2 text-center text-xs text-muted-foreground">
						Both services stay in sync. You can add or replace one later from Services.
					</p>
				{:else}
					<p class="mt-5 text-center text-xs text-muted-foreground">
						Connect at least one service to continue.
					</p>
				{/if}
			</section>
		</div>
	</div>
{/if}
