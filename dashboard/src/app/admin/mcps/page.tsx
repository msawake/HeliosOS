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

  function search(q?: string, cat?: string) {
    const searchQuery = q ?? query;
    const searchCat = cat ?? category;
    if (!searchQuery.trim()) return;
    setLoading(true);
    setExpanded(null);
    api.searchMCPs(searchQuery, searchCat || undefined)
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
      <h1 className="text-2xl font-semibold text-[#0d0d0d] mb-2">MCP Registry</h1>
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
        <button onClick={() => search()} className="px-4 py-2 bg-[#10A37F] hover:bg-[#0d8c6d] text-white text-sm rounded-lg font-medium">Search</button>
      </div>

      {!query && categories.length > 0 && (
        <div className="grid grid-cols-2 lg:grid-cols-4 gap-3 mb-6">
          {categories.slice(0, 16).map((c) => (
            <button key={c.category} onClick={() => { setCategory(c.category); setQuery(c.category); search(c.category, c.category); }}
              className="bg-white border border-[#e5e5e5] rounded-xl p-3 text-left hover:border-[#10A37F] transition-colors">
              <p className="text-[#0d0d0d] font-medium text-sm">{c.category}</p>
              <p className="text-[#8e8ea0] text-xs">{c.count} packages</p>
            </button>
          ))}
        </div>
      )}

      {loading ? <p className="text-gray-400">Searching...</p> : packages.length > 0 ? (
        <div className="space-y-2">
          {packages.map((p) => (
            <div key={p.name} className="bg-white border border-[#e5e5e5] rounded-xl">
              <button onClick={() => toggleExpand(p.name)} className="w-full text-left p-4">
                <div className="flex items-center gap-2 mb-1">
                  <span className="text-[#0d0d0d] font-medium text-sm font-mono">{p.name}</span>
                  <span className="text-xs px-2 py-0.5 rounded bg-violet-50 text-violet-700">{p.category}</span>
                </div>
                <p className="text-[#6e6e80] text-sm">{p.description}</p>
              </button>
              {expanded === p.name && expandedConfig && (
                <div className="px-4 pb-4 border-t border-[#e5e5e5] pt-3">
                  <p className="text-xs text-gray-500 mb-2">Connection Config:</p>
                  <pre className="bg-[#f7f7f8] rounded-lg p-4 text-xs text-[#6e6e80] overflow-auto max-h-96">{JSON.stringify(expandedConfig, null, 2)}</pre>
                </div>
              )}
            </div>
          ))}
        </div>
      ) : query ? <p className="text-gray-500">No MCP packages found for &quot;{query}&quot;</p> : null}
    </div>
  );
}
