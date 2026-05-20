'use client';

import { useEffect, useState } from 'react';
import { api, type ScheduledJob } from '@/lib/api';

export default function SchedulerPage() {
  const [jobs, setJobs] = useState<ScheduledJob[]>([]);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    api.getScheduledJobs()
      .then(setJobs)
      .catch(() => setJobs([]))
      .finally(() => setLoading(false));
  }, []);

  return (
    <div>
      <h1 className="text-2xl font-semibold text-[#0d0d0d] mb-2">Scheduler</h1>
      <p className="text-sm text-gray-400 mb-6">Cron-scheduled agent jobs. These agents run automatically on their configured schedule.</p>

      {loading ? <p className="text-gray-400">Loading jobs...</p> : jobs.length > 0 ? (
        <div className="space-y-2">
          {jobs.map((j) => (
            <div key={j.agent_id} className="bg-white border border-[#e5e5e5] rounded-xl p-4">
              <div className="flex items-center gap-3 mb-2">
                <span className="text-[#0d0d0d] font-medium">{j.name || j.agent_id}</span>
                <span className={`text-xs px-2 py-0.5 rounded ${
                  j.status === 'active' ? 'bg-emerald-50 text-emerald-700 border border-emerald-200' : 'bg-gray-100 text-gray-500 border border-gray-200'
                }`}>{j.status}</span>
              </div>
              <div className="grid grid-cols-3 gap-4 text-xs text-gray-500">
                <div>
                  <p className="text-gray-600 mb-0.5">Schedule</p>
                  <p className="text-gray-300 font-mono">{j.schedule}</p>
                </div>
                <div>
                  <p className="text-gray-600 mb-0.5">Next Run</p>
                  <p className="text-[#6e6e80]">{j.next_run ? new Date(j.next_run).toLocaleString() : 'N/A'}</p>
                </div>
                <div>
                  <p className="text-gray-600 mb-0.5">Last Run</p>
                  <p className="text-[#6e6e80]">{j.last_run ? new Date(j.last_run).toLocaleString() : 'Never'}</p>
                </div>
              </div>
            </div>
          ))}
        </div>
      ) : (
        <p className="text-gray-500">No scheduled jobs. Deploy agents with &quot;scheduled&quot; execution type to see them here.</p>
      )}
    </div>
  );
}
