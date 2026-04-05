'use client';

import { useEffect, useState } from 'react';
import { api, type EventEntry } from '@/lib/api';

const PRIORITIES = ['', 'P0_CRITICAL', 'P1_HIGH', 'P2_MEDIUM', 'P3_LOW'];
const STATUSES = ['', 'PENDING', 'IN_PROGRESS', 'RESOLVED', 'EXPIRED'];

export default function EventsPage() {
  const [events, setEvents] = useState<EventEntry[]>([]);
  const [dept, setDept] = useState('');
  const [priority, setPriority] = useState('');
  const [status, setStatus] = useState('');
  const [loading, setLoading] = useState(false);

  function load() {
    setLoading(true);
    const params: Record<string, string> = {};
    if (dept) params.department = dept;
    if (priority) params.priority = priority;
    if (status) params.status = status;
    api.getEvents(params).then(setEvents).catch(() => setEvents([])).finally(() => setLoading(false));
  }

  useEffect(() => { load(); }, []);

  return (
    <div>
      <h1 className="text-2xl font-bold text-white mb-6">Event Bus Explorer</h1>

      <div className="flex gap-3 mb-6 flex-wrap">
        <input
          placeholder="Department..."
          value={dept} onChange={(e) => setDept(e.target.value)}
          className="px-3 py-2 bg-gray-800 border border-gray-700 rounded-lg text-sm text-white placeholder-gray-500 w-40"
        />
        <select value={priority} onChange={(e) => setPriority(e.target.value)}
          className="px-3 py-2 bg-gray-800 border border-gray-700 rounded-lg text-sm text-white">
          <option value="">All Priorities</option>
          {PRIORITIES.filter(Boolean).map((p) => <option key={p} value={p}>{p}</option>)}
        </select>
        <select value={status} onChange={(e) => setStatus(e.target.value)}
          className="px-3 py-2 bg-gray-800 border border-gray-700 rounded-lg text-sm text-white">
          <option value="">All Statuses</option>
          {STATUSES.filter(Boolean).map((s) => <option key={s} value={s}>{s}</option>)}
        </select>
        <button onClick={load}
          className="px-4 py-2 bg-sky-600 hover:bg-sky-500 text-white text-sm rounded-lg font-medium">
          Search
        </button>
      </div>

      {loading ? (
        <p className="text-gray-400">Loading...</p>
      ) : events.length === 0 ? (
        <p className="text-gray-500">No events found. Events are generated when agents communicate across departments.</p>
      ) : (
        <div className="bg-gray-900 border border-gray-800 rounded-xl overflow-hidden">
          <table className="w-full text-sm">
            <thead>
              <tr className="border-b border-gray-800 text-gray-400 text-xs uppercase">
                <th className="px-4 py-3 text-left">Time</th>
                <th className="px-4 py-3 text-left">Source</th>
                <th className="px-4 py-3 text-left">Department</th>
                <th className="px-4 py-3 text-left">Category</th>
                <th className="px-4 py-3 text-left">Priority</th>
                <th className="px-4 py-3 text-left">Status</th>
              </tr>
            </thead>
            <tbody>
              {events.map((e, i) => (
                <tr key={i} className="border-b border-gray-800/50 hover:bg-gray-800/30">
                  <td className="px-4 py-3 text-gray-400">{e.timestamp || '-'}</td>
                  <td className="px-4 py-3 text-white">{e.source_agent || '-'}</td>
                  <td className="px-4 py-3 text-gray-300">{e.target_department || '-'}</td>
                  <td className="px-4 py-3 text-gray-300">{e.category || '-'}</td>
                  <td className="px-4 py-3">
                    <span className={`text-xs px-2 py-0.5 rounded ${
                      e.priority?.includes('CRITICAL') ? 'bg-red-500/20 text-red-400' :
                      e.priority?.includes('HIGH') ? 'bg-amber-500/20 text-amber-400' :
                      'bg-gray-700 text-gray-400'
                    }`}>{e.priority || '-'}</span>
                  </td>
                  <td className="px-4 py-3">
                    <span className={`text-xs px-2 py-0.5 rounded ${
                      e.status === 'RESOLVED' ? 'bg-green-500/20 text-green-400' :
                      e.status === 'PENDING' ? 'bg-amber-500/20 text-amber-400' :
                      'bg-gray-700 text-gray-400'
                    }`}>{e.status || '-'}</span>
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
