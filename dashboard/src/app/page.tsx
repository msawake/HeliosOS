'use client';

import { useEffect, useMemo, useState } from 'react';
import Link from 'next/link';
import {
  Robot,
  Pulse,
  StackSimple,
  CheckCircle,
  RocketLaunch,
  ClockCounterClockwise,
  ArrowRight,
} from '@phosphor-icons/react';
import { api, type Agent, type AuditEntry } from '@/lib/api';
import { useAgentStatus } from '@/lib/hooks/useAgentStatus';
import { STACK_LABELS, relativeTime, cn } from '@/lib/utils';
import { PageHeader } from '@/components/layout/PageHeader';
import { Button } from '@/components/ui/button';
import { Card, CardContent } from '@/components/ui/card';
import { StatCard } from '@/components/ui/stat-card';
import { Badge } from '@/components/ui/badge';
import { Skeleton } from '@/components/ui/skeleton';

interface HealthInfo {
  status?: string;
  components?: {
    database?: boolean;
    agents_registered?: number;
    pending_approvals?: number;
    pending_events?: number;
    llm_providers?: string[];
    adapters?: string[];
  };
}

/** Map an audit action to a colored dot + human label. */
function actionMeta(action = ''): { tone: string; label: string } {
  const a = action.toLowerCase();
  if (a.includes('fail') || a.includes('error') || a.includes('deny')) return { tone: 'bg-danger', label: action };
  if (a.startsWith('agent.deploy') || a.includes('create')) return { tone: 'bg-success', label: action };
  if (a.startsWith('agent.undeploy') || a.includes('delete') || a.includes('remove')) return { tone: 'bg-warning', label: action };
  if (a.startsWith('auth')) return { tone: 'bg-accent', label: action };
  if (a.includes('invoke') || a.includes('run') || a.includes('tool')) return { tone: 'bg-accent', label: action };
  return { tone: 'bg-muted', label: action };
}

