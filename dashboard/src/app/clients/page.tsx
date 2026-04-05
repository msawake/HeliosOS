'use client';

import { useEffect, useState } from 'react';
import Link from 'next/link';
import { api, type ClientSummary } from '@/lib/api';

export default function ClientsPage() {
  const [clients, setClients] = useState<ClientSummary[]>([]);
  const [loading, setLoading] = useState(true);
  const [showCreate, setShowCreate] = useState(false);
  const [newId, setNewId] = useState('');
  const [newName, setNewName] = useState('');

  function load() {
    setLoading(true);
    api.getClients()
      .then(setClients)
      .catch(() => setClients([]))
      .finally(() => setLoading(false));
  }

  useEffect(() => { load(); }, []);

  async function create() {
    if (!newId.trim() || !newName.trim()) return;
    try {
      await api.createClient(newId.trim(), newName.trim());
      setNewId('');
      setNewName('');
      setShowCreate(false);
      load();
    } catch (e: any) {
      alert(e.message);
    }
  }

  return (
    <div>
      <div className="flex items-center justify-between mb-6">
        <div>
          <h1 className="text-2xl font-bold text-white mb-1">Clients</h1>
          <p className="text-sm text-gray-400">Deploy client-scoped agents with isolated MCP connections and credentials.</p>
        </div>
        <button onClick={() => setShowCreate(!showCreate)}
          className="px-4 py-2 bg-teal-600 hover:bg-teal-500 text-white text-sm rounded-lg font-medium">
          + New Client
        </button>
      </div>

      {showCreate && (
        <div className="bg-gray-900 border border-gray-800 rounded-xl p-4 mb-6 flex gap-3 items-end">
          <div className="flex-1">
            <label className="text-xs text-gray-400 block mb-1">Client ID</label>
            <input value={newId} onChange={(e) => setNewId(e.target.value)} placeholder="acme-corp"
              className="w-full px-3 py-2 bg-white text-gray-900 border border-gray-300 rounded-lg text-sm" />
          </div>
          <div className="flex-1">
            <label className="text-xs text-gray-400 block mb-1">Client Name</label>
            <input value={newName} onChange={(e) => setNewName(e.target.value)} placeholder="Acme Corporation"
              className="w-full px-3 py-2 bg-white text-gray-900 border border-gray-300 rounded-lg text-sm" />
          </div>
          <button onClick={create} className="px-4 py-2 bg-teal-600 hover:bg-teal-500 text-white text-sm rounded-lg font-medium">
            Create
          </button>
        </div>
      )}

      {loading ? <p className="text-gray-400">Loading clients...</p> : clients.length > 0 ? (
        <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">
          {clients.map((c) => (
            <Link key={c.id} href={`/clients/${c.id}`}
              className="bg-gray-900 border border-gray-800 rounded-xl p-5 hover:border-teal-500 transition-colors block">
              <div className="flex items-center gap-2 mb-3">
                <span className="text-white font-medium text-lg">{c.name}</span>
                <span className={`text-xs px-2 py-0.5 rounded ${
                  c.status === 'active' ? 'bg-green-500/20 text-green-400' : 'bg-gray-700 text-gray-400'
                }`}>{c.status}</span>
              </div>
              <div className="grid grid-cols-2 gap-3 text-sm">
                <div>
                  <p className="text-gray-500 text-xs">Agents</p>
                  <p className="text-white font-medium">{c.agent_count}</p>
                </div>
                <div>
                  <p className="text-gray-500 text-xs">MCP Servers</p>
                  <p className="text-white font-medium">{c.mcp_server_count}</p>
                </div>
              </div>
              <p className="text-gray-600 text-xs mt-3 font-mono">{c.id}</p>
            </Link>
          ))}
        </div>
      ) : (
        <div className="text-center py-12">
          <p className="text-gray-500 mb-2">No clients yet.</p>
          <p className="text-gray-600 text-sm">Create a client to deploy agents with isolated MCP tools and credentials.</p>
        </div>
      )}
    </div>
  );
}
