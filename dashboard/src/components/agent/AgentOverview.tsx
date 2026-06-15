'use client';

import { useState } from 'react';
import { useRouter } from 'next/navigation';
import { api, type Agent } from '@/lib/api';
import { Card, CardHeader, CardTitle, CardContent } from '@/components/ui/card';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { CodeBlock } from '@/components/ui/code-block';
import {
  Dialog,
  DialogTrigger,
  DialogContent,
  DialogHeader,
  DialogTitle,
  DialogDescription,
  DialogFooter,
  DialogClose,
} from '@/components/ui/dialog';
import { STACK_LABELS, EXEC_LABELS } from '@/lib/utils';

function Field({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div className="flex flex-col gap-1 py-3">
      <dt className="text-xs text-tertiary">{label}</dt>
      <dd className="text-[13px] text-primary">{children || <span className="text-muted">—</span>}</dd>
    </div>
  );
}

export function AgentOverview({ agent, onChanged }: { agent: Agent; onChanged: () => void }) {
  const router = useRouter();
  const [busy, setBusy] = useState<null | 'stop' | 'undeploy'>(null);

  const metadata = Object.entries(agent.metadata ?? {}).filter(([k]) => !k.startsWith('_'));
  const llm = agent.llm_config;

  const stop = async () => {
    setBusy('stop');
    try {
      await api.stopAgent(agent.agent_id);
      onChanged();
    } finally {
      setBusy(null);
    }
  };

  const undeploy = async () => {
    setBusy('undeploy');
    try {
      await api.undeployAgent(agent.agent_id);
      router.push('/');
    } finally {
      setBusy(null);
    }
  };

  return (
    <div className="space-y-6">
      <Card>
        <CardContent className="px-5 py-1">
          <dl className="grid grid-cols-2 gap-x-8 sm:grid-cols-3 lg:grid-cols-4 [&>div]:border-b [&>div]:border-edge-subtle">
            <Field label="Agent ID">
              <span className="font-mono text-xs">{agent.agent_id}</span>
            </Field>
            <Field label="Stack">
              <Badge>{STACK_LABELS[agent.stack ?? ''] ?? agent.stack ?? '—'}</Badge>
            </Field>
            <Field label="Execution type">
              {EXEC_LABELS[agent.execution_type ?? ''] ?? agent.execution_type}
            </Field>
            <Field label="Namespace">{agent.namespace ?? 'default'}</Field>
            <Field label="Department">{agent.department}</Field>
            <Field label="Ownership">{agent.ownership}</Field>
            <Field label="Schedule">
              {agent.schedule ? <span className="font-mono text-xs">{agent.schedule}</span> : null}
            </Field>
            <Field label="Model">
              {llm?.chat_model ? (
                <span className="font-mono text-xs">
                  {llm.chat_model}
                  {llm.provider ? <span className="text-tertiary"> · {llm.provider}</span> : null}
                </span>
              ) : null}
            </Field>
          </dl>
        </CardContent>
      </Card>

      {agent.goal ? (
        <Card>
          <CardHeader>
            <CardTitle>Goal</CardTitle>
          </CardHeader>
          <CardContent className="text-[13px] text-secondary">{agent.goal}</CardContent>
        </Card>
      ) : null}

      {agent.tools?.length ? (
        <Card>
          <CardHeader>
            <CardTitle>Tools</CardTitle>
          </CardHeader>
          <CardContent className="flex flex-wrap gap-1.5">
            {agent.tools.map((t) => (
              <Badge key={t} variant="outline" className="font-mono">
                {t}
              </Badge>
            ))}
          </CardContent>
        </Card>
      ) : null}

      {agent.event_triggers?.length ? (
        <Card>
          <CardHeader>
            <CardTitle>Event triggers</CardTitle>
          </CardHeader>
          <CardContent className="flex flex-wrap gap-1.5">
            {agent.event_triggers.map((t) => (
              <Badge key={t} variant="outline" className="font-mono">
                {t}
              </Badge>
            ))}
          </CardContent>
        </Card>
      ) : null}

      {metadata.length ? (
        <Card>
          <CardHeader>
            <CardTitle>Metadata</CardTitle>
          </CardHeader>
          <CardContent>
            <dl className="grid grid-cols-1 gap-x-8 sm:grid-cols-2">
              {metadata.map(([k, v]) => (
                <div key={k} className="flex justify-between gap-4 border-b border-edge-subtle py-2 text-[13px]">
                  <dt className="text-tertiary">{k}</dt>
                  <dd className="font-mono text-xs text-primary">{String(v)}</dd>
                </div>
              ))}
            </dl>
          </CardContent>
        </Card>
      ) : null}

      {agent.system_prompt ? (
        <CodeBlock label="System prompt" code={agent.system_prompt} wrap maxHeight={360} />
      ) : null}

      {/* Danger zone */}
      <Card className="border-danger/20">
        <CardHeader>
          <CardTitle className="text-danger">Danger zone</CardTitle>
        </CardHeader>
        <CardContent className="flex flex-wrap items-center gap-3">
          <Dialog>
            <DialogTrigger asChild>
              <Button variant="secondary" disabled={busy !== null}>
                Stop
              </Button>
            </DialogTrigger>
            <DialogContent>
              <DialogHeader>
                <DialogTitle>Stop this agent?</DialogTitle>
                <DialogDescription>
                  The scheduler turns off and the agent stays in the registry. Deploy again to re-enable it.
                </DialogDescription>
              </DialogHeader>
              <DialogFooter>
                <DialogClose asChild>
                  <Button variant="ghost">Cancel</Button>
                </DialogClose>
                <DialogClose asChild>
                  <Button variant="secondary" onClick={stop}>
                    Stop agent
                  </Button>
                </DialogClose>
              </DialogFooter>
            </DialogContent>
          </Dialog>

          <Dialog>
            <DialogTrigger asChild>
              <Button variant="destructive" disabled={busy !== null}>
                Undeploy
              </Button>
            </DialogTrigger>
            <DialogContent>
              <DialogHeader>
                <DialogTitle>Undeploy this agent?</DialogTitle>
                <DialogDescription>
                  This removes <span className="font-mono text-primary">{agent.agent_id}</span> from the
                  registry completely. This cannot be undone.
                </DialogDescription>
              </DialogHeader>
              <DialogFooter>
                <DialogClose asChild>
                  <Button variant="ghost">Cancel</Button>
                </DialogClose>
                <DialogClose asChild>
                  <Button variant="destructive" onClick={undeploy}>
                    Undeploy
                  </Button>
                </DialogClose>
              </DialogFooter>
            </DialogContent>
          </Dialog>

          <p className="text-xs text-tertiary">
            Stop pauses the scheduler. Undeploy removes the agent entirely.
          </p>
        </CardContent>
      </Card>
    </div>
  );
}
