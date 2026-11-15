<script lang="ts">
	import type { Candidate } from '$lib/types/relay';

	let {
		candidates,
		onpick
	}: {
		candidates: Candidate[];
		onpick: (candidate: Candidate) => void;
	} = $props();
</script>

<div class="form-row">
	<span class="form-label">Did you mean…</span>
	<div class="candidate-list">
		{#each candidates as candidate, index (index)}
			<button type="button" class="candidate" onclick={() => onpick(candidate)}>
				<span class="candidate-name">{candidate.artist} – {candidate.title}</span>
				{#if candidate.recording_mbid}
					<span class="candidate-mbid">{candidate.recording_mbid.slice(0, 8)}…</span>
				{/if}
			</button>
		{/each}
	</div>
	<span class="form-hint">Pick one to fill the fields below, then save the mapping.</span>
</div>

<style>
	.candidate-list {
		display: flex;
		flex-direction: column;
		gap: 0.25rem;
	}

	.candidate {
		display: flex;
		align-items: baseline;
		justify-content: space-between;
		gap: 0.75rem;
		padding: 0.4rem 0.6rem;
		font: inherit;
		text-align: left;
		color: oklch(0.78 0.01 40);
		background: oklch(0.19 0.006 40);
		border: 1px solid oklch(0.26 0.008 40);
		border-radius: 4px;
		cursor: pointer;
		transition:
			border-color 0.15s,
			background 0.15s;
	}

	.candidate:hover {
		background: oklch(0.23 0.008 40);
		border-color: oklch(0.4 0.01 40);
	}

	.candidate-name {
		font-size: 0.8rem;
	}

	.candidate-mbid {
		font-size: 0.65rem;
		font-family: var(--font-mono, monospace);
		color: oklch(0.42 0.01 40);
		flex-shrink: 0;
	}

	.form-label {
		font-size: 0.7rem;
		letter-spacing: 0.06em;
		text-transform: uppercase;
		color: oklch(0.5 0.01 40);
		font-family: var(--font-heading);
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
</style>
