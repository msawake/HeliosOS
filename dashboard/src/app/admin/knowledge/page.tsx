'use client';

import { useEffect, useState } from 'react';
import { api, type KnowledgeEntry } from '@/lib/api';

export default function KnowledgePage() {
  const [entries, setEntries] = useState<KnowledgeEntry[]>([]);
  const [query, setQuery] = useState('');
  const [loading, setLoading] = useState(true);
  const [expanded, setExpanded] = useState<string | null>(null);

  function load(q?: string) {
    setLoading(true);
    api.searchKnowledge(q || undefined)
      .then(setEntries)
      .catch(() => setEntries([]))
      .finally(() => setLoading(false));
  }

  useEffect(() => { load(); }, []);

  return (
    <div>
      <h1 className="text-2xl font-bold text-white mb-2">Knowledge Base</h1>
      <p className="text-sm text-gray-400 mb-6">Organizational knowledge available to all agents for grounded responses.</p>

      <div className="flex gap-3 mb-6">
        <input placeholder="Search knowledge base..." value={query} onChange={(e) => setQuery(e.target.value)}
          onKeyDown={(e) => e.key === 'Enter' && load(query)}
          className="flex-1 px-3 py-2 bg-white text-gray-900 border border-gray-300 rounded-lg text-sm placeholder-gray-400" />
        <button onClick={() => load(query)} className="px-4 py-2 bg-emerald-600 hover:bg-emerald-500 text-white text-sm rounded-lg font-medium">
          Search
        </button>
      </div>

      {loading ? <p className="text-gray-400">Loading...</p> : entries.length > 0 ? (
        <div className="space-y-2">
          {entries.map((e) => (
            <div key={e.id} className="bg-gray-900 border border-gray-800 rounded-xl">
              <button onClick={() => setExpanded(expanded === e.id ? null : e.id)} className="w-full text-left p-4">
                <div className="flex items-center gap-2 mb-1">
                  <span className="text-white font-medium">{e.title}</span>
                  <span className="text-xs px-2 py-0.5 rounded bg-emerald-500/20 text-emerald-400">{e.category}</span>
                </div>
                {e.tags && e.tags.length > 0 && (
                  <div className="flex gap-1 mt-1">
                    {e.tags.map((t) => <span key={t} className="text-xs text-gray-500 bg-gray-800 px-1.5 py-0.5 rounded">{t}</span>)}
                  </div>
                )}
              </button>
              {expanded === e.id && (
                <div className="px-4 pb-4 border-t border-gray-800 pt-3">
                  <pre className="bg-gray-950 rounded-lg p-4 text-xs text-gray-300 overflow-auto max-h-96 whitespace-pre-wrap">{e.content}</pre>
                </div>
              )}
            </div>
          ))}
        </div>
      ) : (
        <p className="text-gray-500">No knowledge entries found. Knowledge is populated as agents learn and store information.</p>
      )}
    </div>
  );
}
