'use client';

import { useEffect, useState } from 'react';
import { api } from '@/lib/api';

interface AuditEntry {
  timestamp?: string;
  action?: string;
  agent?: string;
  details?: string;
  [key: string]: unknown;
}

export default function AuditPage() {
  const [entries, setEntries] = useState<AuditEntry[]>([]);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    api.getAudit(200)
      .then(setEntries)
      .catch(() => setEntries([]))
      .finally(() => setLoading(false));
  }, []);

  return (
    <div>
      <h1 className="text-2xl font-semibold text-[#0d0d0d] mb-2">Audit Log</h1>
      <p className="text-sm text-gray-400 mb-6">Complete record of agent actions, approvals, and system events for compliance.</p>

      {loading ? <p className="text-gray-400">Loading audit log...</p> : entries.length > 0 ? (
        <div className="space-y-1">
          {entries.map((e, i) => (
            <div key={i} className="bg-white border border-[#e5e5e5] rounded-lg px-4 py-3 flex items-center gap-4 text-sm">
              <span className="text-[#8e8ea0] text-xs w-40 shrink-0">
                {e.timestamp ? new Date(e.timestamp).toLocaleString() : 'N/A'}
              </span>
              <span className="text-amber-700 font-medium w-32 shrink-0">{e.action || 'unknown'}</span>
              {e.agent && <span className="text-gray-400 w-36 shrink-0 truncate">{e.agent}</span>}
              <span className="text-gray-500 truncate">{e.details || JSON.stringify(e)}</span>
            </div>
          ))}
        </div>
      ) : (
        <p className="text-gray-500">No audit entries. Actions are logged as agents operate and humans approve/deny requests.</p>
      )}
    </div>
  );
}
