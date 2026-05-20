import type { Agent, AgentProcess } from "@/lib/api";
import { ago, fmt, shortName, usd } from "@/lib/utils";
import { PhaseBadge } from "@/components/PhaseBadge";
import { StackBadge } from "@/components/StackBadge";

interface Props {
  agents: Agent[];
  procs: AgentProcess[];
  selected?: string;
  onSelect: (name: string) => void;
}

function ManifestDetail({ agent, proc }: { agent: Agent; proc?: AgentProcess }) {
  const meta = agent.metadata as { model?: string } | undefined;
  return (
    <div>
      <DetailRow k="Name" v={<span className="font-bold text-bright">{agent.name}</span>} />
      <DetailRow k="Stack" v={<StackBadge stack={agent.stack} />} />
      <DetailRow k="Execution Type" v={agent.execution_type ?? "-"} />
      <DetailRow k="Namespace" v={agent.namespace ?? "default"} />
      <DetailRow k="Model" v={agent.model ?? meta?.model ?? "-"} />
      <DetailRow k="Tools" v={`${(agent.tools ?? []).length} registered`} />

      {proc && (
        <Section title="Process State">
          <pre className="text-[10px] leading-relaxed text-text">
            {`Phase:      ${proc.phase.toUpperCase()}
PID:        ${proc.pid ?? "-"}
Tokens:     ${fmt(proc.tokens)}
Cost:       ${usd(proc.dollars)}
Tool Calls: ${proc.tool_calls ?? 0}
Heartbeat:  ${ago(proc.last_heartbeat)}`}
          </pre>
        </Section>
      )}

      {!!agent.tools?.length && (
        <Section title="Registered Tools">
          <pre className="text-[10px] text-ok">
            {agent.tools.map((t) => `  ✓ ${t}`).join("\n")}
          </pre>
        </Section>
      )}

      {agent.system_prompt && (
        <Section title="System Prompt">
          <pre className="whitespace-pre-wrap text-[10px] leading-relaxed text-text">
            {agent.system_prompt.substring(0, 600) +
              (agent.system_prompt.length > 600 ? "\n..." : "")}
          </pre>
        </Section>
      )}
    </div>
  );
}

function DetailRow({ k, v }: { k: string; v: React.ReactNode }) {
  return (
    <div className="flex justify-between border-b border-border py-1">
      <span className="text-dim">{k}</span>
      <span className="max-w-[60%] truncate text-bright">{v}</span>
    </div>
  );
}

function Section({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <div className="mt-3 rounded-md border border-border bg-bg p-[10px]">
      <h4 className="mb-[6px] text-[11px] uppercase tracking-wider text-warn">{title}</h4>
      {children}
    </div>
  );
}

export function ManifestTab({ agents, procs, selected, onSelect }: Props) {
  const current = agents.find((a) => a.name === selected);
  const proc = current ? procs.find((p) => shortName(p.name) === current.name) : undefined;

  return (
    <div className="grid h-full grid-cols-[300px_1fr]">
      <div className="overflow-y-auto border-r border-border">
        <div className="border-b border-border bg-surface px-[14px] py-2 text-[10px] uppercase tracking-widest text-dim">
          Select Agent
        </div>
        {agents.length === 0 ? (
          <div className="p-10 text-center text-dim">No agents.</div>
        ) : (
          agents.map((a) => {
            const p = procs.find((x) => shortName(x.name) === a.name);
            const isSelected = a.name === selected;
            return (
              <button
                key={a.name}
                onClick={() => onSelect(a.name)}
                className={`block w-full cursor-pointer border-b border-border px-3 py-2 text-left ${
                  isSelected ? "bg-info/10" : ""
                }`}
              >
                <div className="flex items-center justify-between">
                  <span className="text-bright">{a.name}</span>
                  {p && <PhaseBadge phase={p.phase} className="text-[9px]" />}
                </div>
                <div className="mt-[2px] text-[10px] text-dim">
                  <StackBadge stack={a.stack} /> · {a.execution_type ?? "-"} ·{" "}
                  {(a.tools ?? []).length} tools
                </div>
              </button>
            );
          })
        )}
      </div>

      <div className="overflow-y-auto p-4">
        {current ? (
          <ManifestDetail agent={current} proc={proc} />
        ) : (
          <div className="p-10 text-center text-dim">
            Select an agent from the list to inspect its contract.
          </div>
        )}
      </div>
    </div>
  );
}
