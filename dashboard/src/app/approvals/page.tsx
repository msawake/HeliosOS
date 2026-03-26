'use client';

import { useEffect, useState } from 'react';
import { Badge } from '@/components/Badge';

interface Approval {
  id: string;
  title: string;
  agent: string;
  category: string;
  status: string;
  created_at: string;
  sla_hours: number;
}

export default function ApprovalsPage() {
  const [items, setItems] = useState<Approval[]>([]);

  useEffect(() => {
    fetch('/api/approvals')
      .then((r) => (r.ok ? r.json() : []))
      .then(setItems)
      .catch(() => setItems([]));
  }, []);

  async function handleAction(id: string, action: 'approve' | 'deny') {
    await fetch(`/api/approvals/${id}/${action}`, { method: 'POST' });
    setItems((prev) => prev.filter((i) => i.id !== id));
  }

  return (
    <div>
      <h1 className="text-2xl font-bold mb-6">Approvals</h1>

      {items.length === 0 ? (
        <div className="card text-center py-12">
          <p className="text-gray-400 text-lg">No pending approvals</p>
          <p className="text-gray-400 text-sm mt-1">Human-in-the-loop items appear here</p>
        </div>
      ) : (
        <div className="space-y-3">
          {items.map((item) => (
            <div key={item.id} className="card flex items-center gap-4">
              <div className="flex-1">
                <div className="flex items-center gap-2">
                  <span className="font-semibold">{item.title}</span>
                  <Badge label={item.category} variant="event_driven" />
                </div>
                <p className="text-sm text-gray-500 mt-0.5">Agent: {item.agent} &middot; SLA: {item.sla_hours}h</p>
              </div>
              <div className="flex gap-2">
                <button
                  onClick={() => handleAction(item.id, 'approve')}
                  className="px-3 py-1.5 text-sm bg-green-100 text-green-800 rounded-lg hover:bg-green-200"
                >
                  Approve
                </button>
                <button
                  onClick={() => handleAction(item.id, 'deny')}
                  className="px-3 py-1.5 text-sm bg-red-100 text-red-800 rounded-lg hover:bg-red-200"
                >
                  Deny
                </button>
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
