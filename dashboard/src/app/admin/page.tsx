'use client';

import { useEffect, useState } from 'react';
import { api, type SystemHealth } from '@/lib/api';

export default function AdminHealthPage() {
  const [health, setHealth] = useState<SystemHealth | null>(null);
  const [metrics, setMetrics] = useState<Record<string, number>>({});
  const [error, setError] = useState('');

  useEffect(() => {
    api.getSystemHealth().then(setHealth).catch((e) => setError(e.message));
    api.getMetrics().then((r) => setMetrics(r.dashboard || {})).catch(() => {});
  }, []);

  if (error) return <p className="text-red-400">{error}</p>;
  if (!health) return <p className="text-gray-400">Loading...</p>;

  const stats = [
    { label: 'Total Agents', value: health.agents?.total ?? 0, color: 'text-sky-400' },
    { label: 'Running', value: health.agents?.running ?? 0, color: 'text-green-400' },
    { label: 'Pending Approvals', value: health.approvals?.pending ?? 0, color: 'text-amber-400' },
    { label: 'Active Workflows', value: health.workflows?.active ?? 0, color: 'text-purple-400' },
  ];

  return (
    <div>
      <h1 className="text-2xl font-bold text-white mb-6">System Health</h1>

      <div className="grid grid-cols-2 lg:grid-cols-4 gap-4 mb-8">
        {stats.map((s) => (
          <div key={s.label} className="bg-gray-900 border border-gray-800 rounded-xl p-5">
            <p className={`text-3xl font-bold ${s.color}`}>{s.value}</p>
            <p className="text-sm text-gray-400 mt-1">{s.label}</p>
          </div>
        ))}
      </div>

      {health.agents?.by_stack && (
        <div className="bg-gray-900 border border-gray-800 rounded-xl p-5 mb-6">
          <h2 className="text-sm font-semibold text-gray-300 mb-3">Agents by Stack</h2>
          <div className="flex gap-6">
            {Object.entries(health.agents.by_stack).map(([stack, count]) => (
              <div key={stack} className="text-center">
                <p className="text-xl font-bold text-white">{count}</p>
                <p className="text-xs text-gray-500 capitalize">{stack}</p>
              </div>
            ))}
          </div>
        </div>
      )}

      <div className="bg-gray-900 border border-gray-800 rounded-xl p-5">
        <h2 className="text-sm font-semibold text-gray-300 mb-3">Metrics Dashboard</h2>
        {Object.keys(metrics).length === 0 ? (
          <p className="text-gray-500 text-sm">No metrics recorded yet. Start the main loop to generate metrics.</p>
        ) : (
          <div className="grid grid-cols-2 lg:grid-cols-4 gap-3">
            {Object.entries(metrics).map(([name, value]) => (
              <div key={name} className="bg-gray-800 rounded-lg p-3">
                <p className="text-lg font-semibold text-white">{typeof value === 'number' ? value.toFixed(1) : value}</p>
                <p className="text-xs text-gray-500">{name.replace(/_/g, ' ')}</p>
              </div>
            ))}
          </div>
        )}
      </div>
    </div>
  );
}
