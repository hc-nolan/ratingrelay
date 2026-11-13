<script lang="ts">
	import { page } from '$app/state';
	import { resolve } from '$app/paths';
	import { cn } from '$lib/utils';
	import type { Snippet } from 'svelte';

	let { status }: { status?: Snippet } = $props();

	/** Every route in the app hangs off the same three doors. */
	const links = [
		{ href: resolve('/'), match: '/', label: 'Relay' },
		{ href: resolve('/history'), match: '/history', label: 'History' },
		{ href: resolve('/unmatched'), match: '/unmatched', label: 'Unmatched' },
		{ href: resolve('/services'), match: '/services', label: 'Services' }
	];

	let unmatched = $state<number | null>(null);
	let signedIn = $state(false);
	let signingOut = $state(false);

	$effect(() => {
		let live = true;
		fetch('/api/relay/unmatched')
			.then((r) => (r.ok ? r.json() : []))
			.then((rows: { has_mapping: boolean }[]) => {
				if (live) unmatched = rows.filter((r) => !r.has_mapping).length;
			})
			.catch(() => {});
		fetch('/api/auth/status')
			.then((r) => (r.ok ? r.json() : null))
			.then((s) => {
				if (live) signedIn = !!(s?.required && s?.authenticated);
			})
			.catch(() => {});
		return () => {
			live = false;
		};
	});

	async function signOut() {
		signingOut = true;
		await fetch('/api/auth/logout', { method: 'POST' }).catch(() => {});
		location.reload();
	}

	function isActive(href: string): boolean {
		const path = page.url.pathname.replace(/\/$/, '');
		return href === '/' ? path === '' : path === href;
	}
</script>

<header class="flex shrink-0 items-center gap-5 border-b border-border px-5 py-2.5">
	<a href={resolve('/')} class="flex items-center gap-2.5 text-foreground">
		<span class="grid size-7 place-items-center rounded-md bg-primary text-primary-foreground">
			<svg
				viewBox="0 0 16 16"
				width="15"
				height="15"
				fill="none"
				stroke="currentColor"
				stroke-width="1.8"
				stroke-linecap="round"
				aria-hidden="true"
			>
				<path d="M3 12V8m5 4V3m5 9V6" />
			</svg>
		</span>
		<span class="text-lg font-semibold tracking-tight">RatingRelay</span>
	</a>

	<nav class="flex items-center gap-1" aria-label="Main">
		{#each links as link (link.href)}
			<a
				href={link.href}
				aria-current={isActive(link.match) ? 'page' : undefined}
				class={cn(
					'inline-flex items-center gap-1.5 rounded-md px-2.5 py-1.5 text-sm font-medium transition-colors',
					isActive(link.match)
						? 'bg-muted text-foreground'
						: 'text-muted-foreground hover:bg-muted/60 hover:text-foreground'
				)}
			>
				{link.label}
				{#if link.match === '/unmatched' && unmatched}
					<span
						class="rounded-full bg-warn/15 px-1.5 py-px font-mono text-xs text-warn tabular-nums"
					>
						{unmatched}
					</span>
				{/if}
			</a>
		{/each}
	</nav>

	<div class="ml-auto flex items-center gap-3">
		{@render status?.()}
		{#if signedIn}
			<button
				type="button"
				onclick={signOut}
				disabled={signingOut}
				class="rounded-md px-2 py-1.5 text-sm text-muted-foreground transition-colors hover:bg-muted hover:text-foreground disabled:opacity-50"
			>
				{signingOut ? 'Signing out…' : 'Sign out'}
			</button>
		{/if}
	</div>
</header>
