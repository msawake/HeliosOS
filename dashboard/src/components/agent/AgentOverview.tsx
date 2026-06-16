'use client';

import { useState } from 'react';
import { useRouter } from 'next/navigation';
import { api, type Agent } from '@/lib/api';
import { useCopy } from '@/lib/use-copy';
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
import { AgentEnvironment } from '@/components/agent/AgentEnvironment';

function Field({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div className="flex flex-col gap-1 py-3">
      <dt className="text-xs text-tertiary">{label}</dt>
      <dd className="text-[13px] text-primary">{children || <span className="text-muted">—</span>}</dd>
    </div>
  );
}

/** Human-in-the-loop approval rules, packed into metadata._governance by the
 * manifest's `spec.governance`. */
type ApprovalRule = {
  tool: string;
  mode?: string;
  approvers?: string[];
  sla_hours?: number;
  on_timeout?: string;
  priority?: string;
  reason?: string;
};
type Governance = { approvals?: ApprovalRule[]; audit_level?: string };

/** Per-agent Drive identity, packed into metadata._drive by the manifest. */
type DriveCfg = { service_account?: string; folder_id?: string; access?: string };

const MODE_BADGE: Record<string, { variant: 'warning' | 'success' | 'outline'; label: string }> = {
  always: { variant: 'warning', label: 'approval required' },
  never: { variant: 'success', label: 'auto-approved' },
  conditional: { variant: 'outline', label: 'conditional' },
};

export function AgentOverview({ agent, onChanged }: { agent: Agent; onChanged: () => void }) {
  const router = useRouter();
  const [busy, setBusy] = useState<null | 'stop' | 'undeploy'>(null);

  const metadata = Object.entries(agent.metadata ?? {}).filter(([k]) => !k.startsWith('_'));
  const llm = agent.llm_config;
  const governance = (agent.metadata?._governance ?? null) as Governance | null;
  const approvals = governance?.approvals ?? [];
  const drive = (agent.metadata?._drive ?? null) as DriveCfg | null;
  const { copied, copy } = useCopy();

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

      {approvals.length ? (
        <Card>
          <CardHeader>
            <CardTitle>Governance — tool approvals</CardTitle>
          </CardHeader>
          <CardContent className="space-y-2.5">
            <p className="text-xs text-tertiary">
              Rules apply top-to-bottom; the first match decides. A gated call pauses the run for
              human approval before it executes.
            </p>
            {approvals.map((r, i) => {
              const m = MODE_BADGE[r.mode ?? 'always'] ?? MODE_BADGE.always;
              return (
                <div
                  key={`${r.tool}-${i}`}
                  className="flex flex-wrap items-center gap-2 border-b border-edge-subtle pb-2.5 last:border-0 last:pb-0"
                >
                  <Badge variant="outline" className="font-mono">
                    {r.tool}
                  </Badge>
                  <Badge variant={m.variant}>{m.label}</Badge>
                  {r.approvers?.length ? (
                    <span className="text-xs text-tertiary">approvers: {r.approvers.join(', ')}</span>
                  ) : null}
                  {r.sla_hours ? <span className="text-xs text-tertiary">· SLA {r.sla_hours}h</span> : null}
                  {r.on_timeout ? (
                    <span className="text-xs text-tertiary">· on timeout: {r.on_timeout}</span>
                  ) : null}
                  {r.reason ? <p className="w-full text-xs text-secondary">{r.reason}</p> : null}
                </div>
              );
            })}
            {governance?.audit_level ? (
              <p className="pt-1 text-xs text-tertiary">
                Audit level: <span className="text-secondary">{governance.audit_level}</span>
              </p>
            ) : null}
          </CardContent>
        </Card>
      ) : null}

      {drive?.service_account ? (
        <Card>
          <CardHeader>
            <CardTitle>Service account · Google Drive</CardTitle>
          </CardHeader>
          <CardContent className="space-y-3">
            <p className="text-xs text-tertiary">
              This agent has its own service account. Share a Google Drive folder with this email
              {drive.access === 'readwrite' ? ' (as Content Manager, in a Shared Drive)' : ''} and the
              agent can {drive.access === 'readwrite' ? 'read context + write reports' : 'read context files'}.
            </p>
            <div className="flex items-center gap-2 rounded-md border border-edge bg-inset px-3 py-2">
              <code className="flex-1 break-all font-mono text-xs text-primary">{drive.service_account}</code>
              <Button size="sm" variant="ghost" onClick={() => copy(drive.service_account!)}>
                {copied ? 'Copied' : 'Copy'}
              </Button>
            </div>
            <dl className="grid grid-cols-2 gap-x-8 text-[13px]">
              <div className="flex justify-between border-b border-edge-subtle py-2">
                <dt className="text-tertiary">Access</dt>
                <dd><Badge variant="outline">{drive.access ?? 'read'}</Badge></dd>
              </div>
              <div className="flex justify-between border-b border-edge-subtle py-2">
                <dt className="text-tertiary">Context folder</dt>
                <dd className="font-mono text-xs text-primary">
                  {drive.folder_id && drive.folder_id !== 'REPLACE_WITH_FOLDER_ID'
                    ? drive.folder_id
                    : <span className="text-muted">not set</span>}
                </dd>
              </div>
            </dl>
          </CardContent>
        </Card>
      ) : null}

      <AgentEnvironment agent={agent} onChanged={onChanged} />

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
