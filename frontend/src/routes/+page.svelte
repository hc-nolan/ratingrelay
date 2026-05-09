<script lang="ts">
	import { untrack } from 'svelte';
	import type { PageData } from './$types';
	import type { PlexAuthState, LastFMAuthState, ListenBrainzAuthState, SetupStep } from '$lib/types/auth';

	let { data }: { data: PageData } = $props();

	// ── Service state ─────────────────────────────────────────────────────────
	// untrack: intentionally snapshot load values; mutations are local from here on.
	let plex = $state<PlexAuthState>(
		untrack(() => ({
			status: data.authStatus.plex.status,
			username: data.authStatus.plex.username,
			server_name: data.authStatus.plex.server_name,
			server_url: data.authStatus.plex.server_url,
		}))
	);
	let lastfm = $state<LastFMAuthState>(
		untrack(() => ({ status: data.authStatus.lastfm.status, username: data.authStatus.lastfm.username }))
	);
	let listenbrainz = $state<ListenBrainzAuthState>(
		untrack(() => ({ status: data.authStatus.listenbrainz.status, username: data.authStatus.listenbrainz.username }))
	);

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
	// POST /api/plex/auth/init  → { pin_id: number, oauth_url: string }
	// GET  /api/plex/auth/callback  (handled by backend, closes popup via postMessage)
	// GET  /api/plex/auth/status    → PlexStatusResponse

	let plexPopup: Window | null = null;

	async function startPlexAuth() {
		plex.status = 'checking';
		try {
			const res = await fetch('/api/plex/auth/init', { method: 'POST' });
			if (!res.ok) throw new Error(await res.text());
			const { oauth_url }: { pin_id: number; oauth_url: string } = await res.json();

			// Open Plex OAuth in a centred popup
			const w = 800, h = 700;
			const left = Math.round(window.screenX + (window.outerWidth - w) / 2);
			const top = Math.round(window.screenY + (window.outerHeight - h) / 2);
			plexPopup = window.open(
				oauth_url,
				'plex-oauth',
				`width=${w},height=${h},left=${left},top=${top},toolbar=0,menubar=0`
			);

			plex.status = 'needs-auth'; // show "waiting for popup" UI
		} catch {
			plex.status = 'error';
		}
	}

	// Listen for the postMessage sent by the backend callback page
	function handleMessage(event: MessageEvent) {
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
		} catch {
			plex.status = 'error';
		}
	}

	$effect(() => {
		window.addEventListener('message', handleMessage);
		return () => window.removeEventListener('message', handleMessage);
	});

	// ── Server selection ──────────────────────────────────────────────────────
	// GET  /api/plex/servers   → { servers: PlexServerItem[] }
	// POST /api/plex/server    → PlexStatusResponse

	interface PlexServerItem { name: string; product: string; client_identifier: string }
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

	// Auto-load servers when we reach the server-select step
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
	// POST /api/lastfm/auth  { api_key, api_secret, username, password }

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
	// POST /api/listenbrainz/auth  { username, token }

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
</script>

<svelte:head>
	<title>RatingRelay — Setup</title>
</svelte:head>

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
				class:active={step === 'done'}
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
					<!-- Plex SVG chevron -->
					<svg viewBox="0 0 24 24" fill="currentColor" xmlns="http://www.w3.org/2000/svg">
						<path d="M12 0C5.373 0 0 5.373 0 12s5.373 12 12 12 12-5.373 12-12S18.627 0 12 0zm-1.2 17.4L6 12l4.8-5.4 1.44 1.62L9.36 12l2.88 3.78-1.44 1.62zm3.6 0L9.6 12l4.8-5.4 1.44 1.62L12.96 12l2.88 3.78-1.44 1.62z"/>
					</svg>
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
					<!-- server icon -->
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
							<p class="card-instructions" style:color="oklch(0.65 0.18 22)">{serverError}</p>
						{/if}
					{/if}
				</div>
			{/if}
		</section>

		<!-- ── Step: Music Services ────────────────────────────── -->
		<section class="service-group" class:group-active={step === 'music-services' || step === 'done' || step === 'server-select'}>
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
						<p class="form-error">{lastfmError}</p>
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
						<svg viewBox="0 0 24 24" fill="currentColor" xmlns="http://www.w3.org/2000/svg">
							<path d="M12 0C5.373 0 0 5.373 0 12s5.373 12 12 12 12-5.373 12-12S18.627 0 12 0zm.35 4.292l2.13 4.31 4.755.692-3.44 3.35.812 4.735-4.257-2.238-4.258 2.238.813-4.735-3.44-3.35 4.754-.692 2.13-4.31z"/>
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
						<p class="form-error">{lbError}</p>
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

		<!-- ── Done state ──────────────────────────────────────── -->
		{#if step === 'done'}
			<div class="done-banner" role="status">
				<svg viewBox="0 0 20 20" fill="currentColor" width="20" height="20" aria-hidden="true">
					<path fill-rule="evenodd" d="M16.707 5.293a1 1 0 010 1.414l-8 8a1 1 0 01-1.414 0l-4-4a1 1 0 011.414-1.414L8 12.586l7.293-7.293a1 1 0 011.414 0z" clip-rule="evenodd"/>
				</svg>
				All services connected — RatingRelay is ready.
			</div>
		{/if}
	</main>
</div>

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
		margin-bottom: 16px; /* offset for label */
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

	.card-instructions strong {
		color: oklch(0.72 0.04 50);
		font-weight: 600;
	}

	.card-instructions a {
		color: var(--primary);
		text-decoration: underline;
		text-underline-offset: 2px;
	}

	/* ── PIN display ───────────────────────────────────────────────────────── */
	.pin-display {
		display: flex;
		gap: 0.5rem;
	}

	.pin-digit {
		width: 48px;
		height: 56px;
		display: grid;
		place-items: center;
		font-family: var(--font-heading);
		font-size: 1.75rem;
		font-weight: 700;
		letter-spacing: 0.05em;
		color: var(--primary);
		background: oklch(0.13 0.02 38);
		border: 1px solid oklch(0.45 0.15 38 / 0.5);
		box-shadow: inset 0 0 12px oklch(0.55 0.18 38 / 0.08);
		animation: digit-appear 0.3s ease both;
	}

	@keyframes digit-appear {
		from { opacity: 0; transform: translateY(4px); }
		to { opacity: 1; transform: translateY(0); }
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

	/* ── Token input row ───────────────────────────────────────────────────── */
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

	.token-row {
		display: flex;
		gap: 0.5rem;
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

	/* ── Done banner ───────────────────────────────────────────────────────── */
	.done-banner {
		margin-top: 1.5rem;
		display: flex;
		align-items: center;
		gap: 0.625rem;
		padding: 0.875rem 1.125rem;
		background: oklch(0.55 0.14 140 / 0.1);
		border: 1px solid oklch(0.5 0.13 140 / 0.35);
		color: oklch(0.7 0.14 140);
		font-size: 0.8125rem;
		font-weight: 500;
		animation: slide-up 0.4s ease both;
	}

	@keyframes slide-up {
		from { opacity: 0; transform: translateY(8px); }
		to { opacity: 1; transform: translateY(0); }
	}
</style>
