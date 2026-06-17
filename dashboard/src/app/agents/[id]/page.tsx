'use client';

import { useCallback, useEffect, useState } from 'react';
import { useParams } from 'next/navigation';
import { api, type Agent } from '@/lib/api';
import { PageHeader } from '@/components/layout/PageHeader';
import { Tabs, TabsList, TabsTrigger, TabsContent } from '@/components/ui/tabs';
import { Skeleton } from '@/components/ui/skeleton';
import { ErrorState } from '@/components/ui/error-state';
import { StatusBadge } from '@/components/StatusBadge';
import { Button } from '@/components/ui/button';
import Link from 'next/link';
import { ChatCircle } from '@phosphor-icons/react';
import { AgentOverview } from '@/components/agent/AgentOverview';
import { AgentInvoke } from '@/components/agent/AgentInvoke';
import { AgentLogs } from '@/components/agent/AgentLogs';
import { AgentEdit } from '@/components/agent/AgentEdit';

export default function AgentDetailPage() {
  const params = useParams<{ id: string }>();
  const id = decodeURIComponent(params.id);
  const [agent, setAgent] = useState<Agent | null>(null);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    setAgent(null);
    setError(null);
    try {
      setAgent(await api.getAgent(id));
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Failed to load agent');
    }
  }, [id]);

  useEffect(() => {
    load();
  }, [load]);

  if (error) {
    return (
      <div>
        <PageHeader title="Agent" back={{ href: '/agents', label: 'Agents' }} />
        <ErrorState title="Couldn't load this agent" detail={error} onRetry={load} />
      </div>
    );
  }

  if (!agent) {
    return (
      <div>
        <PageHeader title={<Skeleton className="h-7 w-56" />} back={{ href: '/agents', label: 'Agents' }} />
        <Skeleton className="h-9 w-full max-w-md" />
        <div className="mt-6 space-y-3">
          <Skeleton className="h-40 w-full" />
        </div>
      </div>
    );
  }

  return (
    <div>
      <PageHeader
        title={agent.name}
        description={agent.description}
        back={{ href: '/agents', label: 'Agents' }}
        actions={
          <>
            <StatusBadge status={agent.status} />
            <Button variant="secondary" asChild>
              <Link href={`/agents/${encodeURIComponent(id)}/chat`}>
                <ChatCircle className="h-4 w-4" aria-hidden />
                Chat
              </Link>
            </Button>
          </>
        }
      />

      <Tabs defaultValue="overview">
        <TabsList>
          <TabsTrigger value="overview">Overview</TabsTrigger>
          <TabsTrigger value="invoke">Invoke</TabsTrigger>
          <TabsTrigger value="logs">Logs</TabsTrigger>
          <TabsTrigger value="edit">Edit</TabsTrigger>
        </TabsList>

        <TabsContent value="overview">
          <AgentOverview agent={agent} onChanged={load} />
        </TabsContent>
        <TabsContent value="invoke">
          <AgentInvoke agentId={id} />
        </TabsContent>
        <TabsContent value="logs">
          <AgentLogs agentId={id} />
        </TabsContent>
        <TabsContent value="edit">
          <AgentEdit agent={agent} onSaved={load} />
        </TabsContent>
      </Tabs>
    </div>
  );
}
