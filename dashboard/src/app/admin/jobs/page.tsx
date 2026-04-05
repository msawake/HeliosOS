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

  if (loading) return <p className="text-gray-400">Loading...</p>;

  return (
    <div>
      <h1 className="text-2xl font-bold text-white mb-6">Scheduler</h1>

      {jobs.length === 0 ? (
        <div className="bg-gray-900 border border-gray-800 rounded-xl p-8 text-center">
          <p className="text-gray-500">No scheduled jobs. Deploy agents with execution_type=SCHEDULED to see them here.</p>
        </div>
      ) : (
        <div className="bg-gray-900 border border-gray-800 rounded-xl overflow-hidden">
          <table className="w-full text-sm">
            <thead>
              <tr className="border-b border-gray-800 text-gray-400 text-xs uppercase">
                <th className="px-4 py-3 text-left">Agent</th>
                <th className="px-4 py-3 text-left">Cron</th>
                <th className="px-4 py-3 text-left">Interval</th>
                <th className="px-4 py-3 text-left">Last Run</th>
                <th className="px-4 py-3 text-left">Next Run</th>
                <th className="px-4 py-3 text-left">Status</th>
              </tr>
            </thead>
            <tbody>
              {jobs.map((j) => (
                <tr key={j.agent_id} className="border-b border-gray-800/50 hover:bg-gray-800/30">
                  <td className="px-4 py-3 text-white font-medium">{j.agent_id}</td>
                  <td className="px-4 py-3 text-gray-300 font-mono text-xs">{j.cron_expr}</td>
                  <td className="px-4 py-3 text-gray-400">{formatInterval(j.interval_seconds)}</td>
                  <td className="px-4 py-3 text-gray-400">{j.last_run ? new Date(j.last_run).toLocaleString() : 'Never'}</td>
                  <td className="px-4 py-3 text-gray-300">{new Date(j.next_run_at).toLocaleString()}</td>
                  <td className="px-4 py-3">
                    <span className={`text-xs px-2 py-0.5 rounded ${
                      j.active ? 'bg-green-500/20 text-green-400' : 'bg-gray-700 text-gray-400'
                    }`}>{j.active ? 'Active' : 'Stopped'}</span>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}

function formatInterval(seconds: number): string {
  if (seconds < 60) return `${seconds}s`;
  if (seconds < 3600) return `${Math.round(seconds / 60)}m`;
  if (seconds < 86400) return `${Math.round(seconds / 3600)}h`;
  return `${Math.round(seconds / 86400)}d`;
}
