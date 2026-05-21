import { useEffect, useState } from "react";
import type { Agent, AgentProcess, AgentRun } from "@/lib/api";
import { api } from "@/lib/api";
import { ago, fmt, usd } from "@/lib/utils";
import { Button } from "@/components/ui/button";
import { ConfirmDialog } from "@/components/ui/confirm-dialog";
import { InvokeAgentDialog } from "@/components/InvokeAgentDialog";
import { PhaseBadge } from "./PhaseBadge";
import { Sheet } from "@/components/ui/sheet";
import { StackBadge } from "./StackBadge";

function agentToYaml(a: Agent): string {
  const model = a.llm_config?.chat_model ?? a.model ?? "claude-sonnet-4-5";
  const provider =
    a.llm_config?.provider ??
    (model.startsWith("gpt") || model.startsWith("o") ? "openai" : "anthropic");
  const toolLines = (a.tools ?? []).map((t) => `      - ${t}`).join("\n");
  const toolsBlock = toolLines ? `\n${toolLines}` : " []";
  const systemPrompt = a.system_prompt ?? "";
  const systemPromptBlock = systemPrompt
    .split("\n")
    .map((l) => `    ${l}`)
    .join("\n");
  const scheduleBlock =
    a.execution_type === "scheduled" && a.schedule
      ? `\n    schedule: "${a.schedule}"`
      : a.execution_type === "scheduled"
        ? `\n    schedule: ""  # required for scheduled agents`
        : "";
  const goalBlock =
    a.execution_type === "autonomous" && a.goal ? `\n    goal: "${a.goal}"` : "";
  const triggerLines = (a.event_triggers ?? []).map((t) => `      - ${t}`).join("\n");
  const triggersBlock =
    a.execution_type === "event_driven" && triggerLines
      ? `\n    event_triggers:\n${triggerLines}`
      : "";

  return `apiVersion: agentos/v1
kind: AgentContract
metadata:
  name: ${a.name}
  namespace: ${a.namespace ?? "default"}
spec:
  runtime:
    framework: ${a.stack ?? "forgeos"}
  lifecycle:
    type: ${a.execution_type ?? "reflex"}${scheduleBlock}${goalBlock}${triggersBlock}
  llm:
    chat_model: ${model}
    provider: ${provider}
  capabilities:
    tools:
      allowed:${toolsBlock}
  system_prompt: |
${systemPromptBlock}
`;
}

