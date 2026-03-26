'use client';

import { useEffect, useState } from 'react';
import { useParams, useRouter } from 'next/navigation';
import { Badge } from '@/components/Badge';
import { STACK_LABELS, EXEC_LABELS } from '@/lib/utils';
import type { AgentSummary } from '@/lib/api';

export default function AgentDetailPage() {
  const { id } = useParams<{ id: string }>();
  const router = useRouter();
  const [agent, setAgent] = useState<AgentSummary | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    fetch(`/api/platform/agents/${id}`)
      .then((r) => (r.ok ? r.json() : null))
      .then(setAgent)
      .catch(() => setAgent(null))
      .finally(() => setLoading(false));
  }, [id]);

  async function handleStop() {
    await fetch(`/api/platform/agents/${id}/stop`, { method: 'POST' });
    const r = await fetch(`/api/platform/agents/${id}`);
    if (r.ok) setAgent(await r.json());
  }

  async function handleDelete() {
    if (!confirm('Are you sure you want to undeploy this agent?')) return;
    await fetch(`/api/platform/agents/${id}`, { method: 'DELETE' });
    router.push('/agents');
  }

  if (loading) return <div className="text-gray-400">Loading...</div>;
  if (!agent) return <div className="text-gray-400">Agent not found</div>;

  return (
    <div>
      <button onClick={() => router.push('/agents')} className="text-sm text-gray-400 hover:text-gray-600 mb-4">
        &larr; Back to Agents
      </button>

      <div className="flex items-start justify-between mb-6">
        <div>
          <h1 className="text-2xl font-bold">{agent.name}</h1>
          <p className="text-gray-500 mt-1">{agent.description || 'No description'}</p>
          <p className="text-xs text-gray-400 font-mono mt-1">{agent.agent_id}</p>
        </div>
        <div className="flex gap-2">
          <button onClick={handleStop} className="px-3 py-1.5 text-sm bg-amber-100 text-amber-800 rounded-lg hover:bg-amber-200">
            Stop
          </button>
          <button onClick={handleDelete} className="px-3 py-1.5 text-sm bg-red-100 text-red-800 rounded-lg hover:bg-red-200">
            Undeploy
          </button>
        </div>
      </div>

      <div className="grid grid-cols-2 lg:grid-cols-4 gap-4 mb-8">
        <div className="card">
          <p className="text-xs text-gray-500 mb-1">Stack</p>
          <Badge label={STACK_LABELS[agent.stack] || agent.stack} variant={agent.stack} />
        </div>
        <div className="card">
          <p className="text-xs text-gray-500 mb-1">Execution Type</p>
          <Badge label={EXEC_LABELS[agent.execution_type] || agent.execution_type} variant={agent.execution_type} />
        </div>
        <div className="card">
          <p className="text-xs text-gray-500 mb-1">Ownership</p>
          <Badge label={agent.ownership} variant={agent.ownership} />
        </div>
        <div className="card">
          <p className="text-xs text-gray-500 mb-1">Status</p>
          <Badge label={agent.status} variant={agent.status} />
        </div>
      </div>

      <div className="card">
        <h2 className="font-semibold mb-3">Agent Configuration</h2>
        <pre className="bg-gray-50 rounded-lg p-4 text-xs overflow-auto">
          {JSON.stringify(agent, null, 2)}
        </pre>
      </div>
    </div>
  );
}
