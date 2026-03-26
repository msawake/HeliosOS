'use client';

import { useEffect, useState } from 'react';
import { Badge } from '@/components/Badge';

interface Workflow {
  id: string;
  name: string;
  type: string;
  progress: number;
  priority: string;
  created_at: string;
}

export default function WorkflowsPage() {
  const [workflows, setWorkflows] = useState<Workflow[]>([]);

  useEffect(() => {
    fetch('/api/workflows')
      .then((r) => (r.ok ? r.json() : []))
      .then(setWorkflows)
      .catch(() => setWorkflows([]));
  }, []);

  return (
    <div>
      <h1 className="text-2xl font-bold mb-6">Workflows</h1>

      {workflows.length === 0 ? (
        <div className="card text-center py-12">
          <p className="text-gray-400 text-lg">No active workflows</p>
          <p className="text-gray-400 text-sm mt-1">Workflows are created when agents execute multi-step tasks</p>
        </div>
      ) : (
        <div className="space-y-3">
          {workflows.map((wf) => (
            <div key={wf.id} className="card flex items-center gap-4">
              <div className="flex-1">
                <div className="flex items-center gap-2">
                  <span className="font-semibold">{wf.name}</span>
                  <Badge label={wf.type} variant="running" />
                  <Badge label={wf.priority} variant="scheduled" />
                </div>
                <p className="text-xs text-gray-400 font-mono mt-0.5">{wf.id}</p>
              </div>
              <div className="w-32">
                <div className="flex items-center gap-2">
                  <div className="flex-1 bg-gray-200 rounded-full h-2">
                    <div className="bg-brand-500 h-2 rounded-full" style={{ width: `${wf.progress}%` }} />
                  </div>
                  <span className="text-xs text-gray-500">{wf.progress}%</span>
                </div>
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
