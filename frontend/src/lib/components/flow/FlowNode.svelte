<script lang="ts">
	import type { Snippet } from 'svelte';
	import { cn } from '$lib/utils';

	let {
		label,
		tone = 'neutral',
		ghost = false,
		class: className,
		actions,
		children
	}: {
		/** The port name on the card: FROM, TO. */
		label: string;
		tone?: 'source' | 'target' | 'danger' | 'neutral';
		/** An unallocated node: dashed, no fill, and it invites a click. */
		ghost?: boolean;
		class?: string;
		actions?: Snippet;
		children?: Snippet;
	} = $props();

	const accent: Record<string, string> = {
		source: 'bg-primary',
		target: 'bg-emerald-500',
		danger: 'bg-destructive',
		neutral: 'bg-muted-foreground'
	};
</script>

<div
	class={cn(
		'relative w-full rounded-lg border bg-card text-card-foreground shadow-sm',
		ghost ? 'border-border/60 bg-transparent shadow-none' : 'border-border',
		!ghost && tone === 'danger' && 'border-destructive/40',
		className
	)}
	data-tone={tone}
>
	<div class="flex items-center gap-2.5 px-5 pt-3">
		<span class={cn('size-2 shrink-0 rounded-full', accent[tone])} aria-hidden="true"></span>
		<span
			class="font-heading text-xs font-semibold tracking-[0.18em] text-muted-foreground uppercase"
		>
			{label}
		</span>
		<div class="ml-auto flex items-center gap-1.5">
			{@render actions?.()}
		</div>
	</div>
	<div class="px-5 pt-3 pb-4">
		{@render children?.()}
	</div>
</div>
