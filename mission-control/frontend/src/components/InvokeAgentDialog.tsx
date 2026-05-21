import { useState } from "react";
import { api } from "@/lib/api";
import { Button } from "@/components/ui/button";

interface Props {
  open: boolean;
  onClose: () => void;
  pid: string;
  label: string;
  onQueued?: (msg: string) => void;
}

export function InvokeAgentDialog({ open, onClose, pid, label, onQueued }: Props) {
  const [prompt, setPrompt] = useState("");
  const [busy, setBusy] = useState(false);

  if (!open) return null;

  const run = async () => {
    setBusy(true);
    const r = await api.invoke(pid, prompt, { async: true });
    setBusy(false);
    const accepted = r.ok && (r.body?.accepted || r.body?.status === "accepted");
    if (accepted) {
      onQueued?.(`Queued: ${label}`);
      setPrompt("");
      onClose();
    } else {
      const detail =
        r.body?.error || r.body?.detail || `HTTP ${r.status} — invoke failed`;
      onQueued?.(`Failed to queue: ${detail}`);
    }
  };

  return (
    <div
      className="fixed inset-0 z-[60] flex items-center justify-center bg-black/60 backdrop-blur-[1px]"
      onClick={onClose}
    >
      <div
        onClick={(e) => e.stopPropagation()}
        className="w-[640px] max-w-[94vw] border-2 border-border bg-surface shadow-[0_8px_40px_rgba(0,0,0,0.6)]"
      >
        <div className="flex items-center justify-between border-b border-border bg-bg px-3 py-2">
          <span className="font-mono text-[11px] font-bold uppercase tracking-widest text-ok">
            ▶ Run Agent
          </span>
          <button
            onClick={onClose}
            aria-label="Close"
            className="cursor-pointer text-lg leading-none text-dim hover:text-text"
          >
            ×
          </button>
        </div>

        <div className="px-3 py-3 font-mono text-[11px] text-text">
          <div className="mb-2 flex justify-between text-dim">
            <span>
              <span>name: </span>
              <span className="text-bright">{label}</span>
            </span>
            <span>
              <span>pid: </span>
              <span className="text-bright">{pid}</span>
            </span>
          </div>

          <label className="mb-1 block text-dim">Prompt (optional)</label>
          <textarea
            value={prompt}
            onChange={(e) => setPrompt(e.target.value)}
            placeholder='leave blank for default trigger, or e.g. "DRY_RUN — list tickets only"'
            spellCheck={false}
            rows={4}
            disabled={busy}
            className="w-full resize-y border border-border bg-bg p-2 font-mono text-[11px] text-text outline-none focus:border-info"
          />
          <div className="mt-2 text-[10px] text-dim">
            Fires the agent in the background. Watch RECENT RUNS or
            Governance → AGENT LOGS for results.
          </div>
        </div>

        <div className="flex justify-end gap-2 border-t border-border bg-bg px-3 py-2">
          <Button variant="ghost" onClick={onClose} disabled={busy}>
            CLOSE
          </Button>
          <Button variant="ok" onClick={run} disabled={busy}>
            {busy ? "QUEUEING…" : "RUN NOW"}
          </Button>
        </div>
      </div>
    </div>
  );
}
