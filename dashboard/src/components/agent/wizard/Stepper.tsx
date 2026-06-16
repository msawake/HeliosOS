import { Check } from '@phosphor-icons/react';

import { cn } from '@/lib/utils';

/** Horizontal progress header for the wizard. Completed steps are clickable to
 *  jump back; forward navigation is gated by the orchestrator (validation). */
export function Stepper({
  steps,
  current,
  onJump,
}: {
  steps: string[];
  current: number;
  onJump: (index: number) => void;
}) {
  return (
    <ol className="flex flex-wrap items-center gap-x-1.5 gap-y-2">
      {steps.map((title, i) => {
        const done = i < current;
        const active = i === current;
        return (
          <li key={title} className="flex items-center gap-1.5">
            <button
              type="button"
              disabled={i > current}
              onClick={() => i <= current && onJump(i)}
              className={cn(
                'flex items-center gap-1.5 rounded-full border px-2.5 py-1 text-[12px] transition-colors',
                active && 'border-accent/40 bg-accent-wash text-accent',
                done && 'border-edge text-secondary hover:bg-surface-hover cursor-pointer',
                !active && !done && 'border-edge-subtle text-muted cursor-default',
              )}
              aria-current={active ? 'step' : undefined}
            >
              <span
                className={cn(
                  'flex h-4 w-4 items-center justify-center rounded-full text-[10px] font-medium',
                  active && 'bg-accent text-paper',
                  done && 'bg-success text-paper',
                  !active && !done && 'bg-inset text-muted',
                )}
              >
                {done ? <Check className="h-2.5 w-2.5" weight="bold" aria-hidden /> : i + 1}
              </span>
              {title}
            </button>
            {i < steps.length - 1 ? <span className="text-muted">·</span> : null}
          </li>
        );
      })}
    </ol>
  );
}
