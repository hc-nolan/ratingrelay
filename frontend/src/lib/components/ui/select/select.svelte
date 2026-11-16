<script lang="ts">
	import { Select } from 'bits-ui';
	import { cn } from '$lib/utils';

	type Option = { value: string | number; label: string };

	let {
		value = $bindable<string | number>(),
		options,
		class: className,
		disabled = false,
		label,
		placeholder = 'Select…'
	}: {
		value: string | number;
		options: Option[];
		class?: string;
		disabled?: boolean;
		/** Accessible name for the trigger. */
		label?: string;
		placeholder?: string;
	} = $props();

	// bits-ui speaks strings; the callers keep their own value types (a star
	// rating is a number, a service is a string). Convert at the edge so the
	// page never has to care.
	// bits-ui holds the open string; the caller's value is the source of truth.
	let stringValue = $derived(String(value ?? ''));

	let current = $derived(
		options.find((o) => String(o.value) === String(value))?.label ?? placeholder
	);

	function pick(next: string) {
		const match = options.find((o) => String(o.value) === next);
		value = match ? match.value : next;
	}
</script>

<!-- Our own listbox rather than a <select>: the platform popup cannot be styled,
     so it arrives as the browser's grey menu with its own caret and checkmark.
     This is the same control in every browser. -->
<Select.Root
	type="single"
	items={options.map((o) => ({ value: String(o.value), label: o.label }))}
	{disabled}
	value={stringValue}
	onValueChange={(next) => pick(String(next))}
>
	<Select.Trigger
		aria-label={label}
		class={cn(
			'group flex h-8 items-stretch border border-border bg-background text-sm text-foreground transition-colors',
			'hover:border-border/80 data-[state=open]:border-primary/60',
			'focus-visible:border-primary/60 focus-visible:outline-none',
			'disabled:cursor-not-allowed disabled:opacity-50',
			className
		)}
	>
		<span class="flex min-w-0 flex-1 items-center px-2.5 text-left">
			<span class="truncate">{current}</span>
		</span>
		<span
			class="grid w-7 shrink-0 place-items-center border-l border-border/70 text-muted-foreground transition-colors group-hover:text-foreground"
			aria-hidden="true"
		>
			<svg viewBox="0 0 8 5" width="8" height="5" fill="currentColor">
				<path d="M0 0h8L4 5z" />
			</svg>
		</span>
	</Select.Trigger>

	<Select.Portal>
		<Select.Content
			class="z-50 max-h-72 min-w-[var(--bits-floating-anchor-width)] overflow-y-auto border border-border bg-popover text-popover-foreground shadow-[0_16px_40px_-24px_oklch(0_0_0/80%)]"
			sideOffset={4}
		>
			{#each options as option (option.value)}
				<Select.Item
					value={String(option.value)}
					label={option.label}
					class="relative flex cursor-pointer items-center gap-2 px-2.5 py-1.5 text-sm outline-none data-[highlighted]:bg-accent data-[highlighted]:text-accent-foreground data-[selected]:text-primary"
				>
					<span class="w-3 shrink-0">
						{#if String(option.value) === String(value)}
							<svg viewBox="0 0 12 12" width="10" height="10" fill="none" aria-hidden="true">
								<path
									d="M2 6.5l2.5 2.5L10 3.5"
									stroke="currentColor"
									stroke-width="1.8"
									stroke-linecap="round"
									stroke-linejoin="round"
								/>
							</svg>
						{/if}
					</span>
					<span class="truncate">{option.label}</span>
				</Select.Item>
			{/each}
		</Select.Content>
	</Select.Portal>
</Select.Root>
