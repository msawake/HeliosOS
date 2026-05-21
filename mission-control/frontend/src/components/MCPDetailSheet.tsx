import type { MCPServerConfig } from "@/lib/api";
import { Button } from "@/components/ui/button";
import { Sheet } from "@/components/ui/sheet";

function toYaml(s: MCPServerConfig): string {
  const lines: string[] = [
    `server_name: ${s.server_name}`,
    `package: ${JSON.stringify(s.package)}`,
  ];
  const envEntries = Object.entries(s.env_vars ?? {});
  if (envEntries.length > 0) {
    lines.push("env_vars:");
    for (const [k, v] of envEntries) {
      lines.push(`  ${k}: ${JSON.stringify(v)}`);
    }
  }
  const args = s.args ?? [];
  if (args.length > 0) {
    lines.push("args:");
    for (const a of args) {
      lines.push(`  - ${JSON.stringify(a)}`);
    }
  }
  return lines.join("\n");
}

interface Props {
  open: boolean;
  onClose: () => void;
  server: MCPServerConfig | null;
  onEdit: () => void;
  onDelete: () => void;
}

export function MCPDetailSheet({ open, onClose, server, onEdit, onDelete }: Props) {
  if (!server) return null;

  return (
    <Sheet open={open} onClose={onClose} title={server.server_name}>
      <div className="mt-1 flex justify-between border-b border-border py-1">
        <span className="text-dim">package</span>
        <span className="max-w-[260px] truncate font-mono text-[10px] text-bright">
          {server.package}
        </span>
      </div>
      <div className="mt-1 flex justify-between border-b border-border py-1">
        <span className="text-dim">env vars</span>
        <span className="text-bright">{Object.keys(server.env_vars ?? {}).length}</span>
      </div>
      <div className="mt-1 flex justify-between border-b border-border py-1">
        <span className="text-dim">args</span>
        <span className="text-bright">{(server.args ?? []).length}</span>
      </div>

      <div className="mt-3 rounded-md border border-border bg-bg p-[10px]">
        <h4 className="mb-[6px] text-[11px] uppercase tracking-wider text-warn">
          Config · YAML
        </h4>
        <pre className="whitespace-pre-wrap font-mono text-[11px] leading-relaxed text-ok">
          {toYaml(server)}
        </pre>
      </div>

      <div className="mt-4 flex gap-2">
        <Button variant="ok" onClick={onEdit}>
          EDIT
        </Button>
        <Button variant="danger" onClick={onDelete}>
          DELETE
        </Button>
      </div>
    </Sheet>
  );
}
