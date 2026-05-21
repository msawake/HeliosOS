import { useState } from "react";
import type { AgentProcess } from "@/lib/api";
import { api } from "@/lib/api";
import { ago, fmt, shortName, usd } from "@/lib/utils";
import { Button } from "@/components/ui/button";
import { ConfirmDialog } from "@/components/ui/confirm-dialog";
import { UploadAgentDialog } from "@/components/UploadAgentDialog";
import { PhaseBadge } from "@/components/PhaseBadge";
import { StackBadge } from "@/components/StackBadge";
import { Table, Tbody, Td, Th, Thead, Tr } from "@/components/ui/table";

interface Props {
  procs: AgentProcess[];
  onSelect: (pid: string) => void;
  onChange: () => void;
}

interface Alert {
  sev: "HIGH" | "MEDIUM" | "CRITICAL";
  color: string;
  msg: string;
}

export function FleetTab({ procs, onSelect, onChange }: Props) {
  const [pending, setPending] = useState<{
    pid: string;
    label: string;
    action: "stop" | "delete";
  } | null>(null);
  const [uploadOpen, setUploadOpen] = useState(false);

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

  const requestAction = (pid: string, label: string, action: "stop" | "delete") => {
    if (!pid) return;
    setPending({ pid, label, action });
  };

  const confirmAction = async () => {
    if (!pending) return;
    const { pid, action } = pending;
    setPending(null);
    await api.stop(pid);
    if (action === "delete") await api.remove(pid);
    onChange();
  };

  const toolbar = (
    <div className="flex items-center justify-between border-b border-border bg-bg px-[14px] py-2">
      <span className="text-[10px] uppercase tracking-widest text-dim">
        Fleet · {procs.length} process{procs.length === 1 ? "" : "es"}
      </span>
      <Button variant="ok" onClick={() => setUploadOpen(true)}>
        ↑ UPLOAD AGENT
      </Button>
    </div>
  );

  const uploadDialog = (
    <UploadAgentDialog
      open={uploadOpen}
      onClose={() => setUploadOpen(false)}
      onDeployed={onChange}
    />
  );

  if (!procs.length) {
    return (
      <div>
        {toolbar}
        <div className="p-10 text-center text-dim">
          No agent processes. Deploy agents to see them here.
        </div>
        {uploadDialog}
      </div>
    );
  }

  return (
    <div>
      {toolbar}
      <Table>
        <Thead>
          <Tr>
            <Th>Stack</Th>
            <Th>Name</Th>
            <Th>PID</Th>
            <Th>Phase</Th>
            <Th>Namespace</Th>
            <Th>Tokens</Th>
            <Th>Cost</Th>
            <Th>Tool Calls</Th>
            <Th>Wallclock</Th>
            <Th>Heartbeat</Th>
            <Th>Actions</Th>
          </Tr>
        </Thead>
        <Tbody>
          {procs.map((p, i) => {
            const sn = shortName(p.name);
            const pid = p.pid != null ? String(p.pid) : "";
            return (
              <Tr key={pid || `${sn}-${i}`} onClick={() => onSelect(pid)}>
                <Td>
                  <StackBadge stack={p.stack} />
                </Td>
                <Td className="text-bright">{sn}</Td>
                <Td className="font-mono text-[10px] text-dim">{pid || "-"}</Td>
                <Td>
                  <PhaseBadge
                    phase={p.display_phase ?? p.phase ?? "unknown"}
                    title={
                      p.display_phase === "scheduled" && p.next_run_at
                        ? `next run: ${new Date(p.next_run_at).toLocaleString()}`
                        : undefined
                    }
                  />
                </Td>
                <Td>{p.namespace ?? "-"}</Td>
                <Td>{fmt(p.tokens)}</Td>
                <Td>{usd(p.dollars)}</Td>
                <Td>{p.tool_calls ?? 0}</Td>
                <Td>-</Td>
                <Td>{ago(p.last_heartbeat)}</Td>
                <Td>
                  <div className="flex gap-1">
                    <Button
                      variant="ghost"
                      onClick={(e) => {
                        e.stopPropagation();
                        requestAction(pid, sn, "stop");
                      }}
                      disabled={p.phase === "stopped"}
                    >
                      STOP
                    </Button>
                    <Button
                      variant="danger"
                      onClick={(e) => {
                        e.stopPropagation();
                        requestAction(pid, sn, "delete");
                      }}
                    >
                      DELETE
                    </Button>
                  </div>
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

      <ConfirmDialog
        open={!!pending}
        destructive={pending?.action === "delete"}
        title={pending?.action === "delete" ? "Delete agent" : "Stop agent"}
        confirmLabel={pending?.action === "delete" ? "STOP & DELETE" : "STOP"}
        cancelLabel="CANCEL"
        message={
          pending && (
            <div className="space-y-2">
              <div>
                <span className="text-dim">name: </span>
                <span className="text-bright">{pending.label}</span>
              </div>
              <div>
                <span className="text-dim">pid:  </span>
                <span className="text-bright">{pending.pid}</span>
              </div>
              <div className="pt-2 text-warn">
                {pending.action === "delete"
                  ? "Stops the process and unregisters it. Permanent."
                  : "Transitions the process to STOPPED. The agent stays in the registry — you can RUN NOW to start it again."}
              </div>
            </div>
          )
        }
        onConfirm={confirmAction}
        onCancel={() => setPending(null)}
      />
      {uploadDialog}
    </div>
  );
}
