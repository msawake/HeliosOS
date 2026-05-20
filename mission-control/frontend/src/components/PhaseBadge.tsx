import { Badge } from "@/components/ui/badge";
import { cn } from "@/lib/utils";

const PHASE_CLASSES: Record<string, string> = {
  running: "bg-ok/10 text-ok",
  admitted: "bg-info/10 text-info",
  starting: "bg-info/10 text-info",
  pending: "bg-info/10 text-info",
  draining: "bg-warn/10 text-warn",
  stopped: "bg-dim/10 text-dim",
  failed: "bg-danger/10 text-danger",
  quarantined: "bg-orange/10 text-orange",
  evicted: "bg-purple/10 text-purple",
};

export function PhaseBadge({ phase, className }: { phase: string; className?: string }) {
  const cls = PHASE_CLASSES[phase] ?? "bg-dim/10 text-dim";
  return <Badge className={cn(cls, className)}>{phase.toUpperCase()}</Badge>;
}
