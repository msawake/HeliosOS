import type { ToolDef } from '@/lib/api';

/** Split a tool id into a namespace tag + a readable label.
 *  `mcp__atlassian__jira_search` → { ns: 'atlassian', label: 'jira_search' }
 *  `company__request_approval`   → { ns: 'company',  label: 'request_approval' } */
export function toolLabel(name: string): { ns?: string; label: string } {
  const segs = name.split('__').filter(Boolean);
  if (segs[0] === 'mcp' && segs.length >= 3) return { ns: segs[1], label: segs.slice(2).join('__') };
  if (segs.length >= 2) return { ns: segs[0], label: segs.slice(1).join('__') };
  return { label: name };
}

/** The namespace bucket a tool belongs to (`mcp__atlassian__*` → 'atlassian',
 *  `shell__exec` → 'shell', a bare name → 'other'). */
export function toolNamespace(name: string): string {
  return toolLabel(name).ns ?? 'other';
}

/** The wildcard that selects every tool in a namespace (`atlassian` →
 *  `mcp__atlassian__*` for MCP servers, `shell` → `shell__*`). MCP namespaces
 *  are detected by the presence of an `mcp__`-prefixed member. */
export function namespaceWildcard(ns: string, members: ToolDef[]): string {
  const isMcp = members.some((t) => t.name.startsWith('mcp__'));
  return isMcp ? `mcp__${ns}__*` : `${ns}__*`;
}

/** Group tool defs by namespace, sorted by namespace then tool label. */
export function groupByNamespace(defs: ToolDef[]): Array<{ ns: string; tools: ToolDef[] }> {
  const map = new Map<string, ToolDef[]>();
  for (const d of defs) {
    const ns = toolNamespace(d.name);
    (map.get(ns) ?? map.set(ns, []).get(ns)!).push(d);
  }
  return [...map.entries()]
    .map(([ns, tools]) => ({
      ns,
      tools: tools.slice().sort((a, b) => toolLabel(a.name).label.localeCompare(toolLabel(b.name).label)),
    }))
    .sort((a, b) => a.ns.localeCompare(b.ns));
}
