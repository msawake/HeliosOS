import type { Icon } from '@phosphor-icons/react';

import { cn } from '@/lib/utils';

// Composed empty state: a quiet icon medallion, one sentence, and the action
// that fills the surface. Never an unexplained blank table.

interface EmptyStateProps extends React.HTMLAttributes<HTMLDivElement> {
  icon?: Icon;
  title: string;
  description?: string;
  action?: React.ReactNode;
}

export function EmptyState({
  icon: IconCmp,
  title,
  description,
  action,
  className,
  ...props
}: EmptyStateProps) {
  return (
    <div
      data-slot="empty-state"
      className={cn(
        'flex flex-col items-center justify-center gap-1 rounded-lg border border-dashed border-edge px-6 py-14 text-center',
        className
      )}
      {...props}
    >
      {IconCmp ? (
        <span className="mb-3 flex h-11 w-11 items-center justify-center rounded-full border border-edge bg-inset">
          <IconCmp className="h-5 w-5 text-tertiary" aria-hidden />
        </span>
      ) : null}
      <p className="text-sm font-medium text-primary">{title}</p>
      {description ? <p className="max-w-sm text-[13px] text-tertiary">{description}</p> : null}
      {action ? <div className="mt-4">{action}</div> : null}
    </div>
  );
}
