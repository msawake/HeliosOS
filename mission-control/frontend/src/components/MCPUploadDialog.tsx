import { useRef, useState } from "react";
import { load as loadYaml } from "js-yaml";
import type { MCPServerConfig } from "@/lib/api";
import { api } from "@/lib/api";
import { Button } from "@/components/ui/button";

const PLACEHOLDER = `server_name: my-server
package: mcp-atlassian
env_vars:
  ATLASSIAN_URL: https://your-company.atlassian.net
  ATLASSIAN_TOKEN: secret:atlassian-api-token
args: []`;

interface Props {
  open: boolean;
  onClose: () => void;
  onUploaded: () => void;
}

export function MCPUploadDialog({ open, onClose, onUploaded }: Props) {
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
      setError("Paste a config or choose a YAML file.");
      return;
    }
    setBusy(true);
    setError(null);
    let parsed: MCPServerConfig;
    try {
      parsed = loadYaml(yaml) as MCPServerConfig;
    } catch (e) {
      setBusy(false);
      setError(`YAML parse error: ${(e as Error).message}`);
      return;
    }
    if (!parsed?.server_name?.trim() || !parsed?.package?.trim()) {
      setBusy(false);
      setError("server_name and package are required fields");
      return;
    }
    const res = await api.mcpAdd(parsed);
    setBusy(false);
    if (!res.ok) {
      setError(
        (res.body as { detail?: string } | null)?.detail ?? `HTTP ${res.status}`,
      );
      return;
    }
    reset();
    onUploaded();
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
            ↑ Upload MCP Server Config
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
              id="mcp-yaml-file-input"
            />
            <Button onClick={() => fileRef.current?.click()}>CHOOSE FILE</Button>
            <span className="text-dim">{filename ?? "or paste YAML below"}</span>
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
            placeholder={PLACEHOLDER}
            spellCheck={false}
            rows={12}
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
            {busy ? "UPLOADING…" : "UPLOAD"}
          </Button>
        </div>
      </div>
    </div>
  );
}
