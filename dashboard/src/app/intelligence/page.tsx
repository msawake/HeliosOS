'use client';

import { useState, useRef, useEffect } from 'react';

interface Message {
  role: 'user' | 'assistant';
  content: string;
}

export default function IntelligencePage() {
  const [messages, setMessages] = useState<Message[]>([]);
  const [input, setInput] = useState('');
  const [loading, setLoading] = useState(false);
  const [sessionId] = useState(() => `intel-${Date.now()}`);
  const [schema, setSchema] = useState<any>(null);
  const bottomRef = useRef<HTMLDivElement>(null);

  useEffect(() => { bottomRef.current?.scrollIntoView({ behavior: 'smooth' }); }, [messages]);

  useEffect(() => {
    fetch('/api/intelligence/ontology/schema')
      .then((r) => r.ok ? r.json() : null)
      .then(setSchema)
      .catch(() => setSchema(null));
  }, []);

  async function send() {
    const msg = input.trim();
    if (!msg || loading) return;
    setInput('');
    setMessages((prev) => [...prev, { role: 'user', content: msg }]);
    setLoading(true);
    try {
      const res = await fetch('/api/intelligence/ask', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ question: msg, session_id: sessionId }),
      });
      const data = await res.json();
      setMessages((prev) => [...prev, { role: 'assistant', content: data.response || data.error || 'No response' }]);
    } catch (e: any) {
      setMessages((prev) => [...prev, { role: 'assistant', content: `Error: ${e.message}` }]);
    } finally {
      setLoading(false);
    }
  }

  return (
    <div className="flex flex-col h-[calc(100vh-6rem)]">
      <div className="mb-4">
        <h1 className="text-2xl font-bold text-white">Intelligence</h1>
        <p className="text-sm text-gray-400">
          Business intelligence analyst. Ask about leads, pipeline, revenue, trends, and company data.
        </p>
        {schema && (
          <div className="mt-2 flex gap-2 flex-wrap">
            {(schema.object_types || []).map((t: string) => (
              <span key={t} className="text-xs px-2 py-0.5 rounded bg-purple-500/20 text-purple-400">{t}</span>
            ))}
          </div>
        )}
      </div>

      <div className="flex-1 overflow-y-auto space-y-3 mb-4 pr-2">
        {messages.length === 0 && (
          <div className="text-center py-20">
            <p className="text-gray-500 mb-4">Ask the intelligence analyst about your business data.</p>
            <div className="flex flex-wrap gap-2 justify-center">
              {[
                'How many leads do we have?',
                'What is our pipeline value?',
                'Show revenue by department',
                'Which deals are at risk?',
                'What are the top performing agents?',
              ].map((q) => (
                <button key={q} onClick={() => { setInput(q); }}
                  className="px-3 py-1.5 bg-gray-800 border border-gray-700 rounded-lg text-sm text-gray-300 hover:bg-gray-700 hover:text-white">
                  {q}
                </button>
              ))}
            </div>
          </div>
        )}
        {messages.map((m, i) => (
          <div key={i} className={`flex ${m.role === 'user' ? 'justify-end' : 'justify-start'}`}>
            <div className={`max-w-[80%] px-4 py-3 rounded-2xl text-sm whitespace-pre-wrap ${
              m.role === 'user'
                ? 'bg-purple-600 text-white rounded-br-md'
                : 'bg-gray-800 text-gray-200 rounded-bl-md border border-gray-700'
            }`}>
              {m.content}
            </div>
          </div>
        ))}
        {loading && (
          <div className="flex justify-start">
            <div className="bg-gray-800 border border-gray-700 rounded-2xl rounded-bl-md px-4 py-3 text-sm text-gray-400">
              Analyzing...
            </div>
          </div>
        )}
        <div ref={bottomRef} />
      </div>

      <div className="flex gap-2">
        <input
          value={input}
          onChange={(e) => setInput(e.target.value)}
          onKeyDown={(e) => e.key === 'Enter' && send()}
          placeholder="Ask about your business data..."
          className="flex-1 px-4 py-3 bg-white text-gray-900 border border-gray-300 rounded-xl placeholder-gray-400 focus:outline-none focus:border-purple-500 focus:ring-1 focus:ring-purple-500"
          disabled={loading}
        />
        <button onClick={send} disabled={loading || !input.trim()}
          className="px-6 py-3 bg-purple-600 hover:bg-purple-500 disabled:bg-gray-700 text-white font-medium rounded-xl transition-colors">
          Ask
        </button>
      </div>
    </div>
  );
}
