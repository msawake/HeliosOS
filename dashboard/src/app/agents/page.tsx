'use client';

import { useEffect, useState } from 'react';
import Link from 'next/link';
import { Badge } from '@/components/Badge';
import { STACK_LABELS, EXEC_LABELS, STACKS, EXEC_TYPES } from '@/lib/utils';
import type { AgentSummary } from '@/lib/api';

export default function AgentsPage() {
  const [agents, setAgents] = useState<AgentSummary[]>([]);
  const [filterStack, setFilterStack] = useState('');
  const [filterType, setFilterType] = useState('');
  const [filterOwnership, setFilterOwnership] = useState('');

  useEffect(() => {
    const params = new URLSearchParams();
    if (filterStack) params.set('stack', filterStack);
    if (filterType) params.set('execution_type', filterType);
    if (filterOwnership) params.set('ownership', filterOwnership);
    const qs = params.toString() ? `?${params}` : '';
    fetch(`/api/platform/agents${qs}`)
      .then((r) => (r.ok ? r.json() : []))
      .then(setAgents)
      .catch(() => setAgents([]));
  }, [filterStack, filterType, filterOwnership]);

  return (
    <div>
      <div className="flex items-center justify-between mb-6">
        <h1 className="text-2xl font-bold">Agents</h1>
        <Link
          href="/agents/create"
          className="px-4 py-2 bg-brand-600 text-white rounded-lg text-sm font-medium hover:bg-brand-700 transition-colors"
        >
          + Create Agent
        </Link>
      </div>

      <div className="flex gap-3 mb-6">
        <select
          value={filterStack}
          onChange={(e) => setFilterStack(e.target.value)}
          className="rounded-lg border-gray-300 text-sm"
        >
          <option value="">All Stacks</option>
          {STACKS.map((s) => (
            <option key={s} value={s}>{STACK_LABELS[s]}</option>
          ))}
        </select>
        <select
          value={filterType}
          onChange={(e) => setFilterType(e.target.value)}
          className="rounded-lg border-gray-300 text-sm"
        >
          <option value="">All Types</option>
          {EXEC_TYPES.map((t) => (
            <option key={t} value={t}>{EXEC_LABELS[t]}</option>
          ))}
        </select>
        <select
          value={filterOwnership}
          onChange={(e) => setFilterOwnership(e.target.value)}
          className="rounded-lg border-gray-300 text-sm"
        >
          <option value="">All Ownership</option>
          <option value="personal">Personal</option>
          <option value="shared">Shared</option>
        </select>
      </div>

      {agents.length === 0 ? (
        <div className="card text-center py-12">
          <p className="text-gray-400 text-lg">No agents deployed yet</p>
          <p className="text-gray-400 text-sm mt-1">
            <Link href="/agents/create" className="text-brand-600 hover:underline">
              Create your first agent
            </Link>
          </p>
        </div>
      ) : (
        <div className="space-y-3">
          {agents.map((agent) => (
            <Link
              key={agent.agent_id}
              href={`/agents/${agent.agent_id}`}
              className="card flex items-center gap-4 hover:shadow-md transition-shadow cursor-pointer"
            >
              <div className="flex-1 min-w-0">
                <div className="flex items-center gap-2">
                  <span className="font-semibold text-gray-900 truncate">{agent.name}</span>
                  <Badge label={agent.status} variant={agent.status} />
                </div>
                <p className="text-sm text-gray-500 mt-0.5 truncate">
                  {agent.description || 'No description'}
                </p>
              </div>
              <div className="flex items-center gap-2 shrink-0">
                <Badge label={STACK_LABELS[agent.stack] || agent.stack} variant={agent.stack} />
                <Badge label={EXEC_LABELS[agent.execution_type] || agent.execution_type} variant={agent.execution_type} />
                <Badge label={agent.ownership} variant={agent.ownership} />
              </div>
              <span className="text-xs text-gray-400 shrink-0 font-mono">{agent.agent_id}</span>
            </Link>
          ))}
        </div>
      )}
    </div>
  );
}
