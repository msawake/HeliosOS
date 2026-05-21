import type { AgentProcess } from "@/lib/api";
import { fmt, usd } from "@/lib/utils";
import { StackBadge } from "@/components/StackBadge";
import { ResizableSplit } from "@/components/ui/resizable-split";

interface Bucket {
  key: string;
  agents: number;
  dollars: number;
  tokens: number;
  toolCalls: number;
}

function aggregate(procs: AgentProcess[], keyFn: (p: AgentProcess) => string): Bucket[] {
  const m: Record<string, Bucket> = {};
  procs.forEach((p) => {
    const k = keyFn(p);
    if (!m[k]) m[k] = { key: k, agents: 0, dollars: 0, tokens: 0, toolCalls: 0 };
    m[k].agents++;
    m[k].dollars += p.dollars ?? 0;
    m[k].tokens += p.tokens ?? 0;
    m[k].toolCalls += p.tool_calls ?? 0;
  });
  return Object.values(m).sort((a, b) => b.dollars - a.dollars);
}

function Row({ bucket, max, color, isStack }: { bucket: Bucket; max: number; color: string; isStack: boolean }) {
  const pct = Math.max(2, (bucket.dollars / max) * 100);
  return (
    <div className="flex items-center gap-2 border-b border-border px-[14px] py-2">
      {isStack ? (
        <span className="min-w-[90px]">
          <StackBadge stack={bucket.key} />
        </span>
      ) : (
        <span className="min-w-[90px] text-info">{bucket.key}</span>
      )}
      <span className="min-w-[35px] text-right text-dim">{bucket.agents}</span>
      <div className="flex h-[18px] flex-1 overflow-hidden rounded-sm border border-border bg-bg">
        <div
          className="h-full transition-all"
          style={{ width: `${pct}%`, background: color }}
        />
      </div>
      <span className="min-w-[65px] text-right">{usd(bucket.dollars)}</span>
      <span className="min-w-[55px] text-right text-dim">{fmt(bucket.tokens)}</span>
      <span className="min-w-[45px] text-right text-dim">{bucket.toolCalls} tc</span>
    </div>
  );
}

export function CostTab({ procs }: { procs: AgentProcess[] }) {
  const byStack = aggregate(procs, (p) => p.stack ?? "unknown");
  const byNs = aggregate(procs, (p) => p.namespace ?? "default");
  const max = Math.max(
    0.01,
    ...byStack.map((x) => x.dollars),
    ...byNs.map((x) => x.dollars),
  );

  return (
    <ResizableSplit
      left={
        <div>
          <div className="border-b border-border bg-surface px-[14px] py-2 text-[10px] uppercase tracking-widest text-dim">
            Cost by Framework
          </div>
          {byStack.length === 0 ? (
            <div className="p-10 text-center text-dim">No cost data.</div>
          ) : (
            byStack.map((s) => <Row key={s.key} bucket={s} max={max} color="#58a6ff" isStack />)
          )}
        </div>
      }
      right={
        <div>
          <div className="border-b border-border bg-surface px-[14px] py-2 text-[10px] uppercase tracking-widest text-dim">
            Cost by Namespace
          </div>
          {byNs.length === 0 ? (
            <div className="p-10 text-center text-dim">No cost data.</div>
          ) : (
            byNs.map((n) => <Row key={n.key} bucket={n} max={max} color="#bc8cff" isStack={false} />)
          )}
        </div>
      }
    />
  );
}
