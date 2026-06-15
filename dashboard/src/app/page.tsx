'use client';

import { useCallback, useEffect, useMemo, useState } from 'react';
import Link from 'next/link';
import { Robot, RocketLaunch, MagnifyingGlass } from '@phosphor-icons/react';
import { api, type Agent } from '@/lib/api';
import { useAgentStatus } from '@/lib/hooks/useAgentStatus';
import {
  STACKS,
  EXEC_TYPES,
  STACK_LABELS,
  EXEC_LABELS,
  OWNERSHIP_TYPES,
} from '@/lib/utils';
import { PageHeader } from '@/components/layout/PageHeader';
import { Button } from '@/components/ui/button';
import { Card } from '@/components/ui/card';
import { Input, Select } from '@/components/ui/input';
import { Badge } from '@/components/ui/badge';
import { StatusBadge } from '@/components/StatusBadge';
import { Skeleton } from '@/components/ui/skeleton';
import { EmptyState } from '@/components/ui/empty-state';
import { ErrorState } from '@/components/ui/error-state';
import { Table, TableHeader, TableBody, TableRow, TableHead, TableCell } from '@/components/ui/table';

export default function AgentsPage() {
  const [agents, setAgents] = useState<Agent[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [stack, setStack] = useState('');
  const [type, setType] = useState('');
  const [ownership, setOwnership] = useState('');
  const [q, setQ] = useState('');
  const live = useAgentStatus();

  const load = useCallback(async () => {
    setAgents(null);
    setError(null);
    try {
      const data = await api.listAgents({
        stack: stack || undefined,
        execution_type: type || undefined,
        ownership: ownership || undefined,
      });
      setAgents(Array.isArray(data) ? data : []);
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Failed to load agents');
    }
  }, [stack, type, ownership]);

  useEffect(() => {
    load();
  }, [load]);

  const liveStatus = useMemo(() => {
    const m = new Map<string, string>();
    for (const a of live.agents) {
      const id = (a.agent_id || (a.id as string)) as string | undefined;
      if (id && typeof a.status === 'string') m.set(id, a.status);
    }
    return m;
  }, [live.agents]);

  const filtered = useMemo(() => {
    if (!agents) return [];
    const needle = q.trim().toLowerCase();
    if (!needle) return agents;
    return agents.filter(
      (a) =>
        a.name?.toLowerCase().includes(needle) ||
        a.agent_id?.toLowerCase().includes(needle) ||
        a.description?.toLowerCase().includes(needle)
    );
  }, [agents, q]);

  return (
    <div>
      <PageHeader
        title="Agents"
        description={
          live.connected
            ? live.running > 0
              ? `Live — ${live.running}/${live.total} running`
              : `Live — ${live.total} registered, 0 running`
            : 'Deployed agents on the platform.'
        }
        actions={
          <Button asChild>
            <Link href="/deploy">
              <RocketLaunch className="h-4 w-4" aria-hidden />
              Deploy
            </Link>
          </Button>
        }
      />

      <div className="mb-4 flex flex-wrap items-center gap-2">
        <div className="relative">
          <MagnifyingGlass
            className="pointer-events-none absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-muted"
            aria-hidden
          />
          <Input
            value={q}
            onChange={(e) => setQ(e.target.value)}
            placeholder="Search agents…"
            className="w-64 pl-9"
          />
        </div>
        <Select value={stack} onChange={(e) => setStack(e.target.value)} className="w-auto">
          <option value="">All stacks</option>
          {STACKS.map((s) => (
            <option key={s} value={s}>
              {STACK_LABELS[s] ?? s}
            </option>
          ))}
        </Select>
        <Select value={type} onChange={(e) => setType(e.target.value)} className="w-auto">
          <option value="">All types</option>
          {EXEC_TYPES.map((t) => (
            <option key={t} value={t}>
              {EXEC_LABELS[t] ?? t}
            </option>
          ))}
        </Select>
        <Select value={ownership} onChange={(e) => setOwnership(e.target.value)} className="w-auto">
          <option value="">All ownership</option>
          {OWNERSHIP_TYPES.map((o) => (
            <option key={o} value={o}>
              {o[0].toUpperCase() + o.slice(1)}
            </option>
          ))}
        </Select>
      </div>

      {error ? (
        <ErrorState title="Couldn't load agents" detail={error} onRetry={load} />
      ) : agents === null ? (
        <Card className="p-2">
          <div className="space-y-2 p-2">
            {Array.from({ length: 6 }).map((_, i) => (
              <Skeleton key={i} className="h-10 w-full" />
            ))}
          </div>
        </Card>
      ) : filtered.length === 0 ? (
        <EmptyState
          icon={Robot}
          title={q || stack || type || ownership ? 'No agents match' : 'No agents deployed'}
          description={
            q || stack || type || ownership
              ? 'Try clearing the filters or search.'
              : 'Deploy your first agent from a manifest to see it here.'
          }
          action={
            <Button asChild>
              <Link href="/deploy">
                <RocketLaunch className="h-4 w-4" aria-hidden />
                Deploy an agent
              </Link>
            </Button>
          }
        />
      ) : (
        <Card>
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>Name</TableHead>
                <TableHead>Status</TableHead>
                <TableHead>Stack</TableHead>
                <TableHead>Type</TableHead>
                <TableHead>Namespace</TableHead>
                <TableHead>ID</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {filtered.map((a) => {
                const status = liveStatus.get(a.agent_id) ?? a.status;
                return (
                  <TableRow key={a.agent_id} className="cursor-pointer">
                    <TableCell className="max-w-xs">
                      <Link href={`/agents/${a.agent_id}`} className="block">
                        <span className="font-medium text-primary hover:text-accent">{a.name}</span>
                        {a.description ? (
                          <span className="block truncate text-xs text-tertiary">{a.description}</span>
                        ) : null}
                      </Link>
                    </TableCell>
                    <TableCell>
                      <StatusBadge status={status} />
                    </TableCell>
                    <TableCell>
                      <Badge>{STACK_LABELS[a.stack ?? ''] ?? a.stack ?? '—'}</Badge>
                    </TableCell>
                    <TableCell className="text-tertiary">
                      {EXEC_LABELS[a.execution_type ?? ''] ?? a.execution_type ?? '—'}
                    </TableCell>
                    <TableCell className="text-tertiary">{a.namespace ?? 'default'}</TableCell>
                    <TableCell>
                      <Link
                        href={`/agents/${a.agent_id}`}
                        className="font-mono text-xs text-tertiary hover:text-accent"
                      >
                        {a.agent_id}
                      </Link>
                    </TableCell>
                  </TableRow>
                );
              })}
            </TableBody>
          </Table>
        </Card>
      )}
    </div>
  );
}
