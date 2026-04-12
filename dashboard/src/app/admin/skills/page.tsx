'use client';

import { useEffect, useState } from 'react';
import { api } from '@/lib/api';

interface Domain { domain: string; count: number; }
interface Skill { name: string; description: string; domain: string; path?: string; content?: string; tags?: string[]; }

export default function SkillsPage() {
  const [domains, setDomains] = useState<Domain[]>([]);
  const [skills, setSkills] = useState<Skill[]>([]);
  const [query, setQuery] = useState('');
  const [domain, setDomain] = useState('');
  const [expanded, setExpanded] = useState<string | null>(null);
  const [expandedContent, setExpandedContent] = useState<string>('');
  const [total, setTotal] = useState(0);
  const [loading, setLoading] = useState(false);

  useEffect(() => {
    api.getSkillDomains().then((r) => {
      setDomains(r.domains || []);
      setTotal(r.total || 0);
    }).catch(() => {});
  }, []);

  function search(q?: string, d?: string) {
    const searchQuery = q ?? query;
    const searchDomain = d ?? domain;
    if (!searchQuery.trim()) return;
    setLoading(true);
    setExpanded(null);
    api.searchSkills(searchQuery, searchDomain || undefined)
      .then((r) => setSkills(r.skills || []))
      .catch(() => setSkills([]))
      .finally(() => setLoading(false));
  }

  async function toggleExpand(name: string) {
    if (expanded === name) { setExpanded(null); return; }
    try {
      const skill = await api.getSkill(name);
      setExpandedContent(skill.content || 'No content');
      setExpanded(name);
    } catch { setExpanded(null); }
  }

  return (
    <div>
      <h1 className="text-2xl font-semibold text-[#0d0d0d] mb-2">Skills Library</h1>
      <p className="text-sm text-gray-400 mb-6">{total} reusable skills across {domains.length} domains. Skills are domain expertise that agents use to ground their behavior.</p>

      <div className="flex gap-3 mb-6 flex-wrap">
        <input placeholder="Search skills..." value={query} onChange={(e) => setQuery(e.target.value)}
          onKeyDown={(e) => e.key === 'Enter' && search()}
          className="flex-1 min-w-[200px] px-3 py-2 bg-white text-gray-900 border border-gray-300 rounded-lg text-sm placeholder-gray-400" />
        <select value={domain} onChange={(e) => setDomain(e.target.value)}
          className="px-3 py-2 bg-white text-gray-900 border border-gray-300 rounded-lg text-sm">
          <option value="">All Domains</option>
          {domains.map((d) => <option key={d.domain} value={d.domain}>{d.domain} ({d.count})</option>)}
        </select>
        <button onClick={() => search()} className="px-4 py-2 bg-[#10A37F] hover:bg-[#0d8c6d] text-white text-sm rounded-lg font-medium">Search</button>
      </div>

      {!query && domains.length > 0 && (
        <div className="grid grid-cols-2 lg:grid-cols-3 gap-3 mb-6">
          {domains.map((d) => (
            <button key={d.domain} onClick={() => { setDomain(d.domain); setQuery(d.domain); search(d.domain, d.domain); }}
              className="bg-white border border-[#e5e5e5] rounded-xl p-4 text-left hover:border-[#10A37F] transition-colors">
              <p className="text-[#0d0d0d] font-medium">{d.domain}</p>
              <p className="text-[#8e8ea0] text-sm">{d.count} skills</p>
            </button>
          ))}
        </div>
      )}

      {loading ? <p className="text-gray-400">Searching...</p> : skills.length > 0 ? (
        <div className="space-y-2">
          {skills.map((s) => (
            <div key={s.name} className="bg-white border border-[#e5e5e5] rounded-xl">
              <button onClick={() => toggleExpand(s.name)} className="w-full text-left p-4">
                <div className="flex items-center gap-2 mb-1">
                  <span className="text-[#0d0d0d] font-medium">{s.name}</span>
                  <span className="text-xs px-2 py-0.5 rounded bg-cyan-50 text-cyan-700">{s.domain}</span>
                </div>
                <p className="text-[#6e6e80] text-sm">{s.description}</p>
              </button>
              {expanded === s.name && (
                <div className="px-4 pb-4 border-t border-[#e5e5e5] pt-3">
                  <pre className="bg-[#f7f7f8] rounded-lg p-4 text-xs text-[#6e6e80] overflow-auto max-h-96 whitespace-pre-wrap">{expandedContent}</pre>
                </div>
              )}
            </div>
          ))}
        </div>
      ) : query ? <p className="text-gray-500">No skills found for &quot;{query}&quot;</p> : null}
    </div>
  );
}
