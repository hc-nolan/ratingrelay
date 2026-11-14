<script lang="ts">
	import { untrack } from 'svelte';
	import { resolve } from '$app/paths';
	import AppHeader from '$lib/components/AppHeader.svelte';
	import ServiceMark from '$lib/components/flow/ServiceMark.svelte';
	import ConnectionChip from '$lib/components/services/ConnectionChip.svelte';
	import LastFmForm from '$lib/components/services/LastFmForm.svelte';
	import ListenBrainzForm from '$lib/components/services/ListenBrainzForm.svelte';
	import { Button } from '$lib/components/ui/button';
	import { Select } from '$lib/components/ui/select';
	import type { PlexServerItem, ServiceStatus } from '$lib/types/auth';
	import type { PageData } from './$types';

	let { data }: { data: PageData } = $props();

	let plex = $state(untrack(() => ({ ...data.authStatus.plex })));
	let lastfm = $state(untrack(() => ({ ...data.authStatus.lastfm })));
	let listenbrainz = $state(untrack(() => ({ ...data.authStatus.listenbrainz })));

	/** Which service is showing its credential form. */
	let editing = $state<'lastfm' | 'listenbrainz' | null>(null);

	/** Which service has been asked to disconnect and is waiting for a yes. */
	let confirming = $state<'plex' | 'lastfm' | 'listenbrainz' | null>(null);
	let busy = $state(false);
	let error = $state('');

	// ── Plex: server selection + disconnect ───────────────────────────────────
	let servers = $state<PlexServerItem[]>([]);
	let selectedServer = $state('');
	let loadingServers = $state(false);

	async function loadServers() {
		loadingServers = true;
		error = '';
		try {
			const res = await fetch('/api/plex/servers');
			if (!res.ok) throw new Error(await res.text());
			const body: { servers: PlexServerItem[] } = await res.json();
			servers = body.servers;
			selectedServer = plex.server_name ?? servers[0]?.name ?? '';
		} catch (e) {
			error = e instanceof Error ? e.message : 'Could not list Plex servers';
		} finally {
			loadingServers = false;
		}
	}

	async function saveServer() {
		if (!selectedServer) return;
		busy = true;
		error = '';
		try {
			const res = await fetch('/api/plex/server', {
				method: 'POST',
				headers: { 'Content-Type': 'application/json' },
				body: JSON.stringify({ name: selectedServer })
			});
			if (!res.ok) throw new Error(await res.text());
			const body: { server_name?: string; server_url?: string } = await res.json();
			plex.server_name = body.server_name;
			plex.server_url = body.server_url;
			servers = [];
		} catch (e) {
			error = e instanceof Error ? e.message : 'Could not switch server';
		} finally {
			busy = false;
		}
	}

	// ── Disconnect ────────────────────────────────────────────────────────────
	async function disconnect(service: 'plex' | 'lastfm' | 'listenbrainz') {
		busy = true;
		error = '';
		try {
			const res = await fetch(`/api/${service}/auth`, { method: 'DELETE' });
			if (!res.ok) throw new Error(await res.text());
			const body: { status: ServiceStatus; username?: string } = await res.json();
			const state = service === 'plex' ? plex : service === 'lastfm' ? lastfm : listenbrainz;
			state.status = body.status;
			state.username = body.username;
			if (service === 'plex') {
				plex.server_name = undefined;
				plex.server_url = undefined;
			}
			confirming = null;
			editing = null;
		} catch (e) {
			error = e instanceof Error ? e.message : 'Could not disconnect';
		} finally {
			busy = false;
		}
	}
</script>

<svelte:head><title>Services · RatingRelay</title></svelte:head>

