'use client';

import { useState } from 'react';
import { api, type KnowledgeEntry } from '@/lib/api';

const CATEGORIES = ['', 'policy', 'procedure', 'decision', 'faq', 'technical', 'runbook'];

export default function KnowledgePage() {
  const [query, setQuery] = useState('');
  const [category, setCategory] = useState('');
  const [results, setResults] = useState<KnowledgeEntry[]>([]);
  const [loading, setLoading] = useState(false);

  // Add form
  const [title, setTitle] = useState('');
  const [content, setContent] = useState('');
  const [addCat, setAddCat] = useState('faq');
  const [tags, setTags] = useState('');
  const [addMsg, setAddMsg] = useState('');

  function search() {
    if (!query.trim()) return;
    setLoading(true);
    api.searchKnowledge(query, category || undefined)
      .then(setResults).catch(() => setResults([])).finally(() => setLoading(false));
  }

  async function addEntry() {
    if (!title.trim() || !content.trim()) return;
    try {
      const res = await api.addKnowledge({
        category: addCat, title, content,
        tags: tags.split(',').map((t) => t.trim()).filter(Boolean),
      });
      setAddMsg(`Added: ${res.entry_id}`);
      setTitle(''); setContent(''); setTags('');
    } catch (e: any) {
      setAddMsg(`Error: ${e.message}`);
    }
  }

  return (
    <div>
      <h1 className="text-2xl font-bold text-white mb-6">Knowledge Base</h1>

      <div className="flex gap-3 mb-6">
        <input placeholder="Search knowledge..." value={query} onChange={(e) => setQuery(e.target.value)}
          onKeyDown={(e) => e.key === 'Enter' && search()}
          className="flex-1 px-3 py-2 bg-gray-800 border border-gray-700 rounded-lg text-sm text-white placeholder-gray-500" />
        <select value={category} onChange={(e) => setCategory(e.target.value)}
          className="px-3 py-2 bg-gray-800 border border-gray-700 rounded-lg text-sm text-white">
          <option value="">All Categories</option>
          {CATEGORIES.filter(Boolean).map((c) => <option key={c} value={c}>{c}</option>)}
        </select>
        <button onClick={search}
          className="px-4 py-2 bg-sky-600 hover:bg-sky-500 text-white text-sm rounded-lg font-medium">Search</button>
      </div>

      {loading ? <p className="text-gray-400">Searching...</p> : results.length > 0 ? (
        <div className="space-y-3 mb-8">
          {results.map((r, i) => (
            <div key={i} className="bg-gray-900 border border-gray-800 rounded-xl p-4">
              <div className="flex items-center gap-2 mb-2">
                <span className="text-xs px-2 py-0.5 rounded bg-sky-500/20 text-sky-400">{r.category}</span>
                {r.tags?.map((t) => <span key={t} className="text-xs px-2 py-0.5 rounded bg-gray-700 text-gray-400">{t}</span>)}
              </div>
              <h3 className="text-white font-medium">{r.title}</h3>
              <p className="text-gray-400 text-sm mt-1 line-clamp-3">{r.content}</p>
            </div>
          ))}
        </div>
      ) : query ? <p className="text-gray-500 mb-8">No results found.</p> : null}

      <div className="bg-gray-900 border border-gray-800 rounded-xl p-5">
        <h2 className="text-sm font-semibold text-gray-300 mb-4">Add Knowledge Entry</h2>
        <div className="space-y-3">
          <div className="flex gap-3">
            <input placeholder="Title" value={title} onChange={(e) => setTitle(e.target.value)}
              className="flex-1 px-3 py-2 bg-gray-800 border border-gray-700 rounded-lg text-sm text-white placeholder-gray-500" />
            <select value={addCat} onChange={(e) => setAddCat(e.target.value)}
              className="px-3 py-2 bg-gray-800 border border-gray-700 rounded-lg text-sm text-white">
              {CATEGORIES.filter(Boolean).map((c) => <option key={c} value={c}>{c}</option>)}
            </select>
          </div>
          <textarea placeholder="Content..." value={content} onChange={(e) => setContent(e.target.value)}
            rows={4} className="w-full px-3 py-2 bg-gray-800 border border-gray-700 rounded-lg text-sm text-white placeholder-gray-500" />
          <input placeholder="Tags (comma-separated)" value={tags} onChange={(e) => setTags(e.target.value)}
            className="w-full px-3 py-2 bg-gray-800 border border-gray-700 rounded-lg text-sm text-white placeholder-gray-500" />
          <div className="flex items-center gap-3">
            <button onClick={addEntry}
              className="px-4 py-2 bg-green-600 hover:bg-green-500 text-white text-sm rounded-lg font-medium">Add Entry</button>
            {addMsg && <span className="text-sm text-gray-400">{addMsg}</span>}
          </div>
        </div>
      </div>
    </div>
  );
}
