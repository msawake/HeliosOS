import { cn } from '@/lib/utils';

// Metric card: label over value over meta. Value is mono+tabular by default
// (the data voice); pass `display` for the serif voice on hero stats.

interface StatCardProps extends React.HTMLAttributes<HTMLDivElement> {
  label: string;
  value: React.ReactNode;
  /** Small line under the value: deltas, periods, caveats. */
  meta?: React.ReactNode;
  /** Right-aligned slot in the label row (e.g. an action link). */
  action?: React.ReactNode;
  display?: boolean;
}

export function StatCard({
  label,
  value,
  meta,
  action,
  display = false,
  className,
  ...props
}: StatCardProps) {
  return (
    <div
      data-slot="stat-card"
      className={cn('rounded-lg border border-edge bg-surface p-5 shadow-xs', className)}
      {...props}
    >
      <div className="flex items-center justify-between gap-2">
        <p className="text-[13px] text-tertiary">{label}</p>
        {action}
      </div>
      <div
        className={cn(
          'mt-1.5 text-2xl text-primary',
          display ? 'font-display font-semibold' : 'font-mono font-medium tracking-tight'
        )}
      >
        {value}
      </div>
      {meta ? <div className="mt-1 text-xs text-tertiary">{meta}</div> : null}
    </div>
  );
}
