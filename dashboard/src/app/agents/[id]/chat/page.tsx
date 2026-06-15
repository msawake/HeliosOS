'use client';

import { useCallback, useEffect, useRef, useState } from 'react';
import { useParams } from 'next/navigation';
import { PaperPlaneRight, CircleNotch } from '@phosphor-icons/react';
import { api, isRunSettled, type Agent, type RunHandle } from '@/lib/api';
import { PageHeader } from '@/components/layout/PageHeader';
import { Button } from '@/components/ui/button';
import { Textarea } from '@/components/ui/input';
import { Card } from '@/components/ui/card';
import { Skeleton } from '@/components/ui/skeleton';
import { ErrorState } from '@/components/ui/error-state';
import { cn } from '@/lib/utils';

interface Msg {
  role: 'human' | 'agent' | 'system';
  content: string;
}

const HUMAN = 'operator';
const sleep = (ms: number) => new Promise((r) => setTimeout(r, ms));

export default function AgentChatPage() {
  const params = useParams<{ id: string }>();
  const id = decodeURIComponent(params.id);

  const [agent, setAgent] = useState<Agent | null>(null);
  const [chatId, setChatId] = useState<string | null>(null);
  const [openError, setOpenError] = useState<string | null>(null);
  const [messages, setMessages] = useState<Msg[]>([]);
  const [input, setInput] = useState('');
  const [busy, setBusy] = useState(false);
  const [pending, setPending] = useState<RunHandle | null>(null);
  const threadRef = useRef<HTMLDivElement>(null);

  const open = useCallback(async () => {
    setOpenError(null);
    try {
      const a = await api.getAgent(id);
      setAgent(a);
      const chat = await api.openChat({
        agent_pid: id,
        agent_namespace: a.namespace ?? 'default',
        agent_name: a.name,
        human_name: HUMAN,
        human_namespace: 'humans',
        topic: `Chat with ${a.name}`,
      });
      setChatId(chat.id);
    } catch (e) {
      setOpenError(e instanceof Error ? e.message : 'Could not open chat');
    }
  }, [id]);

  useEffect(() => {
    open();
    return () => {
      // Best-effort close on unmount.
      setChatId((cid) => {
        if (cid) api.closeChat(cid).catch(() => {});
        return cid;
      });
    };
  }, [open]);

  useEffect(() => {
    threadRef.current?.scrollTo({ top: threadRef.current.scrollHeight });
  }, [messages, pending]);

  const send = async () => {
    const content = input.trim();
    if (!content || !chatId || busy) return;
    setInput('');
    setMessages((m) => [...m, { role: 'human', content }]);
    setBusy(true);
    setPending(null);
    try {
      await api.postChatMessage(chatId, { role: 'human', sender: HUMAN, content, client_drives: true });
      const handle = await api.invoke(id, {
        prompt: content,
        sessionId: chatId,
        context: { chat_id: chatId, session_id: chatId },
      });

      const reply = await driveRun(handle);
      if (reply) {
        setMessages((m) => [...m, { role: 'agent', content: reply }]);
        await api
          .postChatMessage(chatId, { role: 'agent', sender: agent?.name ?? 'agent', content: reply })
          .catch(() => {});
      }
    } catch (e) {
      setMessages((m) => [
        ...m,
        { role: 'system', content: e instanceof Error ? e.message : 'Send failed' },
      ]);
    } finally {
      setBusy(false);
      setPending(null);
    }
  };

  /** Poll the run to completion, surfacing approval gates inline. Returns the result text. */
  const driveRun = async (initial: RunHandle): Promise<string | null> => {
    let run = initial;
    let runId = run.run_id;
    // Loop until terminal.
    // (Approvals are resolved via the inline buttons, which flip `pending`.)
    for (;;) {
      if (isRunSettled(run.status) && (run.status || '').toLowerCase() !== 'paused') {
        const s = (run.status || '').toLowerCase();
        if (s === 'failed') return run.error || 'Run failed.';
        return run.result ?? '(no result)';
      }
      if (run.pending?.length) {
        setPending(run);
      }
      await sleep(2000);
      if (!runId) return run.result ?? null;
      run = await api.getRun(runId);
      runId = run.run_id ?? runId;
      if (!run.pending?.length) setPending(null);
    }
  };

  if (openError) {
    return (
      <div>
        <PageHeader title="Chat" back={{ href: `/agents/${encodeURIComponent(id)}`, label: 'Agent' }} />
        <ErrorState title="Couldn't open chat" detail={openError} onRetry={open} />
      </div>
    );
  }

  return (
    <div className="flex h-[calc(100vh-var(--topbar-height)-3.5rem)] flex-col">
      <PageHeader
        title={agent ? `Chat — ${agent.name}` : 'Chat'}
        back={{ href: `/agents/${encodeURIComponent(id)}`, label: 'Agent' }}
      />

      <Card className="flex min-h-0 flex-1 flex-col overflow-hidden">
        <div ref={threadRef} className="flex-1 space-y-4 overflow-y-auto px-5 py-5">
          {!chatId ? (
            <div className="space-y-3">
              <Skeleton className="h-10 w-2/3" />
              <Skeleton className="ml-auto h-10 w-1/2" />
            </div>
          ) : messages.length === 0 ? (
            <p className="text-[13px] text-tertiary">
              Session open. Say something to {agent?.name ?? 'the agent'}.
            </p>
          ) : (
            messages.map((m, i) => <Bubble key={i} msg={m} agentName={agent?.name} />)
          )}

          {pending?.pending?.length ? (
            <InlineApprovals run={pending} onResolved={() => setPending(null)} />
          ) : busy ? (
            <div className="flex items-center gap-2 text-[13px] text-tertiary">
              <CircleNotch className="h-4 w-4 animate-spin" aria-hidden />
              {agent?.name ?? 'Agent'} is working…
            </div>
          ) : null}
        </div>

        <div className="border-t border-edge px-4 py-3">
          <div className="flex items-end gap-2">
            <Textarea
              value={input}
              onChange={(e) => setInput(e.target.value)}
              placeholder="Message…"
              className="min-h-11 flex-1"
              disabled={!chatId}
              onKeyDown={(e) => {
                if (e.key === 'Enter' && !e.shiftKey) {
                  e.preventDefault();
                  send();
                }
              }}
            />
            <Button onClick={send} disabled={!chatId || busy || !input.trim()} size="icon">
              <PaperPlaneRight className="h-4 w-4" aria-hidden />
            </Button>
          </div>
        </div>
      </Card>
    </div>
  );
}

