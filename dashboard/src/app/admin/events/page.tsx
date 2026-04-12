'use client';

import { useEffect, useState } from 'react';
import { api, type EventEntry } from '@/lib/api';

export default function EventsPage() {
  const [events, setEvents] = useState<EventEntry[]>([]);
  const [loading, setLoading] = useState(true);
  const [department, setDepartment] = useState('');
  const [status, setStatus] = useState('');

  function load() {
    setLoading(true);
    const params: Record<string, string> = {};
    if (department) params.department = department;
    if (status) params.status = status;
    api.getEvents(Object.keys(params).length ? params : undefined)
      .then(setEvents)
      .catch(() => setEvents([]))
      .finally(() => setLoading(false));
  }

  useEffect(() => { load(); }, []);

  return (
    <div>
      <h1 className="text-2xl font-semibold text-[#0d0d0d] mb-2">Events</h1>
      <p className="text-sm text-gray-400 mb-6">Platform event bus activity. Filter by department or status.</p>

      <div className="flex gap-3 mb-6 flex-wrap">
        <input placeholder="Filter by department..." value={department} onChange={(e) => setDepartment(e.target.value)}
          className="px-3 py-2 bg-white text-gray-900 border border-gray-300 rounded-lg text-sm placeholder-gray-400 w-48" />
        <select value={status} onChange={(e) => setStatus(e.target.value)}
          className="px-3 py-2 bg-white text-gray-900 border border-gray-300 rounded-lg text-sm">
          <option value="">All Statuses</option>
          <option value="pending">Pending</option>
          <option value="processed">Processed</option>
          <option value="failed">Failed</option>
        </select>
        <button onClick={load} className="px-4 py-2 bg-[#10A37F] hover:bg-[#0d8c6d] text-white text-sm rounded-lg font-medium">
          Filter
        </button>
      </div>

      {loading ? <p className="text-gray-400">Loading events...</p> : events.length > 0 ? (
        <div className="space-y-2">
          {events.map((e, i) => (
            <div key={e.id || i} className="bg-white border border-[#e5e5e5] rounded-xl p-4">
              <div className="flex items-center gap-2 mb-1">
                <span className="text-[#0d0d0d] font-medium text-sm">{e.name}</span>
                <span className="text-xs px-2 py-0.5 rounded bg-amber-50 text-amber-700 border border-amber-200">{e.status}</span>
                {e.priority && <span className="text-xs px-2 py-0.5 rounded bg-red-50 text-red-700 border border-red-200">{e.priority}</span>}
              </div>
              <div className="flex gap-4 text-xs text-gray-500 mt-1">
                <span>Source: {e.source}</span>
                {e.target_department && <span>Dept: {e.target_department}</span>}
                {e.timestamp && <span>{new Date(e.timestamp).toLocaleString()}</span>}
              </div>
            </div>
          ))}
        </div>
      ) : (
        <p className="text-gray-500">No events found. Events appear as agents communicate and process tasks.</p>
      )}
    </div>
  );
}
