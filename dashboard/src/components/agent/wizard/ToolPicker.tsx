import { useMemo, useState } from 'react';
import { CaretRight, MagnifyingGlass } from '@phosphor-icons/react';

import type { ToolDef } from '@/lib/api';
import { Input } from '@/components/ui/input';
import { Badge } from '@/components/ui/badge';
import { groupByNamespace, namespaceWildcard, toolLabel } from '@/lib/tools';
import { cn } from '@/lib/utils';
import { ChipsInput } from './fields';

/** Multi-select tool picker: searchable, namespace-grouped, with per-namespace
 *  wildcard selection and free-text entry for tools not in the catalog. */
export function ToolPicker({
  tools,
  selected,
  onChange,
  loading,
}: {
  tools: ToolDef[];
  selected: string[];
  onChange: (next: string[]) => void;
  loading?: boolean;
}) {
  const [query, setQuery] = useState('');
  const [open, setOpen] = useState<Record<string, boolean>>({});

  const groups = useMemo(() => {
    const q = query.trim().toLowerCase();
    const filtered = q
      ? tools.filter(
          (t) => t.name.toLowerCase().includes(q) || (t.description ?? '').toLowerCase().includes(q),
        )
      : tools;
    return groupByNamespace(filtered);
  }, [tools, query]);

  const sel = new Set(selected);
  const toggle = (name: string) =>
    onChange(sel.has(name) ? selected.filter((t) => t !== name) : [...selected, name]);

  const toggleWildcard = (ns: string, members: ToolDef[]) => {
    const wc = namespaceWildcard(ns, members);
    if (sel.has(wc)) {
      onChange(selected.filter((t) => t !== wc));
    } else {
      // Adding the wildcard supersedes individual picks in this namespace.
      const memberNames = new Set(members.map((m) => m.name));
      onChange([...selected.filter((t) => !memberNames.has(t)), wc]);
    }
  };

  // Custom / wildcard entries the user typed that aren't catalog tool names.
  const catalogNames = useMemo(() => new Set(tools.map((t) => t.name)), [tools]);
  const customEntries = selected.filter((t) => !catalogNames.has(t));

  return (
    <div className="space-y-3">
      <div className="relative">
        <MagnifyingGlass
          className="pointer-events-none absolute left-2.5 top-1/2 h-3.5 w-3.5 -translate-y-1/2 text-muted"
          aria-hidden
        />
        <Input
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          placeholder={loading ? 'Loading tools…' : 'Search tools…'}
          className="pl-8"
          disabled={loading}
        />
      </div>

      <div className="max-h-80 space-y-1.5 overflow-y-auto rounded-lg border border-edge bg-surface p-1.5">
        {groups.length === 0 ? (
          <p className="px-2 py-6 text-center text-[13px] text-tertiary">
            {loading ? 'Loading…' : 'No tools match your search.'}
          </p>
        ) : (
          groups.map(({ ns, tools: members }) => {
            const wc = namespaceWildcard(ns, members);
            const wcActive = sel.has(wc);
            const isOpen = open[ns] ?? false;
            const count = members.filter((m) => sel.has(m.name)).length + (wcActive ? members.length : 0);
            return (
              <div key={ns} className="rounded-md border border-edge-subtle">
                <div className="flex items-center gap-2 px-2 py-1.5">
                  <button
                    type="button"
                    onClick={() => setOpen((o) => ({ ...o, [ns]: !isOpen }))}
                    className="flex items-center gap-1.5 text-left"
                  >
                    <CaretRight
                      className={cn('h-3 w-3 text-muted transition-transform', isOpen && 'rotate-90')}
                      aria-hidden
                    />
                    <span className="font-mono text-[12px] text-secondary">{ns}</span>
                  </button>
                  {count ? <Badge variant="brand">{count}</Badge> : null}
                  <span className="flex-1" />
                  <label className="flex cursor-pointer items-center gap-1.5 text-[11px] text-tertiary">
                    <input
                      type="checkbox"
                      checked={wcActive}
                      onChange={() => toggleWildcard(ns, members)}
                      className="h-3.5 w-3.5 cursor-pointer accent-accent"
                    />
                    all (<span className="font-mono">{wc}</span>)
                  </label>
                </div>
                {isOpen ? (
                  <ul className="border-t border-edge-subtle px-2 py-1.5">
                    {members.map((t) => (
                      <li key={t.name}>
                        <label className="flex cursor-pointer items-start gap-2 py-1">
                          <input
                            type="checkbox"
                            checked={wcActive || sel.has(t.name)}
                            disabled={wcActive}
                            onChange={() => toggle(t.name)}
                            className="mt-0.5 h-3.5 w-3.5 cursor-pointer accent-accent disabled:opacity-50"
                          />
                          <span className="min-w-0">
                            <span className="font-mono text-[12px] text-secondary">{toolLabel(t.name).label}</span>
                            {t.description ? (
                              <span className="block truncate text-[11px] text-tertiary">{t.description}</span>
                            ) : null}
                          </span>
                        </label>
                      </li>
                    ))}
                  </ul>
                ) : null}
              </div>
            );
          })
        )}
      </div>

      <div>
        <p className="mb-1.5 text-xs text-tertiary">
          Add a tool or wildcard not in the catalog (e.g. <span className="font-mono">mcp__atlassian__*</span>)
        </p>
        <ChipsInput
          values={customEntries}
          onChange={(custom) => onChange([...selected.filter((t) => catalogNames.has(t)), ...custom])}
          placeholder="tool name or wildcard…"
          ariaLabel="custom tool"
        />
      </div>
    </div>
  );
}
