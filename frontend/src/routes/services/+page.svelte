<script lang="ts">
	import { untrack } from 'svelte';
	import { resolve } from '$app/paths';
	import AppHeader from '$lib/components/AppHeader.svelte';
	import ServiceMark from '$lib/components/flow/ServiceMark.svelte';
	import AccountRow from '$lib/components/services/AccountRow.svelte';
	import ConnectionChip from '$lib/components/services/ConnectionChip.svelte';
	import LastFmForm from '$lib/components/services/LastFmForm.svelte';
	import HyperfixForm from '$lib/components/services/HyperfixForm.svelte';
	import ListenBrainzForm from '$lib/components/services/ListenBrainzForm.svelte';
	import { Button } from '$lib/components/ui/button';
	import { Input } from '$lib/components/ui/input';
	import { Select } from '$lib/components/ui/select';
	import { loadAccounts } from '$lib/service-status';
	import type { PlexServerItem, ServiceStatus } from '$lib/types/auth';
	import type { ServiceAccountItem, ServiceKind } from '$lib/types/services';
	import type { PageData } from './$types';

	let { data }: { data: PageData } = $props();

	let plex = $state(untrack(() => ({ ...data.authStatus.plex })));
	let lastfm = $state(untrack(() => ({ ...data.authStatus.lastfm })));
	let listenbrainz = $state(untrack(() => ({ ...data.authStatus.listenbrainz })));
	let hyperfix = $state(untrack(() => ({ ...data.authStatus.hyperfix })));

	/** Every connected account: the first of each service, plus any added. */
	let accounts = $state<ServiceAccountItem[]>(untrack(() => [...data.accounts]));

	/** Which service is showing its primary credential form. */
	let editing = $state<ServiceKind | null>(null);

	/** Which account is one click away from being disconnected. */
	let confirming = $state<string | null>(null);

	/** Which service is offering "add another account", and under what name. */
	let adding = $state<ServiceKind | null>(null);
	let newSlug = $state('');
	let newLabel = $state('');
	/** Whether the user typed the slug, so the label stops rewriting it. */
	let slugTouched = $state(false);

	let busy = $state(false);
	let error = $state('');

	// ── Plex: server selection ────────────────────────────────────────────────
	let servers = $state<PlexServerItem[]>([]);
	let selectedServer = $state('');
	let loadingServers = $state(false);

	let rows = $derived.by(() => {
		const grouped = { plex: [], lastfm: [], listenbrainz: [], hyperfix: [] } as Record<
			ServiceKind,
			ServiceAccountItem[]
		>;
		for (const account of accounts) grouped[account.service].push(account);
		return grouped;
	});

	const states = $derived<Record<ServiceKind, { status: ServiceStatus; [key: string]: unknown }>>({
		plex,
		lastfm,
		listenbrainz,
		hyperfix
	});

	let statuses = $derived<Record<ServiceKind, ServiceStatus>>({
		plex: plex.status,
		lastfm: lastfm.status,
		listenbrainz: listenbrainz.status,
		hyperfix: hyperfix.status
	});

	// What the chip shows beside "Connected": a username, or the instance for hyperfix.
	let connectedNames = $derived<Record<ServiceKind, string | undefined>>({
		plex: plex.username,
		lastfm: lastfm.username,
		listenbrainz: listenbrainz.username,
		hyperfix: hyperfix.url
	});

	async function refresh() {
		accounts = await loadAccounts(fetch);
	}

	function slugFrom(label: string): string {
		return label
			.toLowerCase()
			.replace(/[^a-z0-9]+/g, '-')
			.replace(/^-+|-+$/g, '')
			.slice(0, 32);
	}

	function startAdding(service: ServiceKind) {
		adding = service;
		editing = null;
		newSlug = '';
		newLabel = '';
		slugTouched = false;
		error = '';
		if (service === 'plex') void loadServers();
	}

	async function loadServers() {
		loadingServers = true;
		error = '';
		try {
			const res = await fetch('/api/plex/servers');
			if (!res.ok) throw new Error(await res.text());
			const body: { servers: PlexServerItem[] } = await res.json();
			servers = body.servers;
			selectedServer = accounts.find((a) => a.service === 'plex')?.server_name ?? '';
		} catch (e) {
			error = e instanceof Error ? e.message : 'Could not list Plex servers';
		} finally {
			loadingServers = false;
		}
	}

	/** Switch the primary Plex server, keeping the login it was set up with. */
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
			await refresh();
		} catch (e) {
			error = e instanceof Error ? e.message : 'Could not switch server';
		} finally {
			busy = false;
		}
	}

	/** Add a second Plex server: same login, its own library. */
	async function addPlexServer() {
		if (!selectedServer || !newSlug) return;
		busy = true;
		error = '';
		try {
			const res = await fetch('/api/plex/server', {
				method: 'POST',
				headers: { 'Content-Type': 'application/json' },
				body: JSON.stringify({
					name: selectedServer,
					account: newSlug,
					label: newLabel || selectedServer
				})
			});
			if (!res.ok) throw new Error(await res.text());
			adding = null;
			servers = [];
			await refresh();
		} catch (e) {
			error = e instanceof Error ? e.message : 'Could not add that server';
		} finally {
			busy = false;
		}
	}

	// ── Disconnect ────────────────────────────────────────────────────────────
	async function disconnect(account: ServiceAccountItem) {
		busy = true;
		error = '';
		try {
			const query = account.primary
				? ''
				: `?account=${encodeURIComponent(account.id.split(':')[1])}`;
			const res = await fetch(`/api/${account.service}/auth${query}`, { method: 'DELETE' });
			if (!res.ok) throw new Error(await res.text());
			const body: { status: ServiceStatus; username?: string; url?: string } = await res.json();
			const state = states[account.service];
			if (account.primary) {
				state.status = body.status;
				state.username = body.username;
				state.url = body.url;
				if (account.service === 'plex') {
					plex.server_name = undefined;
					plex.server_url = undefined;
				}
			}
			confirming = null;
			editing = null;
			await refresh();
		} catch (e) {
			error = e instanceof Error ? e.message : 'Could not disconnect';
		} finally {
			busy = false;
		}
	}

	const services: { service: ServiceKind; name: string; blurb: string }[] = [
		{
			service: 'plex',
			name: 'Plex',
			blurb: 'Source of your media ratings'
		},
		{
			service: 'lastfm',
			name: 'Last.fm',
			blurb: 'Love every track at or above the cutoff'
		},
		{
			service: 'listenbrainz',
			name: 'ListenBrainz',
			blurb: 'Love or hate every track at or above the cutoff'
		},
		{
			service: 'hyperfix',
			name: 'Hyperfix',
			blurb: 'Source of ratings, instead of Plex (read-only)'
		}
	];
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
				the next run reconciles against it. A second account of the same service is optional: add
				one when you have another Plex server, or a new Last.fm or ListenBrainz account to move to.
			</p>

			{#if error}
				<p class="mt-4 text-sm text-destructive" role="alert">{error}</p>
			{/if}

			<div class="mt-6 space-y-2">
				{#each services as service (service.service)}
					{@const fromEnvironment = rows[service.service].some((a) => a.managed === 'settings')}
					<section class="panel px-4 py-3.5">
						<div class="flex items-center gap-3">
							<ServiceMark service={service.service} size={26} />
							<div class="min-w-0 flex-1">
								<h2 class="text-sm font-medium">{service.name}</h2>
								<p class="text-xs text-muted-foreground">
									{service.blurb}{#if service.service === 'plex' && plex.server_name}
										· {plex.server_name}{/if}
								</p>
							</div>
							<ConnectionChip
								status={statuses[service.service]}
								username={connectedNames[service.service]}
							/>
						</div>

						{#if rows[service.service].length > 0}
							<div class="mt-3 space-y-1.5 border-t border-border pt-3">
								{#each rows[service.service] as account (account.id)}
									<AccountRow {account} {busy} ondisconnect={() => (confirming = account.id)} />
									{#if confirming === account.id}
										<div class="rounded-md border border-destructive/40 px-2.5 py-2">
											<p class="text-sm text-warn">
												{#if account.service === 'plex' && account.primary}
													The login this app checks is signed with your Plex token. Disconnecting
													Plex removes the only login RatingRelay has and leaves it open to anyone
													who can reach it, until you sign in again.
												{:else if account.service === 'plex'}
													Forget this server. The login and your other servers stay as they are.
												{:else if account.service === 'lastfm'}
													Forget the Last.fm API key, secret and password for this account. What was
													already written stays written.
												{:else if account.service === 'listenbrainz'}
													Forget this ListenBrainz token. Feedback already submitted stays
													submitted.
												{:else}
													Forget this hyperfix address and key. What was already written to the
													other services stays written.
												{/if}
											</p>
											<div class="mt-2 flex gap-2">
												<Button
													variant="destructive"
													class="h-8 px-3 text-sm"
													onclick={() => disconnect(account)}
													disabled={busy}>Disconnect anyway</Button
												>
												<Button
													variant="outline"
													class="h-8 px-3 text-sm"
													onclick={() => (confirming = null)}>Cancel</Button
												>
											</div>
										</div>
									{/if}
								{/each}
							</div>
						{/if}

						{#if editing === service.service}
							<div class="mt-3 border-t border-border pt-3">
								{#if service.service === 'lastfm'}
									<LastFmForm
										submitLabel="Replace credentials"
										onsubmitted={async (username) => {
											lastfm.status = 'connected';
											lastfm.username = username;
											editing = null;
											await refresh();
										}}
									/>
								{:else if service.service === 'listenbrainz'}
									<ListenBrainzForm
										submitLabel="Replace token"
										onsubmitted={async (username) => {
											listenbrainz.status = 'connected';
											listenbrainz.username = username;
											editing = null;
											await refresh();
										}}
									/>
								{:else if service.service === 'hyperfix'}
									<HyperfixForm
										submitLabel="Replace key"
										onsubmitted={async (url) => {
											hyperfix.status = 'connected';
											hyperfix.url = url;
											editing = null;
											await refresh();
										}}
									/>
								{/if}
								<Button
									variant="ghost"
									class="mt-2 h-8 px-3 text-sm"
									onclick={() => (editing = null)}>Cancel</Button
								>
							</div>
						{:else if adding === service.service}
							<div class="mt-3 space-y-2 border-t border-border pt-3">
								<p class="text-sm text-muted-foreground">
									{#if service.service === 'plex'}
										A second server of the same Plex login. Give it a short name — a relay reaches
										it as <code>plex:{newSlug || 'name'}</code>.
									{:else}
										A second {service.name} account. Give it a short name — a relay reaches it as
										<code>{service.service}:{newSlug || 'name'}</code>.
									{/if}
								</p>
								{#if service.service === 'plex'}
									{#if servers.length > 0}
										<Select
											bind:value={selectedServer}
											class="min-w-56"
											label="Plex server"
											options={servers.map((srv) => ({ value: srv.name, label: srv.name }))}
										/>
									{:else}
										<Button
											variant="outline"
											class="h-8 px-3 text-sm"
											onclick={loadServers}
											disabled={loadingServers}
										>
											{loadingServers ? 'Loading servers…' : 'List servers'}
										</Button>
									{/if}
								{/if}
								<div class="grid gap-2 sm:grid-cols-2">
									<Input
										bind:value={newLabel}
										placeholder="Name on this page (e.g. Old account)"
										spellcheck={false}
										oninput={() => {
											if (!slugTouched) newSlug = slugFrom(newLabel);
										}}
									/>
									<Input
										bind:value={newSlug}
										placeholder="Short name (e.g. old)"
										autocomplete="off"
										spellcheck={false}
										oninput={() => (slugTouched = true)}
									/>
								</div>
								{#if !newSlug}
									<p class="text-xs text-muted-foreground">
										Give it a short name above and the form appears.
									</p>
								{:else if service.service === 'hyperfix'}
									<p class="text-sm text-muted-foreground">
										One hyperfix instance is enough for one relay: it holds every rating, so a
										second account would read the same feed.
									</p>
								{:else if service.service !== 'plex'}
									{#if service.service === 'lastfm'}
										<LastFmForm
											submitLabel={newSlug ? 'Add this account' : 'Name it first'}
											account={newSlug}
											label={newLabel || undefined}
											onsubmitted={async () => {
												adding = null;
												await refresh();
											}}
										/>
									{:else}
										<ListenBrainzForm
											submitLabel={newSlug ? 'Add this account' : 'Name it first'}
											account={newSlug}
											label={newLabel || undefined}
											onsubmitted={async () => {
												adding = null;
												await refresh();
											}}
										/>
									{/if}
								{:else}
									<Button
										class="h-8 px-3 text-sm"
										onclick={addPlexServer}
										disabled={busy || !selectedServer || !newSlug}>Add this server</Button
									>
								{/if}
								<Button variant="ghost" class="h-8 px-3 text-sm" onclick={() => (adding = null)}
									>Cancel</Button
								>
							</div>
						{:else if service.service === 'plex'}
							<div class="mt-3 flex flex-wrap items-center gap-2 border-t border-border pt-3">
								{#if statuses.plex !== 'connected'}
									<p class="text-sm text-muted-foreground">
										Plex is not connected. Signing in to Plex is part of
										<a class="text-primary hover:underline" href={resolve('/')}>setup</a> — the login
										window has to come from there.
									</p>
								{:else}
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
										variant="outline"
										class="ml-auto h-8 px-3 text-sm"
										onclick={() => startAdding('plex')}>Add another server</Button
									>
								{/if}
							</div>
						{:else if statuses[service.service] === 'connected'}
							<div class="mt-3 flex flex-wrap gap-2 border-t border-border pt-3">
								{#if fromEnvironment}
									<p class="text-sm text-muted-foreground">
										This account's credentials come from the environment, so they are changed where
										the deployment sets them, not here. What the relay has already written is
										untouched either way.
									</p>
								{:else}
									<Button
										variant="outline"
										class="h-8 px-3 text-sm"
										onclick={() => (editing = service.service)}>Replace credentials</Button
									>
								{/if}
								{#if service.service !== 'hyperfix'}
									<Button
										variant="outline"
										class="h-8 px-3 text-sm"
										onclick={() => startAdding(service.service)}>Add another account</Button
									>
								{/if}
							</div>
						{:else}
							<div class="mt-3 border-t border-border pt-3">
								{#if service.service === 'lastfm'}
									<LastFmForm
										onsubmitted={async (username) => {
											lastfm.status = 'connected';
											lastfm.username = username;
											await refresh();
										}}
									/>
								{:else if service.service === 'listenbrainz'}
									<ListenBrainzForm
										onsubmitted={async (username) => {
											listenbrainz.status = 'connected';
											listenbrainz.username = username;
											await refresh();
										}}
									/>
								{:else}
									<HyperfixForm
										onsubmitted={async (url) => {
											hyperfix.status = 'connected';
											hyperfix.url = url;
											await refresh();
										}}
									/>
								{/if}
							</div>
						{/if}
					</section>
				{/each}
			</div>
		</div>
	</main>
</div>
