import type { AgentProcess } from "@/lib/api";
import { fmt, usd } from "@/lib/utils";
import { StackBadge } from "./StackBadge";

interface Props {
  procs: AgentProcess[];
  summary: { total?: number; running?: number; failed?: number; quarantined?: number };
}

function Stat({
  value,
  label,
  valueColor,
  borderColor,
}: {
  value: React.ReactNode;
  label: string;
  valueColor?: string;
  borderColor?: string;
}) {
  return (
    <div
      className="min-w-[70px] rounded-sm border bg-bg px-[10px] py-[3px] text-center"
      style={{ borderColor: borderColor ?? "var(--tw-border, #30363d)" }}
    >
      <div
        className="text-[16px] font-bold"
        style={{ color: valueColor ?? "#f0f6fc" }}
      >
        {value}
      </div>
      <div className="text-[8px] uppercase tracking-wider text-dim">{label}</div>
    </div>
  );
}

export function FleetBar({ procs, summary }: Props) {
  let tC = 0,
    tT = 0,
    tTl = 0;
  procs.forEach((p) => {
    tC += p.dollars ?? 0;
    tT += p.tokens ?? 0;
    tTl += p.tool_calls ?? 0;
  });
  const stks: Record<string, number> = {};
  procs.forEach((p) => {
    const s = p.stack ?? "unknown";
    stks[s] = (stks[s] ?? 0) + 1;
  });

  return (
    <div className="flex flex-shrink-0 flex-wrap items-center gap-[5px] border-b border-border bg-surface px-4 py-[6px]">
      <Stat value={summary.total ?? 0} label="Total" valueColor="#58a6ff" />
      <Stat value={summary.running ?? 0} label="Running" valueColor="#3fb950" />
      <Stat value={summary.failed ?? 0} label="Failed" valueColor="#f85149" />
      <Stat
        value={summary.quarantined ?? 0}
        label="Quarantined"
        valueColor="#db6d28"
        borderColor="#db6d28"
      />
      <div className="mx-[3px] h-7 w-px bg-border" />
      <Stat value={usd(tC)} label="Cost" />
      <Stat value={fmt(tT)} label="Tokens" />
      <Stat value={fmt(tTl)} label="Tools" />
      <div className="mx-[3px] h-7 w-px bg-border" />
      <div className="flex flex-wrap gap-[4px]">
        {Object.entries(stks).map(([s, c]) => (
          <StackBadge key={s} stack={`${s} ${c}`} />
        ))}
      </div>
    </div>
  );
}
