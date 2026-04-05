'use client';

import { useEffect, useState } from 'react';
import { api, type SystemHealth } from '@/lib/api';

export default function AdminHealthPage() {
  const [health, setHealth] = useState<SystemHealth | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    api.getSystemHealth()
      .then(setHealth)
      .catch(() => setHealth(null))
      .finally(() => setLoading(false));
  }, []);

  if (loading) return <p className="text-gray-400">Loading system health...</p>;

  return (
    <div>
      <h1 className="text-2xl font-bold text-white mb-2">System Health</h1>
      <p className="text-sm text-gray-400 mb-6">Platform component status and diagnostics.</p>

      {health ? (
        <div className="space-y-4">
          <div className="flex items-center gap-3 mb-6">
            <span className={`w-3 h-3 rounded-full ${health.status === 'ok' ? 'bg-green-500' : 'bg-red-500'}`} />
            <span className="text-white font-medium text-lg capitalize">{health.status}</span>
          </div>

          <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">
            {Object.entries(health.components).map(([key, value]) => (
              <div key={key} className="bg-gray-900 border border-gray-800 rounded-xl p-4">
                <p className="text-gray-400 text-xs uppercase tracking-wide mb-2">{key.replace(/_/g, ' ')}</p>
                <p className="text-white font-medium text-sm">
                  {typeof value === 'boolean' ? (
                    <span className={value ? 'text-green-400' : 'text-red-400'}>{value ? 'Connected' : 'Disconnected'}</span>
                  ) : typeof value === 'number' ? (
                    value.toLocaleString()
                  ) : Array.isArray(value) ? (
                    value.join(', ') || 'None'
                  ) : (
                    String(value)
                  )}
                </p>
              </div>
            ))}
          </div>
        </div>
      ) : (
        <p className="text-gray-500">Unable to fetch system health. Is the backend running?</p>
      )}
    </div>
  );
}
