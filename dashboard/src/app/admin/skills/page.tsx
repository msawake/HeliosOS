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

  function search() {
    if (!query.trim()) return;
    setLoading(true);
    setExpanded(null);
    api.searchSkills(query, domain || undefined)
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
      <h1 className="text-2xl font-bold text-white mb-2">Skills Library</h1>
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
        <button onClick={search} className="px-4 py-2 bg-sky-600 hover:bg-sky-500 text-white text-sm rounded-lg font-medium">Search</button>
      </div>

      {!query && domains.length > 0 && (
        <div className="grid grid-cols-2 lg:grid-cols-3 gap-3 mb-6">
          {domains.map((d) => (
            <button key={d.domain} onClick={() => { setDomain(d.domain); setQuery(d.domain); setTimeout(search, 0); }}
              className="bg-gray-900 border border-gray-800 rounded-xl p-4 text-left hover:border-sky-500 transition-colors">
              <p className="text-white font-medium">{d.domain}</p>
              <p className="text-gray-500 text-sm">{d.count} skills</p>
            </button>
          ))}
        </div>
      )}

      {loading ? <p className="text-gray-400">Searching...</p> : skills.length > 0 ? (
        <div className="space-y-2">
          {skills.map((s) => (
            <div key={s.name} className="bg-gray-900 border border-gray-800 rounded-xl">
              <button onClick={() => toggleExpand(s.name)} className="w-full text-left p-4">
                <div className="flex items-center gap-2 mb-1">
                  <span className="text-white font-medium">{s.name}</span>
                  <span className="text-xs px-2 py-0.5 rounded bg-sky-500/20 text-sky-400">{s.domain}</span>
                </div>
                <p className="text-gray-400 text-sm">{s.description}</p>
              </button>
              {expanded === s.name && (
                <div className="px-4 pb-4 border-t border-gray-800 pt-3">
                  <pre className="bg-gray-950 rounded-lg p-4 text-xs text-gray-300 overflow-auto max-h-96 whitespace-pre-wrap">{expandedContent}</pre>
                </div>
              )}
            </div>
          ))}
        </div>
      ) : query ? <p className="text-gray-500">No skills found for "{query}"</p> : null}
    </div>
  );
}
