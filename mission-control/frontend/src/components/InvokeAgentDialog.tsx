import { useState } from "react";
import { api } from "@/lib/api";
import { Button } from "@/components/ui/button";

interface Props {
  open: boolean;
  onClose: () => void;
  pid: string;
  label: string;
}

interface Result {
  ok: boolean;
  status: number;
  text: string;
  warnings?: string[] | null;
  durationMs?: number;
  toolCalls?: number;
  tokens?: number;
}

export function InvokeAgentDialog({ open, onClose, pid, label }: Props) {
  const [prompt, setPrompt] = useState("");
  const [busy, setBusy] = useState(false);
  const [result, setResult] = useState<Result | null>(null);

  if (!open) return null;

  const reset = () => {
    setPrompt("");
    setResult(null);
  };

  const run = async () => {
    setBusy(true);
    setResult(null);
    const r = await api.invoke(pid, prompt);
    setBusy(false);
    const body = r.body;
    if (!r.ok) {
      setResult({
        ok: false,
        status: r.status,
        text:
          (body as { detail?: string; error?: string } | null)?.detail ||
          (body as { error?: string } | null)?.error ||
          `HTTP ${r.status}`,
      });
      return;
    }
    setResult({
      ok: !body?.error,
      status: r.status,
      text: body?.result ?? body?.error ?? "(no output)",
      warnings: body?.warnings,
      durationMs: body?.duration ? Math.round(body.duration * 1000) : undefined,
      toolCalls: body?.tool_calls,
      tokens: body?.tokens_used,
    });
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

          <label className="mb-1 block text-dim">Prompt</label>
          <textarea
            value={prompt}
            onChange={(e) => setPrompt(e.target.value)}
            placeholder='e.g. "DRY_RUN — just list the tickets you would comment on"'
            spellCheck={false}
            rows={4}
            disabled={busy}
            className="w-full resize-y border border-border bg-bg p-2 font-mono text-[11px] text-text outline-none focus:border-info"
          />

          {result && (
            <div className="mt-3 border border-border bg-bg p-2">
              <div className="mb-1 flex justify-between text-[10px] uppercase tracking-widest">
                <span className={result.ok ? "text-ok" : "text-danger"}>
                  {result.ok ? "✓ result" : "✕ failed"}
                </span>
                <span className="text-dim">
                  {result.durationMs != null ? `${result.durationMs}ms` : ""}
                  {result.toolCalls != null ? ` · ${result.toolCalls} tools` : ""}
                  {result.tokens != null ? ` · ${result.tokens} tok` : ""}
                </span>
              </div>
              <pre className="max-h-[260px] overflow-auto whitespace-pre-wrap break-words text-[11px] text-text">
                {result.text}
              </pre>
              {result.warnings && result.warnings.length > 0 && (
                <div className="mt-2 border-t border-border pt-2 text-warn">
                  {result.warnings.map((w, i) => (
                    <div key={i}>⚠ {w}</div>
                  ))}
                </div>
              )}
            </div>
          )}
        </div>

        <div className="flex justify-end gap-2 border-t border-border bg-bg px-3 py-2">
          {result && (
            <Button variant="ghost" onClick={reset} disabled={busy}>
              CLEAR
            </Button>
          )}
          <Button variant="ghost" onClick={onClose} disabled={busy}>
            CLOSE
          </Button>
          <Button variant="ok" onClick={run} disabled={busy}>
            {busy ? "RUNNING…" : "RUN NOW"}
          </Button>
        </div>
      </div>
    </div>
  );
}
