'use client';

import { useCallback, useEffect, useRef, useState } from 'react';
import { useParams } from 'next/navigation';
import {
  PaperPlaneRight,
  CircleNotch,
  CaretRight,
  Wrench,
  CheckCircle,
  XCircle,
} from '@phosphor-icons/react';
import { api, type Agent, type ChatStreamEvent } from '@/lib/api';
import { PageHeader } from '@/components/layout/PageHeader';
import { Button } from '@/components/ui/button';
import { Textarea } from '@/components/ui/input';
import { Card } from '@/components/ui/card';
import { CodeBlock } from '@/components/ui/code-block';
import { Markdown } from '@/components/ui/markdown';
import { Skeleton } from '@/components/ui/skeleton';
import { ErrorState } from '@/components/ui/error-state';
import { cn } from '@/lib/utils';

type ToolStatus = 'running' | 'ok' | 'error';

interface TextPart {
  kind: 'text';
  text: string;
}
interface ToolPart {
  kind: 'tool';
  id: number;
  name: string;
  input?: Record<string, unknown>;
  result?: unknown;
  status: ToolStatus;
}
type Part = TextPart | ToolPart;

interface HitlRequest {
  request_id: string;
  title?: string;
  description?: string;
  risk?: string;
}

interface Msg {
  role: 'human' | 'agent' | 'system';
  /** Human / system turns. */
  content?: string;
  /** Agent turns — interleaved prose and tool calls in arrival order. */
  parts?: Part[];
}

const HUMAN = 'operator';

/** True when a tool result carries an error (top-level or nested under `result`). */
function isErrResult(result: unknown): boolean {
  if (!result || typeof result !== 'object') return false;
  const r = result as Record<string, unknown>;
  if ('error' in r && r.error) return true;
  const inner = r.result;
  if (inner && typeof inner === 'object' && 'error' in (inner as Record<string, unknown>)) return true;
  return false;
}

