import { Badge } from '@/components/ui/badge';
import { statusVariant } from '@/lib/utils';

/** The one place a status string earns color (success/danger/warning/neutral). */
export function StatusBadge({ status, className }: { status?: string; className?: string }) {
  if (!status) return null;
  return (
    <Badge variant={statusVariant(status)} className={className}>
      {status.replace(/_/g, ' ')}
    </Badge>
  );
}
