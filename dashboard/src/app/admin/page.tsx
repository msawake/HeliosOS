'use client';

import { useEffect, useState } from 'react';
import { api } from '@/lib/api';

interface HealthData {
  agents: {
    total: number;
    by_stack: Record<string, number>;
    by_execution_type: Record<string, number>;
    by_ownership: Record<string, number>;
    running: number;
  };
  approvals: { pending: number };
  workflows: { active: number };
  metrics: Record<string, unknown>;
}

export default function AdminHealthPage() {
  const [health, setHealth] = useState<HealthData | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    api.getSystemHealth()
      .then((data: any) => setHealth(data))
      .catch(() => setHealth(null))
      .finally(() => setLoading(false));
  }, []);

  if (loading) return <p className="text-gray-400">Loading system health...</p>;

  if (!health) return <p className="text-gray-500">Unable to fetch system health. Is the backend running?</p>;

  const agents = health.agents || { total: 0, by_stack: {}, by_execution_type: {}, by_ownership: {}, running: 0 };

  return (
    <div>
      <h1 className="text-2xl font-semibold text-[#0d0d0d] mb-2">System Health</h1>
      <p className="text-sm text-gray-400 mb-6">Platform component status and diagnostics.</p>

      {/* Status Banner */}
      <div className="flex items-center gap-3 mb-6">
        <span className="w-3 h-3 rounded-full bg-green-500" />
        <span className="text-[#0d0d0d] font-medium text-lg">Online</span>
        <span className="text-[#8e8ea0] text-sm ml-2">{agents.running} agent{agents.running !== 1 ? 's' : ''} running</span>
      </div>

      {/* Top-level stats */}
      <div className="grid grid-cols-2 md:grid-cols-4 gap-4 mb-8">
        <StatCard label="Total Agents" value={agents.total} />
        <StatCard label="Running" value={agents.running} color="green" />
        <StatCard label="Pending Approvals" value={health.approvals?.pending ?? 0} color="amber" />
        <StatCard label="Active Workflows" value={health.workflows?.active ?? 0} />
      </div>

      {/* By Stack */}
      {Object.keys(agents.by_stack).length > 0 && (
        <div className="mb-6">
          <h2 className="text-sm font-medium text-gray-400 uppercase tracking-wide mb-3">Agents by Stack</h2>
          <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
            {Object.entries(agents.by_stack).map(([stack, count]) => (
              <div key={stack} className="bg-white border border-[#e5e5e5] rounded-xl p-4">
                <p className="text-[#8e8ea0] text-xs uppercase">{stack}</p>
                <p className="text-[#0d0d0d] font-bold text-xl mt-1">{count}</p>
              </div>
            ))}
          </div>
        </div>
      )}

      {/* By Execution Type */}
      {Object.keys(agents.by_execution_type).length > 0 && (
        <div className="mb-6">
          <h2 className="text-sm font-medium text-gray-400 uppercase tracking-wide mb-3">Agents by Execution Type</h2>
          <div className="grid grid-cols-2 md:grid-cols-5 gap-3">
            {Object.entries(agents.by_execution_type).map(([type, count]) => (
              <div key={type} className="bg-white border border-[#e5e5e5] rounded-xl p-4">
                <p className="text-[#8e8ea0] text-xs">{type.replace(/_/g, ' ')}</p>
                <p className="text-[#0d0d0d] font-bold text-xl mt-1">{count}</p>
              </div>
            ))}
          </div>
        </div>
      )}

      {/* By Ownership */}
      {Object.keys(agents.by_ownership).length > 0 && (
        <div className="mb-6">
          <h2 className="text-sm font-medium text-gray-400 uppercase tracking-wide mb-3">Agents by Ownership</h2>
          <div className="grid grid-cols-2 md:grid-cols-3 gap-3">
            {Object.entries(agents.by_ownership).map(([type, count]) => (
              <div key={type} className="bg-white border border-[#e5e5e5] rounded-xl p-4">
                <p className="text-[#8e8ea0] text-xs uppercase">{type}</p>
                <p className="text-[#0d0d0d] font-bold text-xl mt-1">{count}</p>
              </div>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}

function StatCard({ label, value, color }: { label: string; value: number; color?: string }) {
  const valueColor = color === 'green' ? 'text-emerald-700' : color === 'amber' ? 'text-amber-700' : 'text-[#0d0d0d]';
  return (
    <div className="bg-white border border-[#e5e5e5] rounded-xl p-5">
      <p className="text-[#8e8ea0] text-xs uppercase tracking-wide">{label}</p>
      <p className={`font-bold text-2xl mt-1 ${valueColor}`}>{value}</p>
    </div>
  );
}
