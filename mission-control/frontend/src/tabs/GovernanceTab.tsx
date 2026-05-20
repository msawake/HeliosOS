import type { Approval, AuditEvent } from "@/lib/api";
import { api } from "@/lib/api";
import { ago } from "@/lib/utils";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";

interface Props {
  pending: Approval[];
  kernelEvts: AuditEvent[];
  onChange: () => void;
}

const PRIORITY_CLS: Record<string, string> = {
  critical: "text-danger font-bold",
  high: "text-orange font-bold",
  medium: "text-warn",
  low: "text-dim",
};

export function GovernanceTab({ pending, kernelEvts, onChange }: Props) {
  const approve = async (id: string) => {
    if (!confirm("Approve?")) return;
    await api.approve(id);
    onChange();
  };
  const reject = async (id: string) => {
    if (!confirm("Reject?")) return;
    await api.reject(id);
    onChange();
  };

  return (
    <div className="grid h-full grid-cols-2">
      <div className="overflow-y-auto border-r border-border">
        <div className="border-b border-border bg-surface px-[14px] py-2 text-[10px] uppercase tracking-widest text-dim">
          HITL Inbox — Pending Human Approvals
        </div>
        {pending.length === 0 ? (
          <div className="p-10 text-center text-dim">
            No pending approvals. Agents are operating autonomously.
          </div>
        ) : (
          pending.map((h) => {
            const pri = h.priority ?? "medium";
            return (
              <Card key={h.id}>
                <div className="text-[10px] text-dim">
                  <span className={PRIORITY_CLS[pri] ?? "text-dim"}>
                    {pri.toUpperCase()}
                  </span>{" "}
                  · {h.from_agent ?? h.agent_id ?? "?"} ·{" "}
                  {ago(h.created_at ?? h.timestamp)}
                </div>
                <div className="my-1 leading-relaxed text-bright">
                  {h.question ??
                    h.message ??
                    h.body ??
                    JSON.stringify(h).substring(0, 300)}
                </div>
                <div className="mt-2 space-x-1">
                  <Button variant="ok" onClick={() => approve(h.id)}>
                    ✓ Approve
                  </Button>
                  <Button variant="reject" onClick={() => reject(h.id)}>
                    ✗ Reject
                  </Button>
                </div>
              </Card>
            );
          })
        )}
      </div>

      <div className="overflow-y-auto">
        <div className="border-b border-border bg-surface px-[14px] py-2 text-[10px] uppercase tracking-widest text-dim">
          Kernel Decision Feed — Allow / Deny
        </div>
        {kernelEvts.length === 0 ? (
          <div className="p-10 text-center text-dim">No kernel events recorded yet.</div>
        ) : (
          kernelEvts.slice(0, 50).map((e, i) => {
            const action = e.action ?? e.event ?? e.type ?? "";
            const isDeny = action.includes("deny") || action.includes("fail");
            const det = (e.details ?? {}) as Record<string, unknown>;
            const actor =
              e.actor ?? e.agent_id ?? (det.agent_id as string) ?? "-";
            const resource =
              (det.name as string) ??
              (det.tool as string) ??
              (det.object as string) ??
              e.resource_id ??
              action;
            const outcome =
              e.outcome ?? (det.outcome as string) ?? (isDeny ? "denied" : "success");
            const time = (e.created_at ?? e.timestamp ?? "").substring(11, 19);
            const ok = outcome === "success";
            return (
              <div
                key={i}
                className="flex items-center gap-2 border-b border-border px-3 py-[5px] text-[11px]"
              >
                <span className="min-w-[55px] text-dim">{time}</span>
                <span className="min-w-[130px] text-info">{actor}</span>
                <span className="text-warn">{String(resource).substring(0, 35)}</span>
                <span className={ok ? "text-ok" : "text-danger"}>
                  {ok ? "✓" : "✗"} {outcome.toUpperCase()}
                </span>
                <span className="ml-1 text-dim">{action}</span>
              </div>
            );
          })
        )}
      </div>
    </div>
  );
}
