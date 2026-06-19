'use client';

import { useCallback, useEffect, useState } from 'react';
import { CheckCircle } from '@phosphor-icons/react';
import { api, type Approval } from '@/lib/api';
import { PageHeader } from '@/components/layout/PageHeader';
import { Card, CardContent } from '@/components/ui/card';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Badge } from '@/components/ui/badge';
import { Skeleton } from '@/components/ui/skeleton';
import { EmptyState } from '@/components/ui/empty-state';
import { ErrorState } from '@/components/ui/error-state';
import { CodeBlock } from '@/components/ui/code-block';
import { AnswerForm } from '@/components/RunPanel';

/** "5m ago" style relative time from an ISO timestamp; null if unparseable. */
function relTime(iso: string | null): string | null {
  if (!iso) return null;
  const t = Date.parse(iso);
  if (Number.isNaN(t)) return null;
  const s = Math.max(0, Math.round((Date.now() - t) / 1000));
  if (s < 60) return `${s}s ago`;
  const m = Math.round(s / 60);
  if (m < 60) return `${m}m ago`;
  const h = Math.round(m / 60);
  if (h < 24) return `${h}h ago`;
  return `${Math.round(h / 24)}d ago`;
}

function ApprovalCard({ approval, onResolved }: { approval: Approval; onResolved: () => void }) {
  // Some approval sources (A2H questions, malformed rows) send objects where a
  // string is expected; rendering one directly as a React child crashes the
  // page. Coerce every directly-rendered field to text and skip non-strings.
  const txt = (v: unknown): string | null =>
    typeof v === 'string' && v.trim() ? v : typeof v === 'number' ? String(v) : null;
  const reqId = txt(approval.request_id ?? approval.id) ?? '';
  const runId = txt(approval.run_id ?? approval.continuation_id);
  const agent = txt(approval.agent ?? approval.agent_id ?? approval.from_agent ?? approval.requesting_agent);
  const tool = txt(approval.tool);
  const source = txt(approval.source);
  const sourceLabel = source === 'a2h' ? 'agent question' : source === 'runtime' ? 'kernel gate' : source;
  const age = relTime(txt(approval.created_at ?? approval.timestamp));
  const title = txt(approval.content?.question ?? approval.title);
  const description = txt(approval.description);
  const riskText = txt(approval.risk_assessment ?? approval.risk);
  const context = approval.content?.context ?? approval.context;
  const kind = (approval.content?.kind ?? '').toLowerCase();
  const isQuestion = ['text', 'choice', 'number', 'question'].includes(kind);

  const [busy, setBusy] = useState<null | 'approve' | 'reject' | 'delete'>(null);
  const [reason, setReason] = useState('');
  const [showReject, setShowReject] = useState(false);
  const [resolved, setResolved] = useState<string | null>(null);

  const act = async (kindArg: 'approve' | 'reject') => {
    setBusy(kindArg);
    try {
      if (kindArg === 'approve') await api.approve(reqId);
      else await api.reject(reqId, reason || undefined);
      setResolved(kindArg === 'approve' ? 'Approved' : 'Rejected');
      onResolved();
    } catch {
      setResolved('Action failed');
    } finally {
      setBusy(null);
    }
  };

  // Remove an entry from the queue regardless of source (rejects the run, or
  // clears an A2H question via the gateway) — for stale / stuck / orphaned rows.
  const del = async () => {
    setBusy('delete');
    try {
      await api.dismiss(reqId);
      setResolved('Deleted');
      onResolved();
    } catch {
      setResolved('Delete failed');
    } finally {
      setBusy(null);
    }
  };

  return (
    <Card>
      <CardContent className="space-y-3 py-4">
        <div className="flex flex-wrap items-center gap-2">
          <Badge variant="warning">pending</Badge>
          {sourceLabel ? <Badge variant="outline">{sourceLabel}</Badge> : null}
          {riskText ? <Badge variant="outline">{riskText} risk</Badge> : null}
          {tool ? (
            <span className="rounded bg-inset px-1.5 py-0.5 font-mono text-[11px] text-secondary">
              {tool}
            </span>
          ) : null}
          {agent ? (
            <span className="text-[13px] text-secondary">
              from <span className="font-medium text-primary">{agent}</span>
            </span>
          ) : null}
          {age ? <span className="text-[11px] text-tertiary">· {age}</span> : null}
          <span className="ml-auto font-mono text-[11px] text-tertiary">{reqId}</span>
        </div>

        {title ? <p className="text-[13px] text-primary">{title}</p> : null}
        {description ? <p className="text-[13px] text-secondary">{description}</p> : null}
        {runId ? (
          <p className="text-xs text-tertiary">
            run <span className="font-mono">{runId}</span>
          </p>
        ) : null}
        {context && typeof context === 'object' && Object.keys(context).length > 0 ? (
          <CodeBlock code={JSON.stringify(context, null, 2)} wrap maxHeight={180} />
        ) : null}

        {resolved ? (
          <p className="text-[13px] text-tertiary">{resolved}.</p>
        ) : isQuestion ? (
          <div className="space-y-2">
            <AnswerForm requestId={reqId} onDone={onResolved} />
            <Button size="sm" variant="ghost" onClick={del} disabled={busy !== null}>
              {busy === 'delete' ? 'Deleting…' : 'Delete'}
            </Button>
          </div>
        ) : (
          <div className="space-y-2">
            <div className="flex items-center gap-2">
              <Button size="sm" onClick={() => act('approve')} disabled={busy !== null}>
                {busy === 'approve' ? 'Approving…' : 'Approve'}
              </Button>
              <Button
                size="sm"
                variant="destructive"
                onClick={() => (showReject ? act('reject') : setShowReject(true))}
                disabled={busy !== null}
              >
                {busy === 'reject' ? 'Rejecting…' : 'Reject'}
              </Button>
              <Button
                size="sm"
                variant="ghost"
                className="ml-auto"
                onClick={del}
                disabled={busy !== null}
              >
                {busy === 'delete' ? 'Deleting…' : 'Delete'}
              </Button>
            </div>
            {showReject ? (
              <Input
                value={reason}
                onChange={(e) => setReason(e.target.value)}
                placeholder="Reason (optional)"
                className="max-w-md"
              />
            ) : null}
          </div>
        )}
      </CardContent>
    </Card>
  );
}

