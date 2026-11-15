<script lang="ts">
	import { goto } from '$app/navigation';
	import { resolve } from '$app/paths';
	import { Button } from '$lib/components/ui/button';
	import * as Card from '$lib/components/ui/card';
	import { serviceLabels } from '$lib/relay';
	import type { JobCreateRequest, JobData, MassUndoRefusal, TrackEvent } from '$lib/types/jobs';

	let { job, events }: { job: JobData; events: TrackEvent[] } = $props();

	/** The engine's reason for a refused reconcile. Select on it, never on the text. */
	const REFUSED = 'mass_undo_refused';

	const refusals = $derived.by((): MassUndoRefusal[] => {
		const byTarget: Record<string, MassUndoRefusal> = {};
		for (const evt of events) {
			if (evt.reason !== REFUSED) continue;
			byTarget[evt.target] ??= { target: evt.target, count: 0, detail: evt.error ?? '' };
			byTarget[evt.target].count += 1;
		}
		return Object.values(byTarget).sort((a, b) => a.target.localeCompare(b.target));
	});

	let submitting = $state(false);
	let error = $state('');

	/**
	 * The same relay, once, with the guard off. Never recurring: the override is
	 * for a run someone looked at, and the API refuses the two together.
	 */
	async function runAllowingUndo() {
		submitting = true;
		error = '';
		const body: JobCreateRequest = {
			source: job.source_config,
			targets: job.targets_config,
			recurring: false,
			dry_run: job.dry_run,
			allow_mass_undo: true
		};
		try {
			const res = await fetch('/api/relay/jobs', {
				method: 'POST',
				headers: { 'Content-Type': 'application/json' },
				body: JSON.stringify(body)
			});
			if (!res.ok) {
				const err = await res.json().catch(() => ({ detail: 'Could not queue the run' }));
				error = err.detail ?? 'Could not queue the run';
				return;
			}
			const created: JobData = await res.json();
			await goto(resolve('/jobs/[id]', { id: created.id }));
		} catch {
			error = 'Network error';
		} finally {
			submitting = false;
		}
	}
</script>

{#if refusals.length > 0}
	<Card.Root class="border-destructive/50" role="alert">
		<Card.Header>
			<Card.Title class="text-destructive">Undo refused</Card.Title>
			<Card.Description>
				This run would have taken back most of what RatingRelay wrote, so it took back nothing. A
				source that answered with an empty or short list looks exactly like this. Check the source
				still has your ratings before allowing it.
			</Card.Description>
		</Card.Header>
		<Card.Content class="space-y-3">
			<ul class="space-y-1 text-sm">
				{#each refusals as refusal (refusal.target)}
					<li>
						<span class="text-foreground">{serviceLabels[refusal.target] ?? refusal.target}</span>
						<span class="text-muted-foreground"> — {refusal.detail}</span>
					</li>
				{/each}
			</ul>
			<div class="flex flex-wrap items-center gap-3">
				<Button variant="destructive" disabled={submitting} onclick={runAllowingUndo}>
					{#if submitting}
						Queueing…
					{:else if job.dry_run}
						Preview again with the undo allowed
					{:else}
						Run once and allow the undo
					{/if}
				</Button>
				<span class="text-xs text-muted-foreground">
					Applies to that one run; scheduled runs keep refusing.
				</span>
			</div>
			{#if error}
				<p class="text-sm text-destructive">{error}</p>
			{/if}
		</Card.Content>
	</Card.Root>
{/if}
