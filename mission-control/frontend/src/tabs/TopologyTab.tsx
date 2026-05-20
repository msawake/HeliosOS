import type { Agent, AgentProcess } from "@/lib/api";
import { shortName } from "@/lib/utils";
import { StackBadge } from "@/components/StackBadge";
import { Table, Tbody, Td, Th, Thead, Tr } from "@/components/ui/table";

interface Props {
  agents: Agent[];
  procs: AgentProcess[];
  onSelect: (name: string) => void;
  onOpenManifest: (name: string) => void;
}

export function TopologyTab({ agents, procs, onSelect, onOpenManifest }: Props) {
  const nsByAgent: Record<string, Agent[]> = {};
  agents.forEach((a) => {
    const ns = a.namespace ?? "default";
    if (!nsByAgent[ns]) nsByAgent[ns] = [];
    nsByAgent[ns].push(a);
  });

  return (
    <div className="grid h-full grid-cols-2">
      <div className="overflow-y-auto border-r border-border">
        <div className="border-b border-border bg-surface px-[14px] py-2 text-[10px] uppercase tracking-widest text-dim">
          Agent Topology by Namespace
        </div>
        {Object.keys(nsByAgent).length === 0 ? (
          <div className="p-10 text-center text-dim">No agents registered.</div>
        ) : (
          Object.entries(nsByAgent).map(([ns, list]) => (
            <div key={ns} className="mx-[14px] my-3">
              <div className="mb-1 text-[10px] text-dim">
                ■ {ns} ({list.length} agents)
              </div>
              {list.map((a) => {
                const proc = procs.find((p) => shortName(p.name) === a.name);
                const ph = proc?.phase ?? "undeployed";
                const borderColor =
                  ph === "running"
                    ? "#3fb950"
                    : ph === "failed"
                      ? "#f85149"
                      : "#30363d";
                return (
                  <button
                    key={a.name}
                    onClick={() => onSelect(a.name)}
                    className="m-[3px] inline-flex items-center gap-1 rounded-sm border px-3 py-[5px] text-[11px] transition-colors hover:border-info hover:bg-info/5"
                    style={{ borderColor }}
                  >
                    <StackBadge stack={a.stack} short />
                    <span>{a.name}</span>
                  </button>
                );
              })}
            </div>
          ))
        )}
      </div>

      <div className="overflow-y-auto">
        <div className="border-b border-border bg-surface px-[14px] py-2 text-[10px] uppercase tracking-widest text-dim">
          Agent Registry
        </div>
        <Table>
          <Thead>
            <Tr>
              <Th>Name</Th>
              <Th>Stack</Th>
              <Th>Execution Type</Th>
              <Th>Tools</Th>
              <Th>Namespace</Th>
            </Tr>
          </Thead>
          <Tbody>
            {agents.map((a) => (
              <Tr key={a.name} onClick={() => onOpenManifest(a.name)}>
                <Td className="text-bright">{a.name}</Td>
                <Td>
                  <StackBadge stack={a.stack} />
                </Td>
                <Td>{a.execution_type ?? "-"}</Td>
                <Td>{(a.tools ?? []).length}</Td>
                <Td>{a.namespace ?? "default"}</Td>
              </Tr>
            ))}
          </Tbody>
        </Table>
      </div>
    </div>
  );
}
