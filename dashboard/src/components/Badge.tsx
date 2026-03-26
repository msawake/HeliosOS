import { cn } from '@/lib/utils';

interface BadgeProps {
  label: string;
  variant?: string;
  className?: string;
}

export function Badge({ label, variant, className }: BadgeProps) {
  const badgeClass = variant ? `badge-${variant}` : '';
  return (
    <span className={cn('badge', badgeClass, className)}>
      {label}
    </span>
  );
}
