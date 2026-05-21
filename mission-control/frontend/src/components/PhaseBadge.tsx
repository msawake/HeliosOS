import { Badge } from "@/components/ui/badge";
import { cn } from "@/lib/utils";

const PHASE_CLASSES: Record<string, string> = {
  running: "bg-ok/10 text-ok",
  scheduled: "bg-info/10 text-info",
  admitted: "bg-dim/10 text-dim",
  starting: "bg-info/10 text-info",
  pending: "bg-info/10 text-info",
  awaiting_human: "bg-warn/10 text-warn",
  draining: "bg-warn/10 text-warn",
  stopped: "bg-dim/10 text-dim",
  failed: "bg-danger/10 text-danger",
  quarantined: "bg-orange/10 text-orange",
  evicted: "bg-purple/10 text-purple",
};

export function PhaseBadge({
  phase,
  className,
  title,
}: {
  phase: string;
  className?: string;
  title?: string;
}) {
  const cls = PHASE_CLASSES[phase] ?? "bg-dim/10 text-dim";
  return (
    <Badge className={cn(cls, className)} title={title}>
      {phase.toUpperCase()}
    </Badge>
  );
}
