import type { Agent, AgentProcess } from "@/lib/api";
import { ago, fmt, usd } from "@/lib/utils";
import { PhaseBadge } from "./PhaseBadge";
import { Sheet } from "@/components/ui/sheet";
import { StackBadge } from "./StackBadge";

interface Props {
  open: boolean;
  onClose: () => void;
  agent?: Agent;
  proc?: AgentProcess;
}

function DetailRow({ k, v }: { k: string; v: React.ReactNode }) {
  return (
    <div className="flex justify-between border-b border-border py-1">
      <span className="text-dim">{k}</span>
      <span className="max-w-[260px] truncate text-bright">{v}</span>
    </div>
  );
}

function ManifestSection({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <div className="mt-3 rounded-md border border-border bg-bg p-[10px]">
      <h4 className="mb-[6px] text-[11px] uppercase tracking-wider text-warn">{title}</h4>
      {children}
    </div>
  );
}

export function AgentDetailSheet({ open, onClose, agent, proc }: Props) {
  if (!agent && !proc) {
    return (
      <Sheet open={open} onClose={onClose} title="Agent Detail">
        <div className="text-dim">No agent selected.</div>
      </Sheet>
    );
  }
  const a = agent ?? ({} as Agent);
  const name = a.name ?? "?";
  const meta = a.metadata as { model?: string } | undefined;

  return (
    <Sheet open={open} onClose={onClose} title={name}>
      <DetailRow
        k="Name"
        v={<span className="font-bold text-bright">{name}</span>}
      />
      <DetailRow k="Stack" v={<StackBadge stack={a.stack} />} />
      <DetailRow k="Execution Type" v={a.execution_type ?? "-"} />
      <DetailRow k="Namespace" v={a.namespace ?? "default"} />
      <DetailRow k="Model" v={a.model ?? meta?.model ?? "-"} />
      <DetailRow k="Tools" v={`${(a.tools ?? []).length} registered`} />

      {proc && (
        <ManifestSection title="Process State">
          <pre className="whitespace-pre-wrap break-all text-[10px] leading-relaxed text-text">
            {`Phase:      `}
            <PhaseBadge phase={proc.phase} />
            {`
PID:        ${proc.pid ?? "-"}
Tokens:     ${fmt(proc.tokens)}
Cost:       ${usd(proc.dollars)}
Tool Calls: ${proc.tool_calls ?? 0}
Heartbeat:  ${ago(proc.last_heartbeat)}`}
          </pre>
        </ManifestSection>
      )}

      {!!(a.tools?.length) && (
        <ManifestSection title="Registered Tools">
          <pre className="whitespace-pre-wrap text-[10px] text-ok">
            {a.tools.map((t) => `  ✓ ${t}`).join("\n")}
          </pre>
        </ManifestSection>
      )}

      {a.system_prompt && (
        <ManifestSection title="System Prompt">
          <pre className="whitespace-pre-wrap break-all text-[10px] leading-relaxed text-text">
            {a.system_prompt.substring(0, 600) +
              (a.system_prompt.length > 600 ? "\n..." : "")}
          </pre>
        </ManifestSection>
      )}

      {a.metadata && Object.keys(a.metadata).length > 0 && (
        <ManifestSection title="Metadata">
          <pre className="whitespace-pre-wrap break-all text-[10px] leading-relaxed text-text">
            {JSON.stringify(a.metadata, null, 2).substring(0, 800)}
          </pre>
        </ManifestSection>
      )}
    </Sheet>
  );
}
