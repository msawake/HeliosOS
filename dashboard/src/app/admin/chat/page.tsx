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
    setMessages(updated);
    setLoading(true);
    try {
      const res = await fetch('http://localhost:5000/api/admin/chat', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ message: text, session_id: 'admin' }),
      });
      const data = await res.json();
      setMessages([...updated, { role: 'assistant', content: data.response || data.error || 'No response' }]);
    } catch {
      setMessages([...updated, { role: 'assistant', content: 'Error: could not reach backend.' }]);
    } finally {
      setLoading(false);
    }
  }

  return (
    <div className="flex flex-col h-[calc(100vh-4rem)]">
      <div className="mb-4">
        <h1 className="text-2xl font-bold text-white mb-1">Admin Chat</h1>
        <p className="text-sm text-gray-400">Talk directly to the platform AI for diagnostics, queries, and commands.</p>
      </div>

      <div className="flex-1 overflow-y-auto space-y-3 mb-4 pr-2">
        {messages.length === 0 && (
          <p className="text-gray-500 text-sm mt-8 text-center">Start a conversation with the platform...</p>
        )}
        {messages.map((m, i) => (
          <div key={i} className={`flex ${m.role === 'user' ? 'justify-end' : 'justify-start'}`}>
            <div className={`max-w-[70%] rounded-xl px-4 py-3 text-sm whitespace-pre-wrap ${
              m.role === 'user'
                ? 'bg-purple-600 text-white'
                : 'bg-gray-800 text-gray-200 border border-gray-700'
            }`}>
              {m.content}
            </div>
          </div>
        ))}
        {loading && (
          <div className="flex justify-start">
            <div className="bg-gray-800 border border-gray-700 rounded-xl px-4 py-3 text-sm text-gray-400">
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
          className="px-6 py-3 bg-purple-600 hover:bg-purple-500 disabled:opacity-50 text-white text-sm rounded-lg font-medium">
          Send
        </button>
      </div>
    </div>
  );
}
