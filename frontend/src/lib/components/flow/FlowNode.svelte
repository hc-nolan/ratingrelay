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
		/** The port name on the card: FROM, TO, TRIGGER. */
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
		target: 'bg-ok',
		danger: 'bg-destructive',
		neutral: 'bg-muted-foreground'
	};
</script>

<div
	class={cn(
		'relative w-full rounded-lg border bg-card text-card-foreground',
		ghost ? 'border-dashed border-border bg-transparent' : 'border-border',
		!ghost && tone === 'danger' && 'border-destructive/40',
		className
	)}
	data-tone={tone}
>
	<div class="flex items-center gap-2 px-4 pt-2.5">
		<span class={cn('size-1.5 shrink-0 rounded-full', accent[tone])} aria-hidden="true"></span>
		<span class="eyebrow">{label}</span>
		<div class="ml-auto flex items-center gap-1">
			{@render actions?.()}
		</div>
	</div>
	<div class="px-4 pt-2 pb-3.5">
		{@render children?.()}
	</div>
</div>