<div class="flex min-h-dvh flex-col">
	<AppHeader>{#snippet status()}{/snippet}</AppHeader>

	<main class="min-h-0 flex-1 overflow-y-auto">
		<div class="mx-auto w-full max-w-4xl px-6 py-8">
			<h1 class="text-xl font-semibold">Services</h1>
			<p class="mt-1 max-w-2xl text-sm text-muted-foreground">
				Plex is the source of truth for ratings; Last.fm and ListenBrainz are where they are
				written. Changing a credential here does not touch anything the relay has already written —
				the next run reconciles against it.
			</p>

			{#if error}
				<p class="mt-4 text-sm text-destructive" role="alert">{error}</p>
			{/if}

			<div class="mt-6 space-y-2">
				<!-- Plex -->
				<section class="panel px-4 py-3.5">
					<div class="flex items-center gap-3">
						<ServiceMark service="plex" size={26} />
						<div class="min-w-0 flex-1">
							<h2 class="text-sm font-medium">Plex</h2>
							<p class="text-xs text-muted-foreground">
								Source of your media ratings{#if plex.server_name}
									· {plex.server_name}{/if}
							</p>
						</div>
						<ConnectionChip status={plex.status} username={plex.username} />
					</div>

					{#if confirming === 'plex'}
						<div class="mt-3 border-t border-border pt-3">
							<p class="text-sm text-warn">
								The login this app checks is signed with your Plex token. Disconnecting Plex removes
								the only login RatingRelay has and leaves it open to anyone who can reach it, until
								you sign in again.
							</p>
							<div class="mt-3 flex gap-2">
								<Button
									variant="destructive"
									class="h-8 px-3 text-sm"
									onclick={() => disconnect('plex')}
									disabled={busy}>Disconnect anyway</Button
								>
								<Button
									variant="outline"
									class="h-8 px-3 text-sm"
									onclick={() => (confirming = null)}>Cancel</Button
								>
							</div>
						</div>
					{:else if plex.status === 'connected'}
						<div class="mt-3 flex flex-wrap items-center gap-2 border-t border-border pt-3">
							{#if servers.length > 0}
								<Select
									bind:value={selectedServer}
									class="min-w-56"
									label="Plex server"
									options={servers.map((srv) => ({ value: srv.name, label: srv.name }))}
								/>
								<Button class="h-8 px-3 text-sm" onclick={saveServer} disabled={busy}>
									Use this server
								</Button>
							{:else}
								<Button
									variant="outline"
									class="h-8 px-3 text-sm"
									onclick={loadServers}
									disabled={loadingServers}
								>
									{loadingServers ? 'Loading servers…' : 'Change server'}
								</Button>
							{/if}
							<Button
								variant="ghost"
								class="ml-auto h-8 px-3 text-sm text-destructive"
								onclick={() => (confirming = 'plex')}>Disconnect</Button
							>
						</div>
					{:else}
						<div class="mt-3 border-t border-border pt-3 text-sm text-muted-foreground">
							Plex is not connected. Signing in to Plex is part of
							<a class="text-primary hover:underline" href={resolve('/')}>setup</a> — the login window
							has to come from there.
						</div>
					{/if}
				</section>

				<!-- Last.fm -->
				<section class="panel px-4 py-3.5">
					<div class="flex items-center gap-3">
						<ServiceMark service="lastfm" size={26} />
						<div class="min-w-0 flex-1">
							<h2 class="text-sm font-medium">Last.fm</h2>
							<p class="text-xs text-muted-foreground">Love every track at or above the cutoff</p>
						</div>
						<ConnectionChip status={lastfm.status} username={lastfm.username} />
					</div>

					{#if confirming === 'lastfm'}
						<div class="mt-3 border-t border-border pt-3">
							<p class="text-sm text-muted-foreground">
								Forget the Last.fm API key, secret and password. What was already written stays
								written, and the next run has nothing to reconcile against Last.fm.
							</p>
							<div class="mt-3 flex gap-2">
								<Button
									variant="destructive"
									class="h-8 px-3 text-sm"
									onclick={() => disconnect('lastfm')}
									disabled={busy}>Disconnect Last.fm</Button
								>
								<Button
									variant="outline"
									class="h-8 px-3 text-sm"
									onclick={() => (confirming = null)}>Cancel</Button
								>
							</div>
						</div>
					{:else if editing === 'lastfm'}
						<div class="mt-3 border-t border-border pt-3">
							<LastFmForm
								submitLabel="Replace credentials"
								onsubmitted={(username) => {
									lastfm.status = 'connected';
									lastfm.username = username;
									editing = null;
								}}
							/>
							<Button variant="ghost" class="mt-2 h-8 px-3 text-sm" onclick={() => (editing = null)}
								>Cancel</Button
							>
						</div>
					{:else if lastfm.status === 'connected'}
						<div class="mt-3 flex gap-2 border-t border-border pt-3">
							<Button
								variant="outline"
								class="h-8 px-3 text-sm"
								onclick={() => (editing = 'lastfm')}>Replace credentials</Button
							>
							<Button
								variant="ghost"
								class="ml-auto h-8 px-3 text-sm text-destructive"
								onclick={() => (confirming = 'lastfm')}>Disconnect</Button
							>
						</div>
					{:else}
						<div class="mt-3 border-t border-border pt-3">
							<LastFmForm
								onsubmitted={(username) => {
									lastfm.status = 'connected';
									lastfm.username = username;
								}}
							/>
						</div>
					{/if}
				</section>

				<!-- ListenBrainz -->
				<section class="panel px-4 py-3.5">
					<div class="flex items-center gap-3">
						<ServiceMark service="listenbrainz" size={26} />
						<div class="min-w-0 flex-1">
							<h2 class="text-sm font-medium">ListenBrainz</h2>
							<p class="text-xs text-muted-foreground">
								Love or hate every track at or above the cutoff
							</p>
						</div>
						<ConnectionChip status={listenbrainz.status} username={listenbrainz.username} />
					</div>

					{#if confirming === 'listenbrainz'}
						<div class="mt-3 border-t border-border pt-3">
							<p class="text-sm text-muted-foreground">
								Forget the ListenBrainz token. Feedback already submitted stays submitted.
							</p>
							<div class="mt-3 flex gap-2">
								<Button
									variant="destructive"
									class="h-8 px-3 text-sm"
									onclick={() => disconnect('listenbrainz')}
									disabled={busy}>Disconnect ListenBrainz</Button
								>
								<Button
									variant="outline"
									class="h-8 px-3 text-sm"
									onclick={() => (confirming = null)}>Cancel</Button
								>
							</div>
						</div>
					{:else if editing === 'listenbrainz'}
						<div class="mt-3 border-t border-border pt-3">
							<ListenBrainzForm
								submitLabel="Replace token"
								onsubmitted={(username) => {
									listenbrainz.status = 'connected';
									listenbrainz.username = username;
									editing = null;
								}}
							/>
							<Button variant="ghost" class="mt-2 h-8 px-3 text-sm" onclick={() => (editing = null)}
								>Cancel</Button
							>
						</div>
					{:else if listenbrainz.status === 'connected'}
						<div class="mt-3 flex gap-2 border-t border-border pt-3">
							<Button
								variant="outline"
								class="h-8 px-3 text-sm"
								onclick={() => (editing = 'listenbrainz')}>Replace token</Button
							>
							<Button
								variant="ghost"
								class="ml-auto h-8 px-3 text-sm text-destructive"
								onclick={() => (confirming = 'listenbrainz')}>Disconnect</Button
							>
						</div>
					{:else}
						<div class="mt-3 border-t border-border pt-3">
							<ListenBrainzForm
								onsubmitted={(username) => {
									listenbrainz.status = 'connected';
									listenbrainz.username = username;
								}}
							/>
						</div>
					{/if}
				</section>
			</div>
		</div>
	</main>
</div>