function Bubble({ msg, agentName }: { msg: Msg; agentName?: string }) {
  if (msg.role === 'system') {
    return (
      <p className="text-center text-xs text-warning">{msg.content}</p>
    );
  }
  const human = msg.role === 'human';
  return (
    <div className={cn('flex', human ? 'justify-end' : 'justify-start')}>
      <div className={cn('max-w-[80%] space-y-1', human && 'text-right')}>
        <p className="text-[11px] text-muted">{human ? 'You' : agentName ?? 'Agent'}</p>
        <div
          className={cn(
            'inline-block whitespace-pre-wrap rounded-lg px-3.5 py-2 text-[13px]',
            human ? 'bg-ink text-paper' : 'border border-edge bg-surface text-primary'
          )}
        >
          {msg.content}
        </div>
      </div>
    </div>
  );
}

function InlineApprovals({ run, onResolved }: { run: RunHandle; onResolved: () => void }) {
  const [busy, setBusy] = useState<string | null>(null);
  const act = async (requestId: string, kind: 'approve' | 'reject') => {
    setBusy(requestId);
    try {
      if (kind === 'approve') await api.approve(requestId);
      else await api.reject(requestId, 'Rejected from chat');
      onResolved();
    } finally {
      setBusy(null);
    }
  };
  return (
    <div className="space-y-2">
      {run.pending?.map((p, i) => (
        <div key={p.request_id ?? i} className="rounded-md border border-warning/25 bg-warning-wash p-3">
          <p className="text-[13px] text-primary">
            Approval needed{p.tool ? <> for <span className="font-mono text-warning">{p.tool}</span></> : null}
          </p>
          <div className="mt-2 flex gap-2">
            <Button size="sm" onClick={() => act(p.request_id ?? '', 'approve')} disabled={busy !== null}>
              Approve
            </Button>
            <Button
              size="sm"
              variant="destructive"
              onClick={() => act(p.request_id ?? '', 'reject')}
              disabled={busy !== null}
            >
              Reject
            </Button>
          </div>
        </div>
      ))}
    </div>
  );
}
