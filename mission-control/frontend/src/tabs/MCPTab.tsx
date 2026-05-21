import { useEffect, useState } from "react";
import type { MCPServerConfig } from "@/lib/api";
import { api } from "@/lib/api";
import { Button } from "@/components/ui/button";
import { ConfirmDialog } from "@/components/ui/confirm-dialog";
import { MCPDetailSheet } from "@/components/MCPDetailSheet";
import { MCPUploadDialog } from "@/components/MCPUploadDialog";
import { Table, Tbody, Td, Th, Thead, Tr } from "@/components/ui/table";

function unwrap<T>(raw: T[] | { items?: T[] } | null): T[] {
  if (!raw) return [];
  if (Array.isArray(raw)) return raw;
  return raw.items ?? [];
}

interface Props {
  onChange?: () => void;
}

export function MCPTab({ onChange }: Props) {
  const [servers, setServers] = useState<MCPServerConfig[]>([]);
  const [editing, setEditing] = useState<MCPServerConfig | null>(null);
  const [isNew, setIsNew] = useState(false);
  const [pendingDelete, setPendingDelete] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [selectedServer, setSelectedServer] = useState<MCPServerConfig | null>(null);
  const [uploadOpen, setUploadOpen] = useState(false);

  const refresh = async () => {
    const r = await api.mcpList();
    setServers(unwrap(r));
    onChange?.();
  };

  useEffect(() => {
    refresh();
  }, []);

  const openNew = () => {
    setIsNew(true);
    setEditing({ server_name: "", package: "", env_vars: {}, args: [] });
    setError(null);
  };

  const openEdit = (s: MCPServerConfig) => {
    setIsNew(false);
    setEditing({ ...s, env_vars: { ...(s.env_vars ?? {}) }, args: [...(s.args ?? [])] });
    setError(null);
  };

  const save = async () => {
    if (!editing) return;
    if (!editing.server_name.trim() || !editing.package.trim()) {
      setError("server_name and package are required");
      return;
    }
    const res = isNew
      ? await api.mcpAdd(editing)
      : await api.mcpUpdate(editing.server_name, editing);
    if (!res.ok) {
      setError(
        (res.body as { detail?: string } | null)?.detail ?? `HTTP ${res.status}`,
      );
      return;
    }
    setEditing(null);
    refresh();
  };

  const doDelete = async () => {
    if (!pendingDelete) return;
    await api.mcpDelete(pendingDelete);
    setPendingDelete(null);
    setSelectedServer(null);
    refresh();
  };

  return (
    <div>
      <div className="flex items-center justify-between border-b border-border bg-bg px-[14px] py-2">
        <span className="text-[10px] uppercase tracking-widest text-dim">
          MCP Servers · {servers.length} configured · restart required to apply
        </span>
        <div className="flex gap-2">
          <Button variant="ok" onClick={() => setUploadOpen(true)}>
            ↑ UPLOAD YAML
          </Button>
          <Button variant="ok" onClick={openNew}>
            + ADD SERVER
          </Button>
        </div>
      </div>

      {!servers.length ? (
        <div className="p-10 text-center text-dim">
          No MCP servers configured. Add one to make its tools available after the next platform restart.
        </div>
      ) : (
        <Table>
          <Thead>
            <Tr>
              <Th>Name</Th>
              <Th>Package</Th>
              <Th>Env vars</Th>
              <Th>Args</Th>
            </Tr>
          </Thead>
          <Tbody>
            {servers.map((s) => (
              <Tr
                key={s.server_name}
                onClick={() => setSelectedServer(s)}
                className={
                  selectedServer?.server_name === s.server_name
                    ? "bg-info/10"
                    : undefined
                }
              >
                <Td className="text-bright">{s.server_name}</Td>
                <Td className="font-mono text-[10px]">{s.package}</Td>
                <Td className="text-dim">
                  {Object.keys(s.env_vars ?? {}).length} vars
                </Td>
                <Td className="text-dim">{(s.args ?? []).length}</Td>
              </Tr>
            ))}
          </Tbody>
        </Table>
      )}

      <MCPDetailSheet
        open={!!selectedServer}
        onClose={() => setSelectedServer(null)}
        server={selectedServer}
        onEdit={() => {
          if (selectedServer) openEdit(selectedServer);
        }}
        onDelete={() => {
          if (selectedServer) setPendingDelete(selectedServer.server_name);
        }}
      />

      <MCPUploadDialog
        open={uploadOpen}
        onClose={() => setUploadOpen(false)}
        onUploaded={refresh}
      />

      {editing && (
        <MCPEditorDialog
          server={editing}
          isNew={isNew}
          error={error}
          onChange={setEditing}
          onCancel={() => setEditing(null)}
          onSave={save}
        />
      )}

      <ConfirmDialog
        open={!!pendingDelete}
        destructive
        title="Delete MCP server"
        confirmLabel="DELETE"
        cancelLabel="CANCEL"
        message={
          <div>
            Remove <span className="text-bright">{pendingDelete}</span> from the
            platform MCP registry?
            <div className="pt-2 text-danger">
              Existing agents using this server keep running until the next restart.
            </div>
          </div>
        }
        onConfirm={doDelete}
        onCancel={() => setPendingDelete(null)}
      />
    </div>
  );
}