export default function AgentChatPage() {
  const params = useParams<{ id: string }>();
  const id = decodeURIComponent(params.id);

  const [agent, setAgent] = useState<Agent | null>(null);
  const [chatId, setChatId] = useState<string | null>(null);
  const [openError, setOpenError] = useState<string | null>(null);
  const [messages, setMessages] = useState<Msg[]>([]);
  const [streaming, setStreaming] = useState<Msg | null>(null);
  const [approvals, setApprovals] = useState<HitlRequest[]>([]);
  const [input, setInput] = useState('');
  const [busy, setBusy] = useState(false);
  const threadRef = useRef<HTMLDivElement>(null);

  // Working buffer for the in-flight agent turn (mutated outside React state,
  // mirrored into `streaming` for rendering and committed on completion).
  const draftRef = useRef<Part[]>([]);
  const toolSeq = useRef(0);
  const abortRef = useRef<AbortController | null>(null);

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
      abortRef.current?.abort();
      setChatId((cid) => {
        if (cid) api.closeChat(cid).catch(() => {});
        return cid;
      });
    };
  }, [open]);

  useEffect(() => {
    threadRef.current?.scrollTo({ top: threadRef.current.scrollHeight });
  }, [messages, streaming, approvals]);

  /** Fold one SSE event into the in-flight agent turn. */
  const applyEvent = (ev: ChatStreamEvent) => {
    let parts = draftRef.current;
    switch (ev.type) {
      case 'text_delta': {
        const last = parts[parts.length - 1];
        if (last?.kind === 'text') {
          parts = parts.slice(0, -1).concat({ kind: 'text', text: last.text + ev.content });
        } else {
          parts = parts.concat({ kind: 'text', text: ev.content });
        }
        break;
      }
      case 'tool_call':
        parts = parts.concat({
          kind: 'tool',
          id: toolSeq.current++,
          name: ev.name,
          input: ev.input ?? {},
          status: 'running',
        });
        break;
      case 'tool_result': {
        const idx = parts.findIndex(
          (p) => p.kind === 'tool' && p.name === ev.name && p.status === 'running'
        );
        if (idx >= 0) {
          const tp = parts[idx] as ToolPart;
          const next = parts.slice();
          next[idx] = { ...tp, result: ev.result, status: isErrResult(ev.result) ? 'error' : 'ok' };
          parts = next;
        }
        break;
      }
      case 'hitl_request':
        setApprovals((a) => [
          ...a,
          { request_id: ev.request_id, title: ev.title, description: ev.description, risk: ev.risk },
        ]);
        return;
      case 'error':
        parts = parts.concat({
          kind: 'text',
          text: `${parts.length ? '\n\n' : ''}⚠ ${ev.error}`,
        });
        break;
      default:
        return; // session / done — nothing to fold in
    }
    draftRef.current = parts;
    setStreaming({ role: 'agent', parts });
  };

  const send = async () => {
    const content = input.trim();
    if (!content || !chatId || busy) return;

    abortRef.current?.abort();
    const ctrl = new AbortController();
    abortRef.current = ctrl;

    setInput('');
    setMessages((m) => [...m, { role: 'human', content }]);
    setApprovals([]);
    draftRef.current = [];
    toolSeq.current = 0;
    setStreaming({ role: 'agent', parts: [] });
    setBusy(true);

    // Best-effort transcript logging (A2H chat record); independent of the stream.
    api
      .postChatMessage(chatId, { role: 'human', sender: HUMAN, content, client_drives: true })
      .catch(() => {});

    let fullText = '';
    try {
      await api.streamChat(id, { message: content, sessionId: chatId, signal: ctrl.signal }, (ev) => {
        if (ev.type === 'text_delta') fullText += ev.content;
        applyEvent(ev);
      });
    } catch (e) {
      if (!ctrl.signal.aborted) {
        applyEvent({ type: 'error', error: e instanceof Error ? e.message : 'Stream failed' });
      }
    } finally {
      const finalParts = draftRef.current;
      if (finalParts.length) setMessages((m) => [...m, { role: 'agent', parts: finalParts }]);
      setStreaming(null);
      setBusy(false);
    }

    if (fullText) {
      api
        .postChatMessage(chatId, { role: 'agent', sender: agent?.name ?? 'agent', content: fullText })
        .catch(() => {});
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

  const showWorking = busy && !(streaming?.parts?.length);

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
          ) : messages.length === 0 && !streaming ? (
            <p className="text-[13px] text-tertiary">
              Session open. Say something to {agent?.name ?? 'the agent'}.
            </p>
          ) : (
            <>
              {messages.map((m, i) => (
                <Bubble key={i} msg={m} agentName={agent?.name} />
              ))}
              {streaming?.parts?.length ? <Bubble msg={streaming} agentName={agent?.name} /> : null}
            </>
          )}

          {approvals.length ? (
            <InlineApprovals approvals={approvals} onResolved={(rid) =>
              setApprovals((a) => a.filter((x) => x.request_id !== rid))
            } />
          ) : null}

          {showWorking ? (
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
    return <p className="text-center text-xs text-warning">{msg.content}</p>;
  }

  if (msg.role === 'human') {
    return (
      <div className="flex justify-end">
        <div className="max-w-[80%] space-y-1 text-right">
          <p className="text-[11px] text-muted">You</p>
          <div className="inline-block whitespace-pre-wrap rounded-lg bg-ink px-3.5 py-2 text-[13px] text-paper">
            {msg.content}
          </div>
        </div>
      </div>
    );
  }

  // Agent turn — render parts in arrival order: prose bubbles + tool chips.
  const parts = msg.parts ?? (msg.content ? [{ kind: 'text', text: msg.content } as TextPart] : []);
  return (
    <div className="flex justify-start">
      <div className="max-w-[85%] space-y-2">
        <p className="text-[11px] text-muted">{agentName ?? 'Agent'}</p>
        {parts.map((p, i) =>
          p.kind === 'tool' ? (
            <ToolCallChip key={`t${p.id}`} part={p} />
          ) : p.text.trim() ? (
            <div
              key={`x${i}`}
              className="inline-block rounded-lg border border-edge bg-surface px-3.5 py-2"
            >
              <Markdown>{p.text}</Markdown>
            </div>
          ) : null
        )}
      </div>
    </div>
  );
}

/** Compressed tool-call row. Collapsed: namespace · name · arg preview · status.
 *  Click to expand the full input and result payloads. */
