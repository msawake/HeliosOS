import { Badge } from "@/components/ui/badge";
import { cn } from "@/lib/utils";

const STACK_CLASSES: Record<string, string> = {
  forgeos: "bg-ok/10 text-ok",
  adk: "bg-info/10 text-info",
  crewai: "bg-purple/10 text-purple",
  sandbox: "bg-orange/10 text-orange",
  "anthropic-agent-sdk": "bg-pink/10 text-pink",
  "anthropic-managed": "bg-pink/10 text-pink",
  "openai-agents": "bg-cyan/10 text-cyan",
  openclaw: "bg-warn/10 text-warn",
};

export function StackBadge({
  stack,
  className,
  short = false,
}: {
  stack: string | undefined;
  className?: string;
  short?: boolean;
}) {
  const s = (stack ?? "unknown").toLowerCase();
  const cls = STACK_CLASSES[s] ?? "bg-dim/10 text-dim";
  return (
    <Badge className={cn(cls, className)}>
      {short ? (stack ?? "?").substring(0, 3) : stack ?? "unknown"}
    </Badge>
  );
}
