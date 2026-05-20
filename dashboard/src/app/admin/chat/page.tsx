'use client';

import { useState, useRef, useEffect } from 'react';

interface Message {
  role: 'user' | 'assistant';
  content: string;
}

export default function AdminChatPage() {
  const [messages, setMessages] = useState<Message[]>([]);
  const [input, setInput] = useState('');
  const [loading, setLoading] = useState(false);
  const bottomRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: 'smooth' });
  }, [messages]);

  async function send() {
    const text = input.trim();
    if (!text || loading) return;
    setInput('');
    const updated = [...messages, { role: 'user' as const, content: text }];
    // Seed an empty assistant message we'll fill as tokens stream in
    setMessages([...updated, { role: 'assistant', content: '' }]);
    setLoading(true);

    try {
      const res = await fetch('/api/admin/chat/stream', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ message: text, session_id: 'admin' }),
      });
      if (!res.ok || !res.body) {
        throw new Error(`HTTP ${res.status}`);
      }
      const reader = res.body.getReader();
      const decoder = new TextDecoder('utf-8');
      let buffer = '';

      while (true) {
        const { value, done } = await reader.read();
        if (done) break;
        buffer += decoder.decode(value, { stream: true });

        // SSE frames are separated by double newlines
        let sep = buffer.indexOf('\n\n');
        while (sep !== -1) {
          const frame = buffer.slice(0, sep);
          buffer = buffer.slice(sep + 2);

          // Each frame starts with "data: "
          if (frame.startsWith('data: ')) {
            try {
              const payload = JSON.parse(frame.slice(6));
              if (payload.type === 'text_delta' && typeof payload.content === 'string') {
                setMessages((prev) => {
                  const copy = [...prev];
                  const last = copy[copy.length - 1];
                  if (last && last.role === 'assistant') {
                    copy[copy.length - 1] = { ...last, content: last.content + payload.content };
                  }
                  return copy;
                });
              } else if (payload.type === 'error') {
                setMessages((prev) => {
                  const copy = [...prev];
                  const last = copy[copy.length - 1];
                  if (last && last.role === 'assistant') {
                    copy[copy.length - 1] = {
                      ...last,
                      content: (last.content || '') + `\n\n[Error: ${payload.error || payload.content || 'unknown'}]`,
                    };
                  }
                  return copy;
                });
              }
              // "done" and "thinking" events are informational — ignore for UI
            } catch {
              // Malformed JSON frame — ignore
            }
          }
          sep = buffer.indexOf('\n\n');
        }
      }
    } catch (e: any) {
      setMessages((prev) => {
        const copy = [...prev];
        const last = copy[copy.length - 1];
        if (last && last.role === 'assistant' && !last.content) {
          copy[copy.length - 1] = {
            ...last,
            content: `Error: ${e.message || 'could not reach backend.'}`,
          };
        }
        return copy;
      });
    } finally {
      setLoading(false);
    }
  }

  return (
    <div className="flex flex-col h-[calc(100vh-4rem)]">
      <div className="mb-4">
        <h1 className="text-2xl font-semibold text-[#0d0d0d] mb-1">Admin Chat</h1>
        <p className="text-sm text-gray-400">Talk directly to the platform AI for diagnostics, queries, and commands.</p>
      </div>

      <div className="flex-1 overflow-y-auto space-y-3 mb-4 pr-2">
        {messages.length === 0 && (
          <p className="text-[#8e8ea0] text-sm mt-8 text-center">Start a conversation with the platform...</p>
        )}
        {messages.map((m, i) => (
          <div key={i} className={`flex ${m.role === 'user' ? 'justify-end' : 'justify-start'}`}>
            <div className={`max-w-[70%] rounded-xl px-4 py-3 text-sm whitespace-pre-wrap ${
              m.role === 'user'
                ? 'bg-[#10A37F] text-white'
                : 'bg-white text-[#0d0d0d] border border-[#d1d1d1]'
            }`}>
              {m.content}
            </div>
          </div>
        ))}
        {loading && (
          <div className="flex justify-start">
            <div className="bg-white border border-[#e5e5e5] rounded-xl px-4 py-3 text-sm text-gray-400">
              Thinking...
            </div>
          </div>
        )}
        <div ref={bottomRef} />
      </div>

      <div className="flex gap-3">
        <input
          value={input}
          onChange={(e) => setInput(e.target.value)}
          onKeyDown={(e) => e.key === 'Enter' && send()}
          placeholder="Ask the platform anything..."
          className="flex-1 px-4 py-3 bg-white text-gray-900 border border-gray-300 rounded-lg text-sm placeholder-gray-400"
        />
        <button onClick={send} disabled={loading}
          className="px-6 py-3 bg-[#10A37F] hover:bg-[#0d8c6d] disabled:opacity-50 text-white text-sm rounded-lg font-medium">
          Send
        </button>
      </div>
    </div>
  );
}