export default function DashboardPage() {
  const live = useAgentStatus();
  const [agents, setAgents] = useState<Agent[] | null>(null);
  const [health, setHealth] = useState<HealthInfo | null>(null);
  const [audit, setAudit] = useState<AuditEntry[] | null>(null);

  useEffect(() => {
    let cancelled = false;
    (async () => {
      const [a, h, au] = await Promise.all([
        api.listAgents().then((x) => (Array.isArray(x) ? x : [])).catch(() => []),
        (api.health() as Promise<HealthInfo>).catch(() => null),
        api.listAudit(10).then((x) => (Array.isArray(x) ? x : [])).catch(() => []),
      ]);
      if (cancelled) return;
      setAgents(a);
      setHealth(h);
      setAudit(au);
    })();
    return () => {
      cancelled = true;
    };
  }, []);

  const total = agents?.length ?? health?.components?.agents_registered ?? 0;
  const running = live.connected ? live.running : 0;
  const pendingApprovals = health?.components?.pending_approvals ?? 0;

  const byNamespace = useMemo(() => {
    const m = new Map<string, number>();
    for (const a of agents ?? []) m.set(a.namespace || 'default', (m.get(a.namespace || 'default') ?? 0) + 1);
    return [...m.entries()].map(([ns, count]) => ({ ns, count })).sort((x, y) => y.count - x.count);
  }, [agents]);

  const byStack = useMemo(() => {
    const m = new Map<string, number>();
    for (const a of agents ?? []) m.set(a.stack || '—', (m.get(a.stack || '—') ?? 0) + 1);
    return [...m.entries()].map(([k, count]) => ({ k, count })).sort((x, y) => y.count - x.count);
  }, [agents]);

  const maxNs = Math.max(1, ...byNamespace.map((n) => n.count));
  const loading = agents === null;

  return (
    <div>
      <PageHeader
        title="Dashboard"
        description="Live overview of your agent fleet, namespaces, and recent activity."
        actions={
          <Button asChild>
            <Link href="/deploy">
              <RocketLaunch className="h-4 w-4" aria-hidden />
              Deploy
            </Link>
          </Button>
        }
      />

      {/* Hero stats */}
      <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
        <StatCard
          display
          label="Agents"
          value={loading ? <Skeleton className="h-7 w-10" /> : total}
          meta={
            <Link href="/agents" className="inline-flex items-center gap-1 text-accent hover:underline">
              View all <ArrowRight className="h-3 w-3" aria-hidden />
            </Link>
          }
          action={<Robot className="h-4 w-4 text-muted" aria-hidden />}
        />
        <StatCard
          display
          label="Running"
          value={loading && !live.connected ? <Skeleton className="h-7 w-10" /> : running}
          meta={live.connected ? `of ${live.total} live` : 'polling…'}
          action={<Pulse className="h-4 w-4 text-muted" aria-hidden />}
        />
        <StatCard
          display
          label="Namespaces"
          value={loading ? <Skeleton className="h-7 w-10" /> : byNamespace.length}
          meta={
            <Link href="/access" className="inline-flex items-center gap-1 text-accent hover:underline">
              Manage <ArrowRight className="h-3 w-3" aria-hidden />
            </Link>
          }
          action={<StackSimple className="h-4 w-4 text-muted" aria-hidden />}
        />
        <StatCard
          display
          label="Pending approvals"
          value={loading ? <Skeleton className="h-7 w-10" /> : pendingApprovals}
          meta={
            <Link href="/approvals" className="inline-flex items-center gap-1 text-accent hover:underline">
              Review <ArrowRight className="h-3 w-3" aria-hidden />
            </Link>
          }
          action={<CheckCircle className="h-4 w-4 text-muted" aria-hidden />}
        />
      </div>

      <div className="mt-3 grid grid-cols-1 gap-3 lg:grid-cols-3">
        {/* Agents by namespace */}
        <Card className="lg:col-span-1">
          <CardContent className="space-y-3 pt-5">
            <div className="flex items-center justify-between">
              <h2 className="text-sm font-medium text-primary">Agents by namespace</h2>
              <StackSimple className="h-4 w-4 text-muted" aria-hidden />
            </div>
            {loading ? (
              <div className="space-y-2">
                {Array.from({ length: 3 }).map((_, i) => <Skeleton key={i} className="h-7 w-full" />)}
              </div>
            ) : byNamespace.length === 0 ? (
              <p className="text-[13px] text-tertiary">No agents yet.</p>
            ) : (
              <div className="space-y-2.5">
                {byNamespace.map(({ ns, count }) => (
                  <Link key={ns} href="/agents" className="block group">
                    <div className="flex items-center justify-between text-[13px]">
                      <span className="font-mono text-secondary group-hover:text-accent">{ns}</span>
                      <span className="tabular-nums text-tertiary">{count}</span>
                    </div>
                    <div className="mt-1 h-1.5 w-full overflow-hidden rounded-full bg-inset">
                      <div
                        className="h-full rounded-full bg-accent/70 transition-all"
                        style={{ width: `${(count / maxNs) * 100}%` }}
                      />
                    </div>
                  </Link>
                ))}
              </div>
            )}

            {!loading && byStack.length > 0 ? (
              <div className="border-t border-edge-subtle pt-3">
                <p className="mb-2 text-xs font-medium text-tertiary">By stack</p>
                <div className="flex flex-wrap gap-1.5">
                  {byStack.map(({ k, count }) => (
                    <Badge key={k} variant="outline" className="gap-1">
                      {STACK_LABELS[k] ?? k}
                      <span className="tabular-nums text-muted">{count}</span>
                    </Badge>
                  ))}
                </div>
              </div>
            ) : null}
          </CardContent>
        </Card>

        {/* Latest activity */}
        <Card className="lg:col-span-2">
          <CardContent className="space-y-3 pt-5">
            <div className="flex items-center justify-between">
              <h2 className="text-sm font-medium text-primary">Latest activity</h2>
              <ClockCounterClockwise className="h-4 w-4 text-muted" aria-hidden />
            </div>
            {audit === null ? (
              <div className="space-y-2">
                {Array.from({ length: 6 }).map((_, i) => <Skeleton key={i} className="h-8 w-full" />)}
              </div>
            ) : audit.length === 0 ? (
              <p className="text-[13px] text-tertiary">No activity recorded yet.</p>
            ) : (
              <ul className="-my-1 divide-y divide-edge-subtle">
                {audit.slice(0, 10).map((e) => {
                  const meta = actionMeta(e.action);
                  return (
                    <li key={e.id} className="flex items-center gap-3 py-2 text-[13px]">
                      <span className={cn('h-1.5 w-1.5 shrink-0 rounded-full', meta.tone)} aria-hidden />
                      <span className="font-mono text-xs text-secondary">{e.action}</span>
                      {e.resource_type ? (
                        <span className="truncate text-tertiary">
                          {e.resource_type}
                          {e.resource_id ? <span className="text-muted"> · {e.resource_id}</span> : null}
                        </span>
                      ) : null}
                      <span className="ml-auto shrink-0 text-muted">{e.actor || 'system'}</span>
                      <span className="w-16 shrink-0 text-right font-mono text-[11px] text-muted" title={e.created_at}>
                        {relativeTime(e.created_at)}
                      </span>
                    </li>
                  );
                })}
              </ul>
            )}
          </CardContent>
        </Card>
      </div>
    </div>
  );
}