interface EditorProps {
  server: MCPServerConfig;
  isNew: boolean;
  error: string | null;
  onChange: (s: MCPServerConfig) => void;
  onCancel: () => void;
  onSave: () => void;
}

function MCPEditorDialog({ server, isNew, error, onChange, onCancel, onSave }: EditorProps) {
  const envEntries = Object.entries(server.env_vars ?? {});

  const setEnvKey = (i: number, newKey: string) => {
    const next: Record<string, string> = {};
    envEntries.forEach(([k, v], j) => {
      next[j === i ? newKey : k] = v;
    });
    onChange({ ...server, env_vars: next });
  };
  const setEnvVal = (i: number, newVal: string) => {
    const next: Record<string, string> = {};
    envEntries.forEach(([k, v], j) => {
      next[k] = j === i ? newVal : v;
    });
    onChange({ ...server, env_vars: next });
  };
  const addEnv = () => {
    const next: Record<string, string> = { ...(server.env_vars ?? {}), "": "" };
    onChange({ ...server, env_vars: next });
  };
  const removeEnv = (i: number) => {
    const next: Record<string, string> = {};
    envEntries.forEach(([k, v], j) => {
      if (j !== i) next[k] = v;
    });
    onChange({ ...server, env_vars: next });
  };

  return (
    <div
      className="fixed inset-0 z-[60] flex items-center justify-center bg-black/60 backdrop-blur-[1px]"
      onClick={onCancel}
    >
      <div
        onClick={(e) => e.stopPropagation()}
        className="w-[640px] max-w-[94vw] border-2 border-border bg-surface shadow-[0_8px_40px_rgba(0,0,0,0.6)]"
      >
        <div className="flex items-center justify-between border-b border-border bg-bg px-3 py-2">
          <span className="font-mono text-[11px] font-bold uppercase tracking-widest text-warn">
            {isNew ? "+ NEW MCP SERVER" : `EDIT · ${server.server_name}`}
          </span>
          <button
            onClick={onCancel}
            aria-label="Close"
            className="cursor-pointer text-lg leading-none text-dim hover:text-text"
          >
            ×
          </button>
        </div>

        <div className="space-y-3 px-3 py-3 font-mono text-[11px]">
          <Field
            label="server_name"
            value={server.server_name}
            disabled={!isNew}
            onChange={(v) => onChange({ ...server, server_name: v })}
            hint="Tools will be exposed as mcp__<server_name>__<tool>"
          />
          <Field
            label="package"
            value={server.package}
            onChange={(v) => onChange({ ...server, package: v })}
            hint="npm pkg (run via npx) or python pkg (run via uvx). e.g. mcp-atlassian, @modelcontextprotocol/server-github"
          />

          <div>
            <div className="mb-1 flex items-center justify-between">
              <span className="text-dim">env_vars</span>
              <Button onClick={addEnv}>+ ADD VAR</Button>
            </div>
            <div className="space-y-1">
              {envEntries.length === 0 && (
                <div className="text-dim">No environment variables set.</div>
              )}
              {envEntries.map(([k, v], i) => (
                <div key={i} className="flex gap-1">
                  <input
                    value={k}
                    onChange={(e) => setEnvKey(i, e.target.value)}
                    placeholder="VAR_NAME"
                    className="w-[40%] border border-border bg-bg px-2 py-1 text-text outline-none focus:border-info"
                  />
                  <input
                    value={v}
                    onChange={(e) => setEnvVal(i, e.target.value)}
                    placeholder='value or "secret:name"'
                    className="flex-1 border border-border bg-bg px-2 py-1 text-text outline-none focus:border-info"
                  />
                  <Button variant="danger" onClick={() => removeEnv(i)}>
                    ×
                  </Button>
                </div>
              ))}
            </div>
            <div className="mt-1 text-[10px] text-dim">
              Use <code>secret:name</code> to resolve via secrets manager, or paste a literal value (only used in local dev).
            </div>
          </div>

          {error && (
            <div className="border border-danger bg-danger/10 px-2 py-1 text-danger">
              ✕ {error}
            </div>
          )}
        </div>

        <div className="flex justify-end gap-2 border-t border-border bg-bg px-3 py-2">
          <Button variant="ghost" onClick={onCancel}>CANCEL</Button>
          <Button variant="ok" onClick={onSave}>{isNew ? "ADD" : "SAVE"}</Button>
        </div>
      </div>
    </div>
  );
}

function Field({
  label, value, hint, disabled, onChange,
}: {
  label: string;
  value: string;
  hint?: string;
  disabled?: boolean;
  onChange: (v: string) => void;
}) {
  return (
    <div>
      <label className="mb-1 block text-dim">{label}</label>
      <input
        value={value}
        disabled={disabled}
        onChange={(e) => onChange(e.target.value)}
        className="w-full border border-border bg-bg px-2 py-1 text-text outline-none focus:border-info disabled:opacity-60"
      />
      {hint && <div className="mt-1 text-[10px] text-dim">{hint}</div>}
    </div>
  );
}
