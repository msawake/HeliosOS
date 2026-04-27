'use client';

import { useEffect, useState } from 'react';
import Link from 'next/link';
import { api } from '@/lib/api';

interface Environment {
  env_id: string;
  name: string;
  status: string;
  namespace: string;
  agent_ids: string[];
  cpu_request: string;
  mem_request: string;
  created_at: string;
}

const STATUS_COLORS: Record<string, string> = {
  running: 'bg-green-500/20 text-green-400',
  pending: 'bg-yellow-500/20 text-yellow-400',
  stopped: 'bg-gray-500/20 text-gray-400',
  failed: 'bg-red-500/20 text-red-400',
};

export default function EnvironmentsPage() {
  const [environments, setEnvironments] = useState<Environment[]>([]);
  const [loading, setLoading] = useState(true);
  const [showCreate, setShowCreate] = useState(false);
  const [createName, setCreateName] = useState('');
  const [creating, setCreating] = useState(false);

  const load = async () => {
    try {
      const data = await api.getEnvironments();
      setEnvironments(data);
    } catch (e) {
      console.error(e);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    load();
    const interval = setInterval(load, 5000);
    return () => clearInterval(interval);
  }, []);

  const handleCreate = async () => {
    if (!createName.trim()) return;
    setCreating(true);
    try {
      await api.createEnvironment({ name: createName.trim() });
      setCreateName('');
      setShowCreate(false);
      await load();
    } catch (e) {
      console.error(e);
    } finally {
      setCreating(false);
    }
  };

  const handleDelete = async (envId: string) => {
    try {
      await api.deleteEnvironment(envId);
      await load();
    } catch (e) {
      console.error(e);
    }
  };

  return (
    <div className="p-6 max-w-6xl">
      <div className="flex items-center justify-between mb-6">
        <div>
          <h1 className="text-xl font-semibold text-white">Environments</h1>
          <p className="text-sm text-[#8e8ea0] mt-1">
            Shared compute pods where agents coexist and share filesystem
          </p>
        </div>
        <button
          onClick={() => setShowCreate(!showCreate)}
          className="px-4 py-2 bg-[#10A37F] text-white text-sm font-medium rounded-lg hover:bg-[#0e9371] transition"
        >
          Create Environment
        </button>
      </div>

      {showCreate && (
        <div className="mb-6 p-4 bg-[#1a1a2e] border border-white/10 rounded-lg">
          <div className="flex items-center gap-3">
            <input
              type="text"
              value={createName}
              onChange={(e) => setCreateName(e.target.value)}
              placeholder="Environment name (e.g. knowledge-env)"
              className="flex-1 px-3 py-2 bg-[#0d0d0d] border border-white/10 rounded-lg text-sm text-white placeholder-[#6e6e80] focus:outline-none focus:border-[#10A37F]"
              onKeyDown={(e) => e.key === 'Enter' && handleCreate()}
            />
            <button
              onClick={handleCreate}
              disabled={creating || !createName.trim()}
              className="px-4 py-2 bg-[#10A37F] text-white text-sm rounded-lg hover:bg-[#0e9371] disabled:opacity-50 transition"
            >
              {creating ? 'Creating...' : 'Create'}
            </button>
          </div>
        </div>
      )}

      {loading ? (
        <div className="text-[#8e8ea0] text-sm">Loading...</div>
      ) : environments.length === 0 ? (
        <div className="text-center py-16 text-[#8e8ea0]">
          <p className="text-lg mb-2">No environments yet</p>
          <p className="text-sm">Create an environment to deploy agents into a shared pod</p>
        </div>
      ) : (
        <div className="grid gap-4">
          {environments.map((env) => (
            <Link
              key={env.env_id}
              href={`/environments/${env.env_id}`}
              className="block p-4 bg-[#1a1a2e] border border-white/10 rounded-lg hover:border-white/20 transition"
            >
              <div className="flex items-center justify-between">
                <div className="flex items-center gap-3">
                  <div className="w-8 h-8 rounded-lg bg-[#10A37F]/20 flex items-center justify-center text-[#10A37F] text-xs font-bold">
                    E
                  </div>
                  <div>
                    <h3 className="text-sm font-medium text-white">{env.name}</h3>
                    <p className="text-xs text-[#6e6e80]">{env.env_id}</p>
                  </div>
                </div>
                <div className="flex items-center gap-4">
                  <div className="text-right">
                    <p className="text-xs text-[#8e8ea0]">
                      {env.agent_ids.length} agent{env.agent_ids.length !== 1 ? 's' : ''}
                    </p>
                    <p className="text-xs text-[#6e6e80]">
                      {env.cpu_request} CPU / {env.mem_request}
                    </p>
                  </div>
                  <span className={`px-2 py-0.5 rounded text-xs font-medium ${STATUS_COLORS[env.status] || STATUS_COLORS.stopped}`}>
                    {env.status}
                  </span>
                  <button
                    onClick={(e) => {
                      e.preventDefault();
                      e.stopPropagation();
                      handleDelete(env.env_id);
                    }}
                    className="text-xs text-red-400/60 hover:text-red-400 transition"
                  >
                    Delete
                  </button>
                </div>
              </div>
            </Link>
          ))}
        </div>
      )}
    </div>
  );
}
