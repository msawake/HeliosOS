import { useRef, useState } from "react";
import { api } from "@/lib/api";
import { Button } from "@/components/ui/button";

interface Props {
  open: boolean;
  onClose: () => void;
  onDeployed: () => void;
}

export function UploadAgentDialog({ open, onClose, onDeployed }: Props) {
  const [yaml, setYaml] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [filename, setFilename] = useState<string | null>(null);
  const fileRef = useRef<HTMLInputElement>(null);

  if (!open) return null;

  const onPickFile = async (e: React.ChangeEvent<HTMLInputElement>) => {
    const f = e.target.files?.[0];
    if (!f) return;
    setFilename(f.name);
    setYaml(await f.text());
    setError(null);
  };

  const reset = () => {
    setYaml("");
    setFilename(null);
    setError(null);
    if (fileRef.current) fileRef.current.value = "";
  };

  const onSubmit = async () => {
    if (!yaml.trim()) {
      setError("Paste a manifest or choose a YAML file.");
      return;
    }
    setBusy(true);
    setError(null);
    const res = await api.uploadYaml(yaml);
    setBusy(false);
    if (!res.ok) {
      const detail =
        (res.body as { detail?: string; error?: string } | null)?.detail ||
        (res.body as { error?: string } | null)?.error ||
        `HTTP ${res.status}`;
      setError(String(detail));
      return;
    }
    reset();
    onDeployed();
    onClose();
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
            ↑ Upload Agent Manifest
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
          <div className="mb-2 flex items-center gap-2">
            <input
              ref={fileRef}
              type="file"
              accept=".yaml,.yml,application/x-yaml,text/yaml"
              onChange={onPickFile}
              className="hidden"
              id="manifest-file-input"
            />
            <Button onClick={() => fileRef.current?.click()}>
              CHOOSE FILE
            </Button>
            <span className="text-dim">
              {filename ?? "or paste YAML below"}
            </span>
            {yaml && (
              <Button variant="ghost" onClick={reset}>
                CLEAR
              </Button>
            )}
          </div>

          <textarea
            value={yaml}
            onChange={(e) => {
              setYaml(e.target.value);
              setError(null);
            }}
            placeholder={`apiVersion: agentos/v1
kind: AgentContract
metadata:
  name: my-agent
  namespace: default
spec:
  runtime:
    framework: forgeos
  lifecycle:
    type: reflex
  llm:
    chat_model: gpt-4o-mini
    provider: openai
  capabilities:
    tools:
      allowed: []`}
            spellCheck={false}
            rows={16}
            className="w-full resize-y border border-border bg-bg p-2 font-mono text-[11px] text-text outline-none focus:border-info"
          />

          {error && (
            <div className="mt-2 border border-danger bg-danger/10 px-2 py-1 text-[11px] text-danger">
              ✕ {error}
            </div>
          )}
        </div>

        <div className="flex justify-end gap-2 border-t border-border bg-bg px-3 py-2">
          <Button variant="ghost" onClick={onClose} disabled={busy}>
            CANCEL
          </Button>
          <Button variant="ok" onClick={onSubmit} disabled={busy || !yaml.trim()}>
            {busy ? "DEPLOYING…" : "DEPLOY"}
          </Button>
        </div>
      </div>
    </div>
  );
}
