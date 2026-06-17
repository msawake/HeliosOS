'use client';

import { useState } from 'react';
import { CaretRight, ListBullets } from '@phosphor-icons/react';
import { useAgentLogs } from '@/lib/hooks/useAgentLogs';
import type { LogEvent } from '@/lib/api';
import { Button } from '@/components/ui/button';
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select';
import { Card } from '@/components/ui/card';
import { Badge } from '@/components/ui/badge';
import { Skeleton } from '@/components/ui/skeleton';
import { EmptyState } from '@/components/ui/empty-state';
import { ErrorState } from '@/components/ui/error-state';
import { CodeBlock } from '@/components/ui/code-block';
import { cn, relativeTime } from '@/lib/utils';

function eventVariant(type?: string): 'default' | 'success' | 'danger' | 'warning' | 'outline' {
  const t = (type || '').toLowerCase();
  if (t.startsWith('run.failed') || t.includes('error')) return 'danger';
  if (t.startsWith('run.completed')) return 'success';
  if (t.startsWith('tool')) return 'warning';
  if (t.startsWith('run')) return 'default';
  return 'outline';
}

function LogRow({ event }: { event: LogEvent }) {
  const [open, setOpen] = useState(false);
  const details = event.details ?? {};
  const hasDetails = Object.keys(details).length > 0;
  const prUrl = typeof details.pr_url === 'string' ? details.pr_url : undefined;

  return (
    <div className="border-b border-edge-subtle px-4 py-2.5 last:border-0">
      <div className="flex items-start gap-3">
        <button
          type="button"
          onClick={() => hasDetails && setOpen((o) => !o)}
          className={cn(
            'mt-0.5 shrink-0 text-muted transition-transform',
            hasDetails ? 'cursor-pointer hover:text-primary' : 'invisible',
            open && 'rotate-90'
          )}
          aria-label={open ? 'Collapse' : 'Expand'}
        >
          <CaretRight className="h-3.5 w-3.5" aria-hidden />
        </button>
        <span
          className="w-20 shrink-0 font-mono text-[11px] text-muted"
          title={event.ts}
        >
          {relativeTime(event.ts)}
        </span>
        <Badge variant={eventVariant(event.type)} className="shrink-0 font-mono">
          {event.type ?? 'event'}
        </Badge>
        <span className="min-w-0 flex-1 text-[13px] text-secondary">{event.description}</span>
      </div>
      {open && hasDetails ? (
        <div className="mt-2 pl-[7.25rem]">
          {prUrl ? (
            <a
              href={prUrl}
              target="_blank"
              rel="noreferrer"
              className="mb-2 inline-block text-xs text-accent hover:underline"
            >
              {prUrl}
            </a>
          ) : null}
          <CodeBlock code={JSON.stringify(details, null, 2)} wrap maxHeight={240} />
        </div>
      ) : null}
    </div>
  );
}

export function AgentLogs({ agentId }: { agentId: string }) {
  const [follow, setFollow] = useState(false);
  const [tail, setTail] = useState(200);
  const { events, error, loading, refetch } = useAgentLogs(agentId, { follow, tail });

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center gap-2">
        <Button
          variant={follow ? 'default' : 'secondary'}
          size="sm"
          onClick={() => setFollow((f) => !f)}
        >
          <span className={cn('h-1.5 w-1.5 rounded-full', follow ? 'bg-paper' : 'bg-muted')} />
          {follow ? 'Following' : 'Follow'}
        </Button>
        <Select value={String(tail)} onValueChange={(v) => setTail(Number(v))}>
          <SelectTrigger className="w-auto">
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            {[50, 100, 200, 500].map((n) => (
              <SelectItem key={n} value={String(n)}>
                Last {n}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
        <Button variant="ghost" size="sm" onClick={refetch}>
          Refresh
        </Button>
      </div>

      {error && events.length === 0 ? (
        <ErrorState title="Couldn't load logs" detail={error} onRetry={refetch} />
      ) : loading && events.length === 0 ? (
        <Card className="p-2">
          <div className="space-y-2 p-2">
            {Array.from({ length: 5 }).map((_, i) => (
              <Skeleton key={i} className="h-7 w-full" />
            ))}
          </div>
        </Card>
      ) : events.length === 0 ? (
        <EmptyState
          icon={ListBullets}
          title="No activity yet"
          description="Run start/end and every tool call shows up here. Invoke the agent to generate events."
        />
      ) : (
        <Card className="overflow-hidden">
          {events.map((e, i) => (
            <LogRow key={`${e.ts}-${e.type}-${i}`} event={e} />
          ))}
        </Card>
      )}
    </div>
  );
}
