import type { AgentProcess } from "@/lib/api";
import { api } from "@/lib/api";
import { ago, fmt, shortName, usd } from "@/lib/utils";
import { Button } from "@/components/ui/button";
import { PhaseBadge } from "@/components/PhaseBadge";
import { StackBadge } from "@/components/StackBadge";
import { Table, Tbody, Td, Th, Thead, Tr } from "@/components/ui/table";

interface Props {
  procs: AgentProcess[];
  onSelect: (name: string) => void;
  onChange: () => void;
}

interface Alert {
  sev: "HIGH" | "MEDIUM" | "CRITICAL";
  color: string;
  msg: string;
}

export function FleetTab({ procs, onSelect, onChange }: Props) {
  const alerts: Alert[] = [];
  procs.forEach((p) => {
    const sn = shortName(p.name);
    if (p.phase === "failed")
      alerts.push({
        sev: "HIGH",
        color: "#db6d28",
        msg: `${sn} FAILED${p.last_error ? " — " + p.last_error : ""}`,
      });
    if (p.phase === "quarantined")
      alerts.push({
        sev: "CRITICAL",
        color: "#f85149",
        msg: `${sn} QUARANTINED — manual review needed`,
      });
    if (p.phase === "evicted")
      alerts.push({
        sev: "MEDIUM",
        color: "#d29922",
        msg: `${sn} EVICTED — preempted for resources`,
      });
  });

  const onStop = async (name: string) => {
    if (!confirm(`Stop and remove ${name}?`)) return;
    await api.stop(name);
    await api.remove(name);
    onChange();
  };

  if (!procs.length) {
    return (
      <div className="p-10 text-center text-dim">
        No agent processes. Deploy agents to see them here.
      </div>
    );
  }

  return (
    <div>
      <Table>
        <Thead>
          <Tr>
            <Th>Stack</Th>
            <Th>Name</Th>
            <Th>Phase</Th>
            <Th>Namespace</Th>
            <Th>Tokens</Th>
            <Th>Cost</Th>
            <Th>Tool Calls</Th>
            <Th>Wallclock</Th>
            <Th>Heartbeat</Th>
            <Th>Signals</Th>
          </Tr>
        </Thead>
        <Tbody>
          {procs.map((p) => {
            const sn = shortName(p.name);
            return (
              <Tr key={sn} onClick={() => onSelect(sn)}>
                <Td>
                  <StackBadge stack={p.stack} />
                </Td>
                <Td className="text-bright">{sn}</Td>
                <Td>
                  <PhaseBadge phase={p.phase ?? "unknown"} />
                </Td>
                <Td>{p.namespace ?? "-"}</Td>
                <Td>{fmt(p.tokens)}</Td>
                <Td>{usd(p.dollars)}</Td>
                <Td>{p.tool_calls ?? 0}</Td>
                <Td>-</Td>
                <Td>{ago(p.last_heartbeat)}</Td>
                <Td>
                  <Button
                    variant="danger"
                    onClick={(e) => {
                      e.stopPropagation();
                      onStop(sn);
                    }}
                  >
                    STOP
                  </Button>
                </Td>
              </Tr>
            );
          })}
        </Tbody>
      </Table>

      {alerts.length > 0 && (
        <div className="border-t-2 border-border">
          <div className="border-b border-border bg-surface px-[14px] py-2 text-[10px] uppercase tracking-widest text-dim">
            Alerts ({alerts.length})
          </div>
          {alerts.map((a, i) => (
            <div
              key={i}
              className="flex items-start gap-2 border-b border-border px-[14px] py-2"
            >
              <span
                className="min-w-[70px] font-bold"
                style={{ color: a.color }}
              >
                {a.sev}
              </span>
              <span>{a.msg}</span>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
