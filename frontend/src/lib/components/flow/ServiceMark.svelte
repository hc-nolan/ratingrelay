<script lang="ts">
	import type { SourceId } from '$lib/types/relay';

	type MarkId = SourceId;

	let { service, size = 16 }: { service: MarkId; size?: number } = $props();
</script>

{#snippet plex(s: number)}
	<svg viewBox="0 0 512 512" width={s} height={s} aria-hidden="true">
		<rect width="512" height="512" rx="15%" fill="#211a15" />
		<path d="M256 70H148l108 186-108 186h108l108-186z" fill="#e5a00d" />
	</svg>
{/snippet}

{#snippet lastfm(s: number)}
	<svg viewBox="0 0 24 24" fill="currentColor" width={s} height={s} aria-hidden="true">
		<path
			d="M10.584 17.21l-.88-2.392s-1.43 1.6-3.573 1.6c-1.898 0-3.244-1.65-3.244-4.29 0-3.38 1.703-4.594 3.38-4.594 2.42 0 3.19 1.565 3.85 3.576l.88 2.75c.88 2.673 2.53 4.815 7.315 4.815 3.41 0 5.73-1.045 5.73-3.8 0-2.227-1.265-3.38-3.63-3.93l-1.76-.385c-1.21-.275-1.57-.77-1.57-1.593 0-.935.737-1.483 1.95-1.483 1.318 0 2.03.494 2.14 1.67l2.75-.33c-.22-2.47-1.925-3.48-4.755-3.48-2.49 0-4.82.935-4.82 3.93 0 1.87.907 3.05 3.19 3.6l1.87.44c1.375.33 1.87.88 1.87 1.76 0 1.046-.99 1.483-2.862 1.483-2.75 0-3.9-1.43-4.562-3.38l-.91-2.75c-1.155-3.52-3-4.87-6.655-4.87C1.87 5.528 0 8.278 0 12.238c0 3.82 1.87 6.234 6.04 6.234 3.135 0 4.544-1.262 4.544-1.262z"
		/>
	</svg>
{/snippet}

{#snippet listenbrainz(s: number)}
	<svg viewBox="9,0,128,160" width={s} height={s} aria-hidden="true">
		<path d="m75.354 7.823v144l61-35v-74z" fill="#eb743b" />
		<path d="m70.354 7.823-61 35v74l61 35z" fill="#2b1c14" />
	</svg>
{/snippet}

{#snippet reset(s: number)}
	<svg
		viewBox="0 0 16 16"
		fill="none"
		stroke="currentColor"
		stroke-width="1.5"
		width={s}
		height={s}
		aria-hidden="true"
	>
		<path d="M8 2a6 6 0 1 0 4.5 2M8 2V5m0-3h3" stroke-linecap="round" stroke-linejoin="round" />
	</svg>
{/snippet}

{#if service === 'plex'}
	{@render plex(size)}
{:else if service === 'lastfm'}
	{@render lastfm(size)}
{:else if service === 'listenbrainz'}
	{@render listenbrainz(size)}
{:else}
	{@render reset(size)}
{/if}