export default function ApprovalsPage() {
  const [approvals, setApprovals] = useState<Approval[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [fromAgent, setFromAgent] = useState('');

  // `silent` refetches in place (no skeleton flicker) — used by the poller so
  // approvals parked while this page is open appear without a manual reload.
  const load = useCallback(async (silent = false) => {
    if (!silent) setApprovals(null);
    setError(null);
    try {
      const data = await api.listApprovals(fromAgent || undefined);
      setApprovals(Array.isArray(data) ? data : []);
    } catch (e) {
      if (!silent) setError(e instanceof Error ? e.message : 'Failed to load approvals');
    }
  }, [fromAgent]);

  useEffect(() => {
    load();
    const t = setInterval(() => load(true), 4000);
    return () => clearInterval(t);
  }, [load]);

  return (
    <div>
      <PageHeader
        title="Approvals"
        description="Human-in-the-loop requests waiting on a decision."
        actions={
          <Input
            value={fromAgent}
            onChange={(e) => setFromAgent(e.target.value)}
            placeholder="Filter by agent id…"
            className="w-56"
          />
        }
      />

      {error ? (
        <ErrorState title="Couldn't load approvals" detail={error} onRetry={load} />
      ) : approvals === null ? (
        <div className="space-y-3">
          {Array.from({ length: 3 }).map((_, i) => (
            <Skeleton key={i} className="h-28 w-full" />
          ))}
        </div>
      ) : approvals.length === 0 ? (
        <EmptyState
          icon={CheckCircle}
          title="Nothing pending"
          description="Approval gates and A2H questions land here when an agent needs a human decision."
        />
      ) : (
        <div className="space-y-3">
          {approvals.map((a, i) => (
            <ApprovalCard key={a.request_id ?? a.id ?? i} approval={a} onResolved={load} />
          ))}
        </div>
      )}
    </div>
  );
}
