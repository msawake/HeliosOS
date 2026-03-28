'use client';

import { useEffect, useState } from 'react';
import { StatCard } from '@/components/StatCard';
import { Badge } from '@/components/Badge';
import { STACK_LABELS, EXEC_LABELS } from '@/lib/utils';
import type { PlatformOverview } from '@/lib/api';

const MOCK_OVERVIEW: PlatformOverview = {
  total: 0,
  by_stack: { forgeos: 0, crewai: 0, adk: 0, openclaw: 0 },
  by_execution_type: { always_on: 0, scheduled: 0, event_driven: 0, reflex: 0, autonomous: 0 },
  by_ownership: { personal: 0, shared: 0 },
  running: 0,
};

export default function OverviewPage() {
  const [data, setData] = useState<PlatformOverview>(MOCK_OVERVIEW);

  useEffect(() => {
    fetch('/api/platform/overview')
      .then((r) => (r.ok ? r.json() : MOCK_OVERVIEW))
      .then(setData)
      .catch(() => {});
  }, []);

  return (
    <div>
      <h1 className="text-2xl font-bold mb-6">Platform Overview</h1>

      <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-4 gap-4 mb-8">
        <StatCard title="Total Agents" value={data.total} color="brand" />
        <StatCard title="Running" value={data.running} color="green" />
        <StatCard title="Personal" value={data.by_ownership.personal ?? 0} color="violet" />
        <StatCard title="Shared" value={data.by_ownership.shared ?? 0} color="teal" />
      </div>

      <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
        <div className="card">
          <h2 className="text-lg font-semibold mb-4">Agents by Stack</h2>
          <div className="space-y-3">
            {Object.entries(data.by_stack).map(([stack, count]) => (
              <div key={stack} className="flex items-center justify-between">
                <div className="flex items-center gap-2">
                  <Badge label={STACK_LABELS[stack] || stack} variant={stack} />
                </div>
                <span className="text-xl font-bold text-gray-700">{count}</span>
              </div>
            ))}
          </div>
        </div>

        <div className="card">
          <h2 className="text-lg font-semibold mb-4">Agents by Execution Type</h2>
          <div className="space-y-3">
            {Object.entries(data.by_execution_type).map(([type, count]) => (
              <div key={type} className="flex items-center justify-between">
                <div className="flex items-center gap-2">
                  <Badge label={EXEC_LABELS[type] || type} variant={type} />
                </div>
                <span className="text-xl font-bold text-gray-700">{count}</span>
              </div>
            ))}
          </div>
        </div>
      </div>
    </div>
  );
}
