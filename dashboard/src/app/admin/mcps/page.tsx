'use client';

import { useEffect, useState } from 'react';
import { api } from '@/lib/api';

interface Category { category: string; count: number; }
interface MCPPackage { name: string; category: string; description: string; path?: string; config?: any; }

export default function MCPsPage() {
  const [categories, setCategories] = useState<Category[]>([]);
  const [packages, setPackages] = useState<MCPPackage[]>([]);
  const [query, setQuery] = useState('');
  const [category, setCategory] = useState('');
  const [expanded, setExpanded] = useState<string | null>(null);
  const [expandedConfig, setExpandedConfig] = useState<any>(null);
  const [total, setTotal] = useState(0);
  const [loading, setLoading] = useState(false);

  useEffect(() => {
    api.getMCPCategories().then((r) => {
      setCategories(r.categories || []);
      setTotal(r.total || 0);
    }).catch(() => {});
  }, []);

  function search() {
    if (!query.trim()) return;
    setLoading(true);
    setExpanded(null);
    api.searchMCPs(query, category || undefined)
      .then((r) => setPackages(r.packages || []))
      .catch(() => setPackages([]))
      .finally(() => setLoading(false));
  }

  async function toggleExpand(name: string) {
    if (expanded === name) { setExpanded(null); return; }
    try {
      const pkg = await api.getMCPPackage(name);
      setExpandedConfig(pkg.config || null);
      setExpanded(name);
    } catch { setExpanded(null); }
  }

  return (
    <div>
      <h1 className="text-2xl font-bold text-white mb-2">MCP Registry</h1>
      <p className="text-sm text-gray-400 mb-6">{total.toLocaleString()} MCP server packages across {categories.length} categories. Connect external services to your agents.</p>

      <div className="flex gap-3 mb-6 flex-wrap">
        <input placeholder="Search MCPs (gmail, slack, stripe, postgres...)" value={query} onChange={(e) => setQuery(e.target.value)}
          onKeyDown={(e) => e.key === 'Enter' && search()}
          className="flex-1 min-w-[200px] px-3 py-2 bg-white text-gray-900 border border-gray-300 rounded-lg text-sm placeholder-gray-400" />
        <select value={category} onChange={(e) => setCategory(e.target.value)}
          className="px-3 py-2 bg-white text-gray-900 border border-gray-300 rounded-lg text-sm">
          <option value="">All Categories</option>
          {categories.map((c) => <option key={c.category} value={c.category}>{c.category} ({c.count})</option>)}
        </select>
        <button onClick={search} className="px-4 py-2 bg-purple-600 hover:bg-purple-500 text-white text-sm rounded-lg font-medium">Search</button>
      </div>

      {!query && categories.length > 0 && (
        <div className="grid grid-cols-2 lg:grid-cols-4 gap-3 mb-6">
          {categories.slice(0, 16).map((c) => (
            <button key={c.category} onClick={() => { setCategory(c.category); setQuery(c.category); setTimeout(search, 0); }}
              className="bg-gray-900 border border-gray-800 rounded-xl p-3 text-left hover:border-purple-500 transition-colors">
              <p className="text-white font-medium text-sm">{c.category}</p>
              <p className="text-gray-500 text-xs">{c.count} packages</p>
            </button>
          ))}
        </div>
      )}

      {loading ? <p className="text-gray-400">Searching...</p> : packages.length > 0 ? (
        <div className="space-y-2">
          {packages.map((p) => (
            <div key={p.name} className="bg-gray-900 border border-gray-800 rounded-xl">
              <button onClick={() => toggleExpand(p.name)} className="w-full text-left p-4">
                <div className="flex items-center gap-2 mb-1">
                  <span className="text-white font-medium text-sm font-mono">{p.name}</span>
                  <span className="text-xs px-2 py-0.5 rounded bg-purple-500/20 text-purple-400">{p.category}</span>
                </div>
                <p className="text-gray-400 text-sm">{p.description}</p>
              </button>
              {expanded === p.name && expandedConfig && (
                <div className="px-4 pb-4 border-t border-gray-800 pt-3">
                  <p className="text-xs text-gray-500 mb-2">Connection Config:</p>
                  <pre className="bg-gray-950 rounded-lg p-4 text-xs text-gray-300 overflow-auto max-h-96">{JSON.stringify(expandedConfig, null, 2)}</pre>
                </div>
              )}
            </div>
          ))}
        </div>
      ) : query ? <p className="text-gray-500">No MCP packages found for &quot;{query}&quot;</p> : null}
    </div>
  );
}
