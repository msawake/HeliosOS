'use client';

import { useEffect, useState, useRef } from 'react';
import { useParams } from 'next/navigation';
import Link from 'next/link';
import { api } from '@/lib/api';

interface ChatEvent {
  type: 'session' | 'text_delta' | 'tool_call' | 'tool_result' | 'hitl_request' | 'done' | 'error';
  content?: string;
  session_id?: string;
  name?: string;
  input?: Record<string, unknown>;
  result?: Record<string, unknown>;
  request_id?: string;
  title?: string;
  description?: string;
  risk?: string;
  tokens_used?: number;
  text?: string;
  error?: string;
}

interface ChatMessage {
  id: string;
  role: 'user' | 'assistant' | 'tool' | 'hitl';
  content: string;
  toolName?: string;
  toolInput?: Record<string, unknown>;
  toolResult?: Record<string, unknown>;
  hitlRequestId?: string;
  hitlTitle?: string;
  hitlDescription?: string;
  hitlRisk?: string;
  hitlStatus?: 'pending' | 'approved' | 'rejected';
}

interface Session {
  session_id: string;
  created_at: string;
  message_count: number;
  preview: string;
}

export default function AgentChatPage() {
  const params = useParams();
  const agentId = params.id as string;

  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [input, setInput] = useState('');
  const [loading, setLoading] = useState(false);
  const [sessionId, setSessionId] = useState<string | null>(null);
  const [sessions, setSessions] = useState<Session[]>([]);
  const [agentName, setAgentName] = useState('');
  const bottomRef = useRef<HTMLDivElement>(null);
  let msgCounter = useRef(0);

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: 'smooth' });
  }, [messages]);

  // Load agent name
  useEffect(() => {
    api.getAgent(agentId)
      .then((a: any) => setAgentName(a.name || agentId))
      .catch(() => {});
  }, [agentId]);

  // Load sessions list
  useEffect(() => {
    fetch(`/api/platform/agents/${agentId}/chat/sessions`)
      .then(r => r.ok ? r.json() : [])
      .then(setSessions)
      .catch(() => {});
  }, [agentId, sessionId]);

  function newId(): string {
    msgCounter.current += 1;
    return `msg-${Date.now()}-${msgCounter.current}`;
  }

  async function loadSession(sid: string) {
    try {
      const res = await fetch(`/api/platform/agents/${agentId}/chat/history?session_id=${sid}`);
      if (!res.ok) return;
      const data = await res.json();
      setSessionId(sid);
      setMessages(
        (data.messages || []).map((m: any, i: number) => ({
          id: `loaded-${i}`,
          role: m.role as 'user' | 'assistant',
          content: m.content || '',
        }))
      );
    } catch {}
  }

  function newConversation() {
    setSessionId(null);
    setMessages([]);
  }

  const [abortController, setAbortController] = useState<AbortController | null>(null);

  async function send() {
    const text = input.trim();
    if (!text) return;
    setInput('');

    const userMsg: ChatMessage = { id: newId(), role: 'user', content: text };
    setMessages(prev => [...prev, userMsg]);

    // Seed an empty assistant message we'll fill with streamed tokens
    const assistantId = newId();
    setMessages(prev => [...prev, { id: assistantId, role: 'assistant', content: '' }]);

    const controller = new AbortController();
    setAbortController(controller);


    try {
      const res = await fetch(`/api/platform/agents/${agentId}/chat/stream`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ message: text, session_id: sessionId }),
        signal: controller.signal,
      });

      if (!res.ok || !res.body) throw new Error(`HTTP ${res.status}`);

      const reader = res.body.getReader();
      const decoder = new TextDecoder();
      let buffer = '';

      while (true) {
        const { value, done } = await reader.read();
        if (done) break;
        buffer += decoder.decode(value, { stream: true });

        let sep = buffer.indexOf('\n\n');
        while (sep !== -1) {
          const frame = buffer.slice(0, sep);
          buffer = buffer.slice(sep + 2);

          if (frame.startsWith('data: ')) {
            try {
              const ev: ChatEvent = JSON.parse(frame.slice(6));
              handleEvent(ev, assistantId);
            } catch {}
          }
          sep = buffer.indexOf('\n\n');
        }
      }
    } catch (e: any) {
      if (e.name === 'AbortError') {
        setMessages(prev => {
          const copy = [...prev];
          const last = copy.find(m => m.id === assistantId);
          if (last) last.content += `\n\n[Generation stopped]`;
          return [...copy];
        });
      } else {
        setMessages(prev => {
          const copy = [...prev];
          const last = copy.find(m => m.id === assistantId);
          if (last) last.content = `Error: ${e.message}`;
          return [...copy];
        });
      }
    } finally {
      setAbortController(null);
    }
  }

  function stopGeneration() {
    if (abortController) {
      abortController.abort();
      setAbortController(null);
    }
  }

  function handleEvent(ev: ChatEvent, assistantId: string) {
    switch (ev.type) {
      case 'session':
        if (ev.session_id) setSessionId(ev.session_id);
        break;

      case 'text_delta':
        setMessages(prev => {
          const copy = [...prev];
          const msg = copy.find(m => m.id === assistantId);
          if (msg) msg.content += ev.content || '';
          return [...copy];
        });
        break;

      case 'tool_call':
        setMessages(prev => [...prev, {
          id: newId(),
          role: 'tool',
          content: `Calling ${ev.name}...`,
          toolName: ev.name,
          toolInput: ev.input as Record<string, unknown>,
        }]);
        break;

      case 'tool_result':
        setMessages(prev => {
          const copy = [...prev];
          // Find the last tool message with this name and update it
          for (let i = copy.length - 1; i >= 0; i--) {
            if (copy[i].role === 'tool' && copy[i].toolName === ev.name && !copy[i].toolResult) {
              copy[i].content = `${ev.name} completed`;
              copy[i].toolResult = ev.result as Record<string, unknown>;
              break;
            }
          }
          return [...copy];
        });
        break;

      case 'hitl_request':
        setMessages(prev => [...prev, {
          id: newId(),
          role: 'hitl',
          content: ev.title || 'Approval required',
          hitlRequestId: ev.request_id,
          hitlTitle: ev.title,
          hitlDescription: ev.description,
          hitlRisk: ev.risk,
          hitlStatus: 'pending',
        }]);
        break;

      case 'error':
        setMessages(prev => {
          const copy = [...prev];
          const msg = copy.find(m => m.id === assistantId);
          if (msg) msg.content += `\n\n[Error: ${ev.error}]`;
          return [...copy];
        });
        break;
    }
  }

  async function handleApproval(msgId: string, requestId: string, approve: boolean) {
    try {
      const endpoint = approve ? 'approve' : 'reject';
      await fetch(`/api/approvals/${requestId}/${endpoint}`, { method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ approved_by: 'chat-user', reason: '' }),
      });
      setMessages(prev => {
        const copy = [...prev];
        const msg = copy.find(m => m.id === msgId);
        if (msg) msg.hitlStatus = approve ? 'approved' : 'rejected';
        return [...copy];
      });
      // Auto-send follow-up so the agent knows the decision
      const action = approve ? 'approved' : 'rejected';
      setInput(`I ${action} the request: "${messages.find(m => m.id === msgId)?.hitlTitle}". Please proceed accordingly.`);
    } catch {}
  }

  return (
    <div className="flex h-[calc(100vh-4rem)]">
      {/* Session sidebar */}
      <div className="w-56 border-r border-[#e5e5e5] flex flex-col bg-white shrink-0">
        <div className="p-3 border-b border-[#e5e5e5]">
          <button onClick={newConversation}
            className="w-full px-3 py-2 text-sm font-medium bg-[#10A37F] text-white rounded-lg hover:bg-[#0d8c6d]">
            + New conversation
          </button>
        </div>
        <div className="flex-1 overflow-y-auto scrollbar-hide p-2 space-y-1">
          {sessions.map(s => (
            <button key={s.session_id} onClick={() => loadSession(s.session_id)}
              className={`w-full text-left px-3 py-2 rounded-lg text-xs transition-colors ${
                sessionId === s.session_id
                  ? 'bg-gray-100 text-[#0d0d0d] font-medium'
                  : 'text-[#6e6e80] hover:bg-gray-50'
              }`}>
              <p className="truncate">{s.preview || 'New conversation'}</p>
              <p className="text-[10px] text-[#8e8ea0] mt-0.5">{s.message_count} msgs</p>
            </button>
          ))}
        </div>
      </div>

      {/* Chat area */}
      <div className="flex-1 flex flex-col">
        {/* Header */}
        <div className="px-4 py-3 border-b border-[#e5e5e5] flex items-center gap-3 bg-white">
          <Link href={`/agents/${agentId}`} className="text-[#8e8ea0] hover:text-[#0d0d0d] text-sm">
            ← Back
          </Link>
          <span className="text-[#0d0d0d] font-medium text-sm">{agentName}</span>
          {sessionId && (
            <span className="text-[10px] text-[#8e8ea0] font-mono">{sessionId.slice(0, 8)}</span>
          )}
        </div>

        {/* Messages */}
        <div className="flex-1 overflow-y-auto p-4 space-y-3">
          {messages.length === 0 && (
            <div className="text-center py-16">
              <p className="text-[#8e8ea0] text-sm">Start a conversation with {agentName || 'this agent'}.</p>
              <p className="text-[#8e8ea0] text-xs mt-1">Messages stream in real-time. Tool calls and approvals appear inline.</p>
            </div>
          )}

          {messages.map(msg => (
            <div key={msg.id}>
              {msg.role === 'user' && (
                <div className="flex justify-end">
                  <div className="max-w-[70%] bg-[#10A37F] text-white rounded-xl px-4 py-3 text-sm whitespace-pre-wrap">
                    {msg.content}
                  </div>
                </div>
              )}

              {msg.role === 'assistant' && (
                <div className="flex justify-start">
                  <div className="max-w-[70%] bg-white border border-[#e5e5e5] rounded-xl px-4 py-3 text-sm text-[#0d0d0d] whitespace-pre-wrap">
                    {msg.content || (loading ? <span className="text-[#8e8ea0]">Thinking...</span> : '')}
                  </div>
                </div>
              )}

              {msg.role === 'tool' && (
                <div className="flex justify-start">
                  <div className="max-w-[80%] bg-[#f7f7f8] border border-[#e5e5e5] rounded-lg px-3 py-2 text-xs font-mono text-[#6e6e80]">
                    <span className="font-medium text-[#0d0d0d]">{msg.toolName}</span>
                    {msg.toolResult ? (
                      <span className="text-emerald-600 ml-2">completed</span>
                    ) : (
                      <span className="text-[#8e8ea0] ml-2">running...</span>
                    )}
                  </div>
                </div>
              )}

              {msg.role === 'hitl' && (
                <div className="flex justify-start">
                  <div className="max-w-[80%] bg-amber-50 border border-amber-200 rounded-xl p-4">
                    <div className="flex items-center gap-2 mb-2">
                      <span className="text-amber-700 font-medium text-sm">Approval Required</span>
                      <span className="text-xs px-2 py-0.5 rounded bg-amber-100 text-amber-600 border border-amber-200">
                        {msg.hitlRisk || 'medium'}
                      </span>
                    </div>
                    <p className="text-sm text-[#0d0d0d] font-medium">{msg.hitlTitle}</p>
                    {msg.hitlDescription && (
                      <p className="text-xs text-[#6e6e80] mt-1">{msg.hitlDescription}</p>
                    )}
                    {msg.hitlStatus === 'pending' && msg.hitlRequestId && (
                      <div className="flex gap-2 mt-3">
                        <button
                          onClick={() => handleApproval(msg.id, msg.hitlRequestId!, true)}
                          className="px-3 py-1.5 bg-[#10A37F] text-white text-xs rounded-lg hover:bg-[#0d8c6d] font-medium">
                          Approve
                        </button>
                        <button
                          onClick={() => handleApproval(msg.id, msg.hitlRequestId!, false)}
                          className="px-3 py-1.5 bg-white text-red-600 border border-red-200 text-xs rounded-lg hover:bg-red-50 font-medium">
                          Reject
                        </button>
                      </div>
                    )}
                    {msg.hitlStatus === 'approved' && (
                      <p className="text-xs text-emerald-700 mt-2 font-medium">Approved</p>
                    )}
                    {msg.hitlStatus === 'rejected' && (
                      <p className="text-xs text-red-600 mt-2 font-medium">Rejected</p>
                    )}
                  </div>
                </div>
              )}
            </div>
          ))}

          <div ref={bottomRef} />
        </div>

        {/* Input */}
        <div className="px-4 py-3 border-t border-[#e5e5e5] bg-white">
          <div className="flex gap-3">
            <input
              value={input}
              onChange={(e) => setInput(e.target.value)}
              onKeyDown={(e) => e.key === 'Enter' && !e.shiftKey && send()}
              placeholder={`Message ${agentName || 'agent'}...`}
              className="flex-1 px-4 py-3 border border-[#d1d1d1] rounded-xl text-sm text-[#0d0d0d] placeholder-[#8e8ea0] focus:border-[#10A37F] focus:ring-1 focus:ring-[#10A37F]/30"
            />
            {abortController ? (
              <button
                onClick={stopGeneration}
                className="px-5 py-3 bg-red-500 hover:bg-red-600 text-white text-sm rounded-xl font-medium transition-colors"
              >
                Stop
              </button>
            ) : (
              <button
                onClick={send}
                disabled={!input.trim()}
                className="px-5 py-3 bg-[#10A37F] hover:bg-[#0d8c6d] disabled:opacity-50 text-white text-sm rounded-xl font-medium transition-colors"
              >
                Send
              </button>
            )}
          </div>
        </div>
      </div>
    </div>
  );
}
