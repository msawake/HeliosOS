'use client';

import { useState } from 'react';
import { CircleNotch } from '@phosphor-icons/react';
import { api, type RunHandle } from '@/lib/api';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { CodeBlock } from '@/components/ui/code-block';
import { StatusBadge } from '@/components/StatusBadge';
import { cn } from '@/lib/utils';

function ApprovalRow({ requestId, tool, args }: { requestId: string; tool?: string; args?: Record<string, unknown> }) {
  const [busy, setBusy] = useState<null | 'approve' | 'reject'>(null);
  const [done, setDone] = useState<string | null>(null);

  const act = async (kind: 'approve' | 'reject') => {
    setBusy(kind);
    try {
      if (kind === 'approve') await api.approve(requestId);
      else await api.reject(requestId, 'Rejected from dashboard');
      setDone(kind === 'approve' ? 'Approved' : 'Rejected');
    } catch {
      setDone('Action failed');
    } finally {
      setBusy(null);
    }
  };

  return (
    <div className="rounded-md border border-warning/25 bg-warning-wash p-3">
      <div className="flex items-center justify-between gap-2">
        <p className="text-[13px] font-medium text-primary">
          Approval needed{tool ? <> for <span className="font-mono text-warning">{tool}</span></> : null}
        </p>
        <span className="font-mono text-[11px] text-tertiary">{requestId}</span>
      </div>
      {args && Object.keys(args).length > 0 ? (
        <CodeBlock className="mt-2" code={JSON.stringify(args, null, 2)} wrap maxHeight={160} />
      ) : null}
      <div className="mt-3 flex items-center gap-2">
        {done ? (
          <span className="text-[13px] text-tertiary">{done}.</span>
        ) : (
          <>
            <Button size="sm" onClick={() => act('approve')} disabled={busy !== null}>
              {busy === 'approve' ? 'Approving…' : 'Approve'}
            </Button>
            <Button size="sm" variant="destructive" onClick={() => act('reject')} disabled={busy !== null}>
              {busy === 'reject' ? 'Rejecting…' : 'Reject'}
            </Button>
          </>
        )}
      </div>
    </div>
  );
}

export function RunPanel({
  run,
  polling,
  error,
}: {
  run: RunHandle | null;
  polling?: boolean;
  error?: string | null;
}) {
  if (!run) {
    return (
      <div className="flex items-center gap-2 text-[13px] text-tertiary">
        {polling ? <CircleNotch className="h-4 w-4 animate-spin" aria-hidden /> : null}
        {polling ? 'Starting run…' : 'No run yet.'}
      </div>
    );
  }

  const failed = (run.status || '').toLowerCase() === 'failed';

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center gap-2">
        <StatusBadge status={run.status} />
        {run.run_id ? <span className="font-mono text-[11px] text-tertiary">{run.run_id}</span> : null}
        {run.simulated ? <Badge variant="outline">simulated</Badge> : null}
        {polling ? (
          <span className="inline-flex items-center gap-1.5 text-xs text-tertiary">
            <CircleNotch className="h-3.5 w-3.5 animate-spin" aria-hidden />
            watching
          </span>
        ) : null}
      </div>

      {error ? <p className="text-xs text-warning">Poll warning: {error}</p> : null}

      {run.suspend_reason ? (
        <p className="text-[13px] text-secondary">
          Paused: <span className="text-warning">{run.suspend_reason}</span>
        </p>
      ) : null}

      {run.pending?.length ? (
        <div className="space-y-2">
          {run.pending.map((p, i) => (
            <ApprovalRow
              key={p.request_id ?? i}
              requestId={p.request_id ?? ''}
              tool={p.tool}
              args={p.args}
            />
          ))}
        </div>
      ) : null}

      {run.result ? <CodeBlock label="Result" code={run.result} wrap maxHeight={420} /> : null}

      {failed && run.error ? (
        <div
          role="alert"
          className={cn('rounded-md border border-danger/20 bg-danger-wash px-4 py-3 text-[13px] text-danger')}
        >
          {run.error}
        </div>
      ) : null}

      {run.warnings?.length ? (
        <ul className="space-y-1 text-xs text-warning">
          {run.warnings.map((w, i) => (
            <li key={i}>⚠ {w}</li>
          ))}
        </ul>
      ) : null}
    </div>
  );
}

/** Inline approval-by-question form (A2H `answer`). Used where a run/approval
 *  asks a free-text or value question rather than a tool gate. */
export function AnswerForm({ requestId, onDone }: { requestId: string; onDone?: () => void }) {
  const [text, setText] = useState('');
  const [busy, setBusy] = useState(false);
  const [done, setDone] = useState(false);

  const submit = async () => {
    setBusy(true);
    try {
      await api.answer(requestId, { text });
      setDone(true);
      onDone?.();
    } finally {
      setBusy(false);
    }
  };

  if (done) return <p className="text-[13px] text-tertiary">Answered.</p>;

  return (
    <div className="flex items-center gap-2">
      <Input
        value={text}
        onChange={(e) => setText(e.target.value)}
        placeholder="Type a reply…"
        className="flex-1"
      />
      <Button size="sm" onClick={submit} disabled={busy || !text.trim()}>
        {busy ? 'Sending…' : 'Answer'}
      </Button>
    </div>
  );
}