function ToolCallChip({ part }: { part: ToolPart }) {
  const [open, setOpen] = useState(false);
  const { ns, label } = toolLabel(part.name);
  const summary = argSummary(part.input);
  const hasInput = !!part.input && Object.keys(part.input).length > 0;
  const hasDetail = hasInput || part.result !== undefined;

  return (
    <div className="rounded-lg border border-edge bg-surface">
      <button
        type="button"
        onClick={() => hasDetail && setOpen((o) => !o)}
        className={cn(
          'flex w-full items-center gap-2 px-2.5 py-1.5 text-left',
          hasDetail ? 'cursor-pointer' : 'cursor-default'
        )}
        aria-expanded={hasDetail ? open : undefined}
      >
        <CaretRight
          className={cn(
            'h-3 w-3 shrink-0 text-muted transition-transform',
            open && 'rotate-90',
            !hasDetail && 'invisible'
          )}
          aria-hidden
        />
        <Wrench className="h-3.5 w-3.5 shrink-0 text-tertiary" aria-hidden />
        {ns ? (
          <span className="shrink-0 rounded bg-inset px-1.5 py-0.5 font-mono text-[10px] text-tertiary">
            {ns}
          </span>
        ) : null}
        <span className="shrink-0 font-mono text-[12px] text-secondary">{label}</span>
        {summary ? (
          <span className="min-w-0 flex-1 truncate font-mono text-[11px] text-muted">{summary}</span>
        ) : (
          <span className="flex-1" />
        )}
        <ToolStatusIcon status={part.status} />
      </button>

      {open && hasDetail ? (
        <div className="space-y-2 border-t border-edge-subtle px-2.5 py-2">
          {hasInput ? (
            <CodeBlock label="Input" code={JSON.stringify(part.input, null, 2)} wrap maxHeight={200} />
          ) : null}
          {part.result !== undefined ? (
            <CodeBlock label="Result" code={prettyResult(part.result)} wrap maxHeight={280} />
          ) : null}
        </div>
      ) : null}
    </div>
  );
}

function ToolStatusIcon({ status }: { status: ToolStatus }) {
  if (status === 'running')
    return <CircleNotch className="h-3.5 w-3.5 shrink-0 animate-spin text-tertiary" aria-label="running" />;
  if (status === 'error')
    return <XCircle weight="fill" className="h-3.5 w-3.5 shrink-0 text-danger" aria-label="failed" />;
  return <CheckCircle weight="fill" className="h-3.5 w-3.5 shrink-0 text-success" aria-label="done" />;
}

function InlineApprovals({
  approvals,
  onResolved,
}: {
  approvals: HitlRequest[];
  onResolved: (requestId: string) => void;
}) {
  const [busy, setBusy] = useState<string | null>(null);
  const act = async (requestId: string, kind: 'approve' | 'reject') => {
    setBusy(requestId);
    try {
      if (kind === 'approve') await api.approve(requestId);
      else await api.reject(requestId, 'Rejected from chat');
      onResolved(requestId);
    } finally {
      setBusy(null);
    }
  };
  return (
    <div className="space-y-2">
      {approvals.map((p) => (
        <div key={p.request_id} className="rounded-md border border-warning/25 bg-warning-wash p-3">
          <p className="text-[13px] text-primary">{p.title || 'Approval needed'}</p>
          {p.description ? <p className="mt-1 text-xs text-secondary">{p.description}</p> : null}
          <div className="mt-2 flex gap-2">
            <Button size="sm" onClick={() => act(p.request_id, 'approve')} disabled={busy !== null}>
              Approve
            </Button>
            <Button
              size="sm"
              variant="destructive"
              onClick={() => act(p.request_id, 'reject')}
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

// ─── helpers ─────────────────────────────────────────────────────────────────

/** Split a tool id into a namespace tag + a readable label.
 *  `mcp__atlassian__jira_search` → { ns: 'atlassian', label: 'jira_search' }
 *  `company__request_approval`   → { ns: 'company',  label: 'request_approval' } */
function toolLabel(name: string): { ns?: string; label: string } {
  const segs = name.split('__').filter(Boolean);
  if (segs[0] === 'mcp' && segs.length >= 3) return { ns: segs[1], label: segs.slice(2).join('__') };
  if (segs.length >= 2) return { ns: segs[0], label: segs.slice(1).join('__') };
  return { label: name };
}

/** One-line preview of tool args for the collapsed chip. */
function argSummary(input?: Record<string, unknown>): string {
  if (!input) return '';
  const entries = Object.entries(input);
  if (entries.length === 0) return '';
  const preview = entries
    .map(([k, v]) => {
      const s = typeof v === 'string' ? v : JSON.stringify(v);
      return `${k}: ${s.length > 36 ? s.slice(0, 36) + '…' : s}`;
    })
    .join(', ');
  return preview.length > 90 ? preview.slice(0, 90) + '…' : preview;
}

function prettyResult(result: unknown): string {
  if (typeof result === 'string') return result;
  try {
    return JSON.stringify(result, null, 2);
  } catch {
    return String(result);
  }
}