interface Props {
  open: boolean;
  onClose: () => void;
  agent?: Agent;
  proc?: AgentProcess;
  onChange?: () => void;
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

export function AgentDetailSheet({ open, onClose, agent, proc, onChange }: Props) {
  const pid = proc?.pid != null ? String(proc.pid) : "";
  const label = (agent?.name ?? proc?.name ?? "agent").split("/").pop() ?? "agent";
  const [pendingAction, setPendingAction] = useState<"stop" | "delete" | null>(null);
  const [invokeOpen, setInvokeOpen] = useState(false);
  const [toast, setToast] = useState<string | null>(null);
  const [runs, setRuns] = useState<AgentRun[]>([]);
  const [expandedRun, setExpandedRun] = useState<string | null>(null);
  const [editMode, setEditMode] = useState(false);
  const [editYaml, setEditYaml] = useState("");
  const [saving, setSaving] = useState(false);
  const [saveError, setSaveError] = useState<string | null>(null);

  useEffect(() => {
    if (!open || !pid) return;
    let cancelled = false;
    const load = async () => {
      const r = await api.agentRuns(pid, 20);
      if (!cancelled && r) setRuns(r.runs ?? []);
    };
    load();
    const t = setInterval(load, 5000);
    return () => {
      cancelled = true;
      clearInterval(t);
    };
  }, [open, pid]);

  useEffect(() => {
    if (!toast) return;
    const t = setTimeout(() => setToast(null), 3500);
    return () => clearTimeout(t);
  }, [toast]);

  const runConfirmed = async () => {
    if (!pid || !pendingAction) return;
    const action = pendingAction;
    setPendingAction(null);
    await api.stop(pid);
    if (action === "delete") await api.remove(pid);
    onChange?.();
  };

  const openEdit = async () => {
    setSaveError(null);
    const fullAgent = pid ? await api.getAgent(pid) : null;
    // Pick the first non-empty value across all sources for each field.
    // Critical: never let an empty/missing value from one source overwrite
    // a good value from another (e.g. fullAgent.system_prompt="" silently
    // clobbering agent.system_prompt="You are...").
    const pick = <K extends keyof Agent>(key: K): Agent[K] | undefined => {
      const v1 = fullAgent?.[key];
      if (v1 !== undefined && v1 !== null && v1 !== "") return v1;
      const v2 = agent?.[key];
      if (v2 !== undefined && v2 !== null && v2 !== "") return v2;
      return v1 ?? v2;
    };
    const base: Agent = {
      name: pick("name") ?? proc?.name ?? "",
      namespace: pick("namespace") ?? proc?.namespace ?? "default",
      stack: pick("stack") ?? proc?.stack ?? "forgeos",
      execution_type: pick("execution_type") ?? proc?.execution_type ?? "reflex",
      model: pick("model"),
      llm_config: pick("llm_config"),
      tools: pick("tools") ?? [],
      system_prompt: pick("system_prompt") ?? "",
      schedule: pick("schedule"),
      goal: pick("goal"),
      event_triggers: pick("event_triggers") ?? [],
      metadata: pick("metadata"),
    };
    setEditYaml(agentToYaml(base));
    setEditMode(true);
  };

  const saveManifest = async () => {
    if (!editYaml.trim() || !pid) return;
    setSaving(true);
    setSaveError(null);
    const res = await api.updateAgentYaml(pid, editYaml);
    setSaving(false);
    if (!res.ok) {
      const detail =
        (res.body as { detail?: string; error?: string } | null)?.detail ||
        (res.body as { error?: string } | null)?.error ||
        `HTTP ${res.status}`;
      setSaveError(String(detail));
      return;
    }
    setEditMode(false);
    setToast("Manifest saved — agent updated.");
    onChange?.();
  };

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
      <DetailRow
        k="Model"
        v={a.llm_config?.chat_model ?? a.model ?? meta?.model ?? "-"}
      />
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

      {editMode ? (
        <div className="mt-3">
          <div className="mb-1 flex items-center justify-between">
            <span className="text-[10px] uppercase tracking-widest text-warn">Edit Manifest</span>
            <span className="text-[10px] text-dim">YAML · agentos/v1</span>
          </div>
          <textarea
            value={editYaml}
            onChange={(e) => { setEditYaml(e.target.value); setSaveError(null); }}
            spellCheck={false}
            rows={20}
            className="w-full resize-y border border-border bg-bg p-2 font-mono text-[11px] text-text outline-none focus:border-info"
          />
          {saveError && (
            <div className="mt-1 border border-danger bg-danger/10 px-2 py-1 text-[11px] text-danger">
              ✕ {saveError}
            </div>
          )}
          <div className="mt-2 flex gap-2">
            <Button variant="ok" onClick={saveManifest} disabled={saving || !editYaml.trim()}>
              {saving ? "SAVING…" : "SAVE"}
            </Button>
            <Button variant="ghost" onClick={() => { setEditMode(false); setSaveError(null); }}>
              CANCEL
            </Button>
          </div>
        </div>
      ) : (
        <div className="mt-3 flex gap-2 flex-wrap">
          {pid && (
            <>
              <Button variant="ok" onClick={() => setInvokeOpen(true)}>
                ▶ RUN NOW
              </Button>
              <Button variant="danger" onClick={() => setPendingAction("stop")}>
                STOP
              </Button>
              <Button variant="danger" onClick={() => setPendingAction("delete")}>
                DELETE
              </Button>
            </>
          )}
          <Button variant="ghost" onClick={openEdit}>
            EDIT MANIFEST
          </Button>
        </div>
      )}

      <InvokeAgentDialog
        open={invokeOpen}
        onClose={() => setInvokeOpen(false)}
        pid={pid}
        label={label}
        onQueued={(msg) => setToast(msg)}
      />

      {toast && (
        <div className="fixed bottom-4 right-4 z-[70] border border-ok bg-bg px-3 py-2 font-mono text-[11px] text-ok shadow-[0_4px_20px_rgba(0,0,0,0.6)]">
          {toast}
        </div>
      )}

      {pid && (
        <ManifestSection title={`Recent Runs (${runs.length})`}>
          {runs.length === 0 ? (
            <div className="text-[10px] text-dim">No runs recorded yet.</div>
          ) : (
            <div className="space-y-1">
              {runs.map((r) => {
                const isOpen = expandedRun === r.id;
                const statusColor =
                  r.status === "completed"
                    ? "text-ok"
                    : r.status === "failed"
                      ? "text-danger"
                      : "text-info";
                return (
                  <div key={r.id} className="border border-border bg-surface">
                    <button
                      onClick={() => setExpandedRun(isOpen ? null : r.id)}
                      className="flex w-full items-center justify-between px-2 py-1 text-left font-mono text-[10px] hover:bg-bg"
                    >
                      <span className="text-dim">
                        {r.started_at ? new Date(r.started_at).toLocaleTimeString() : "-"}
                      </span>
                      <span className={statusColor}>{r.status.toUpperCase()}</span>
                      <span className="text-dim">{r.trigger}</span>
                      <span className="text-dim">{r.tool_calls}t · {r.tokens_used}tok</span>
                      <span className="text-dim">{r.duration_ms ? `${r.duration_ms}ms` : "-"}</span>
                    </button>
                    {isOpen && (
                      <div className="border-t border-border px-2 py-2 font-mono text-[10px]">
                        {r.prompt && (
                          <>
                            <div className="text-dim">prompt:</div>
                            <pre className="mb-2 whitespace-pre-wrap text-text">{r.prompt}</pre>
                          </>
                        )}
                        {r.output && (
                          <>
                            <div className="text-dim">output:</div>
                            <pre className="mb-2 max-h-[200px] overflow-auto whitespace-pre-wrap text-text">{r.output}</pre>
                          </>
                        )}
                        {r.error && (
                          <>
                            <div className="text-dim">error:</div>
                            <pre className="whitespace-pre-wrap text-danger">{r.error}</pre>
                          </>
                        )}
                      </div>
                    )}
                  </div>
                );
              })}
            </div>
          )}
        </ManifestSection>
      )}

      <ConfirmDialog
        open={!!pendingAction}
        destructive
        title={pendingAction === "delete" ? "Delete agent" : "Stop agent"}
        confirmLabel={pendingAction === "delete" ? "STOP & DELETE" : "STOP"}
        cancelLabel="CANCEL"
        message={
          <div className="space-y-2">
            <div>
              <span className="text-dim">name: </span>
              <span className="text-bright">{label}</span>
            </div>
            <div>
              <span className="text-dim">pid:  </span>
              <span className="text-bright">{pid}</span>
            </div>
            <div className="pt-2 text-danger">
              {pendingAction === "delete"
                ? "Stops the process and unregisters it. Permanent."
                : "Transitions the process to STOPPED. The registry entry stays."}
            </div>
          </div>
        }
        onConfirm={runConfirmed}
        onCancel={() => setPendingAction(null)}
      />

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
