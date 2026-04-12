'use client';

import { useState, useRef, useEffect } from 'react';
import { api } from '@/lib/api';

interface Message {
  role: 'user' | 'assistant';
  content: string;
}

export default function IntelligencePage() {
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
      const data = await api.intelligenceAsk(text, 'intelligence');
      setMessages([...updated, { role: 'assistant', content: data.response || 'No response' }]);
    } catch (e: any) {
      setMessages([...updated, { role: 'assistant', content: `Error: ${e.message || 'could not reach the intelligence backend.'}` }]);
    } finally {
      setLoading(false);
    }
  }

  return (
    <div className="flex flex-col h-[calc(100vh-4rem)]">
      <div className="mb-4">
        <h1 className="text-2xl font-semibold text-[#0d0d0d] mb-1">Intelligence</h1>
        <p className="text-sm text-gray-400">Query the organizational knowledge graph, ontology, and connected data sources.</p>
      </div>

      <div className="flex-1 overflow-y-auto space-y-3 mb-4 pr-2">
        {messages.length === 0 && (
          <div className="mt-8 text-center space-y-3">
            <p className="text-gray-500 text-sm">Ask questions about your organization, agents, and data...</p>
            <div className="flex flex-wrap gap-2 justify-center">
              {['What agents are running?', 'Show me sales metrics', 'Which departments have the most agents?'].map((q) => (
                <button key={q} onClick={() => { setInput(q); }}
                  className="text-xs px-3 py-1.5 bg-[#f7f7f8] text-gray-400 rounded-lg hover:bg-gray-100 transition-colors">
                  {q}
                </button>
              ))}
            </div>
          </div>
        )}
        {messages.map((m, i) => (
          <div key={i} className={`flex ${m.role === 'user' ? 'justify-end' : 'justify-start'}`}>
            <div className={`max-w-[70%] rounded-xl px-4 py-3 text-sm whitespace-pre-wrap ${
              m.role === 'user'
                ? 'bg-[#10A37F] text-white'
                : 'bg-[#f7f7f8] text-[#0d0d0d] border border-[#d1d1d1]'
            }`}>
              {m.content}
            </div>
          </div>
        ))}
        {loading && (
          <div className="flex justify-start">
            <div className="bg-[#f7f7f8] border border-[#d1d1d1] rounded-xl px-4 py-3 text-sm text-gray-400">
              Analyzing...
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
          placeholder="Ask about your organization..."
          className="flex-1 px-4 py-3 bg-white text-gray-900 border border-gray-300 rounded-lg text-sm placeholder-gray-400"
        />
        <button onClick={send} disabled={loading}
          className="px-6 py-3 bg-[#10A37F] hover:bg-[#0d8c6d] disabled:opacity-50 text-white text-sm rounded-lg font-medium">
          Ask
        </button>
      </div>
    </div>
  );
}
