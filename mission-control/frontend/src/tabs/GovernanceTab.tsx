import { useEffect, useState } from "react";
import type { AgentLogEvent, Approval, AuditEvent, HitlItem } from "@/lib/api";
import { api } from "@/lib/api";
import { ago } from "@/lib/utils";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { ResizableSplit } from "@/components/ui/resizable-split";

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

type LogFilter = "all" | "runs" | "tools" | "hitl";

export function GovernanceTab({ pending, kernelEvts, onChange }: Props) {
  const [hitlItems, setHitlItems] = useState<HitlItem[]>([]);
  const [logs, setLogs] = useState<AgentLogEvent[]>([]);
  const [filter, setFilter] = useState<LogFilter>("all");
  const [expandedEvt, setExpandedEvt] = useState<number | null>(null);

  useEffect(() => {
    let cancelled = false;
    const tick = async () => {
      const [h, l] = await Promise.all([api.hitlPending(), api.agentLogs(300)]);
      if (cancelled) return;
      if (h?.items) setHitlItems(h.items);
      if (l?.events) setLogs(l.events);
    };
    tick();
    const t = setInterval(tick, 2000);
    return () => {
      cancelled = true;
      clearInterval(t);
    };
  }, []);

  const mergedHitl: HitlItem[] = [
    ...hitlItems,
    ...pending
      .filter((p) => !hitlItems.some((h) => h.id === p.id))
      .map((p) => ({
        source: "approval" as const,
        id: p.id,
        agent_id: p.from_agent ?? p.agent_id,
        priority: p.priority,
        created_at: p.created_at ?? p.timestamp,
        question: p.question ?? p.message ?? p.body,
      })),
  ];

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
  const a2hApprove = async (id: string) => {
    if (!confirm("Approve this A2H request?")) return;
    await api.a2hApprove(id);
    onChange();
  };
  const a2hReject = async (id: string) => {
    if (!confirm("Reject this A2H request?")) return;
    await api.a2hReject(id);
    onChange();
  };

  const filtered = logs.filter((e) => {
    if (filter === "all") return true;
    if (filter === "runs") return e.type?.startsWith("run.");
    if (filter === "tools") return e.type?.startsWith("tool.");
    if (filter === "hitl") return e.type?.startsWith("human.") || e.type?.includes("approval");
    return true;
  });

  return (
    <div className="flex h-full flex-col">
      <div className="flex-1 overflow-hidden">
        <ResizableSplit
          left={
            <div>
              <div className="border-b border-border bg-surface px-[14px] py-2 text-[10px] uppercase tracking-widest text-dim">
                HITL Inbox — Pending Human Approvals ({mergedHitl.length})
              </div>
              {mergedHitl.length === 0 ? (
                <div className="p-10 text-center text-dim">
                  No pending approvals. Agents are operating autonomously.
                </div>
              ) : (
                mergedHitl.map((h) => {
                  const pri = (h.priority ?? "medium").toLowerCase();
                  return (
                    <Card key={`${h.source}-${h.id}`}>
                      <div className="text-[10px] text-dim">
                        <span className={PRIORITY_CLS[pri] ?? "text-dim"}>
                          {pri.toUpperCase()}
                        </span>{" "}
                        · {h.agent_id ?? "?"} · {ago(h.created_at)} ·{" "}
                        <span className="text-info">{h.source}</span>
                      </div>
                      <div className="my-1 leading-relaxed text-bright">
                        {h.question ?? JSON.stringify(h).substring(0, 300)}
                      </div>
                      {h.source === "approval" && (
                        <div className="mt-2 space-x-1">
                          <Button variant="ok" onClick={() => approve(h.id)}>
                            ✓ Approve
                          </Button>
                          <Button variant="reject" onClick={() => reject(h.id)}>
                            ✗ Reject
                          </Button>
                        </div>
                      )}
                      {h.source === "a2h" && (
                        <div className="mt-2 space-x-1">
                          <Button variant="ok" onClick={() => a2hApprove(h.id)}>
                            ✓ Approve
                          </Button>
                          <Button variant="reject" onClick={() => a2hReject(h.id)}>
                            ✗ Reject
                          </Button>
                        </div>
                      )}
                    </Card>
                  );
                })
              )}
            </div>
          }
          right={
            <div>
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
                  const isOpen = expandedEvt === i;
                  const params =
                    (det.params as Record<string, unknown>) ??
                    (det.arguments as Record<string, unknown>) ??
                    (det.args as Record<string, unknown>) ??
                    (det.input as Record<string, unknown>) ??
                    null;
                  const reason =
                    (det.reason as string) ??
                    (det.message as string) ??
                    (det.error as string) ??
                    null;
                  const hasMore =
                    Object.keys(det).length > 0 || !!e.resource_id || !!e.outcome;
                  return (
                    <div key={i} className="border-b border-border">
                      <button
                        type="button"
                        onClick={() => setExpandedEvt(isOpen ? null : i)}
                        className="flex w-full items-center gap-2 px-3 py-[5px] text-left text-[11px] hover:bg-bg"
                      >
                        <span className="w-[10px] text-dim">{isOpen ? "▾" : "▸"}</span>
                        <span className="min-w-[55px] text-dim">{time}</span>
                        <span className="min-w-[130px] truncate text-info">{actor}</span>
                        <span className="text-warn">{String(resource).substring(0, 35)}</span>
                        <span className={ok ? "text-ok" : "text-danger"}>
                          {ok ? "✓" : "✗"} {outcome.toUpperCase()}
                        </span>
                        <span className="ml-1 truncate text-dim">{action}</span>
                      </button>
                      {isOpen && (
                        <div className="border-t border-border bg-bg px-3 py-2 font-mono text-[10px] leading-relaxed">
                          <div className="grid grid-cols-[110px_1fr] gap-x-2 gap-y-[2px] text-text">
                            <span className="text-dim">action</span>
                            <span className="break-all">{action || "-"}</span>
                            <span className="text-dim">actor</span>
                            <span className="break-all">{actor}</span>
                            <span className="text-dim">resource</span>
                            <span className="break-all">{String(resource)}</span>
                            <span className="text-dim">resource_id</span>
                            <span className="break-all">{e.resource_id ?? "-"}</span>
                            <span className="text-dim">outcome</span>
                            <span
                              className={`break-all ${ok ? "text-ok" : "text-danger"}`}
                            >
                              {outcome}
                            </span>
                            <span className="text-dim">timestamp</span>
                            <span className="break-all">
                              {e.created_at ?? e.timestamp ?? "-"}
                            </span>
                            {reason && (
                              <>
                                <span className="text-dim">reason</span>
                                <span className="break-all text-warn">{reason}</span>
                              </>
                            )}
                          </div>
                          {params && (
                            <div className="mt-2">
                              <div className="mb-[2px] text-[10px] uppercase tracking-widest text-warn">
                                Parameters
                              </div>
                              <pre className="whitespace-pre-wrap break-all rounded border border-border bg-surface px-2 py-1 text-text">
                                {JSON.stringify(params, null, 2)}
                              </pre>
                            </div>
                          )}
                          {hasMore && (
                            <div className="mt-2">
                              <div className="mb-[2px] text-[10px] uppercase tracking-widest text-dim">
                                Full Details
                              </div>
                              <pre className="max-h-[280px] overflow-auto whitespace-pre-wrap break-all rounded border border-border bg-surface px-2 py-1 text-text">
                                {JSON.stringify(det, null, 2)}
                              </pre>
                            </div>
                          )}
                        </div>
                      )}
                    </div>
                  );
                })
              )}
            </div>
          }
        />
      </div>

      <div className="flex h-[40%] flex-col border-t-2 border-border">
        <div className="flex items-center justify-between border-b border-border bg-surface px-[14px] py-2">
          <span className="text-[10px] uppercase tracking-widest text-dim">
            Agent Logs ({filtered.length})
          </span>
          <div className="flex gap-1 text-[10px]">
            {(["all", "runs", "tools", "hitl"] as LogFilter[]).map((f) => (
              <button
                key={f}
                onClick={() => setFilter(f)}
                className={`border px-2 py-[2px] uppercase ${
                  filter === f
                    ? "border-info bg-info/10 text-info"
                    : "border-border bg-bg text-dim hover:text-text"
                }`}
              >
                {f}
              </button>
            ))}
          </div>
        </div>
        <div className="flex-1 overflow-y-auto">
          {filtered.length === 0 ? (
            <div className="p-6 text-center text-dim">No agent activity yet.</div>
          ) : (
            filtered.map((ev, i) => {
              const time = (ev.ts ?? "").substring(11, 19);
              const isFail = ev.type.endsWith("failed") || ev.type === "tool.call" && (ev.details as { error?: string })?.error;
              const color =
                ev.type.startsWith("run.")
                  ? "text-info"
                  : ev.type.startsWith("tool.")
                    ? "text-warn"
                    : "text-dim";
              return (
                <div
                  key={i}
                  className="flex items-center gap-2 border-b border-border px-3 py-[4px] font-mono text-[11px]"
                >
                  <span className="min-w-[55px] text-dim">{time}</span>
                  <span className="min-w-[160px] text-bright">
                    {(ev.agent_id ?? "-").split("/").pop()}
                  </span>
                  <span className={`min-w-[110px] ${color}`}>{ev.type}</span>
                  <span className={isFail ? "text-danger" : "text-text"}>
                    {ev.description}
                  </span>
                </div>
              );
            })
          )}
        </div>
      </div>
    </div>
  );
}
