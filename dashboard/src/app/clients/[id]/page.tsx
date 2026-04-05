'use client';

import { useEffect, useState } from 'react';
import { useParams } from 'next/navigation';
import Link from 'next/link';
import { api, type ClientSummary, type ClientMCPConfig, type AgentSummary } from '@/lib/api';

export default function ClientDetailPage() {
  const params = useParams();
  const clientId = params.id as string;

  const [client, setClient] = useState<(ClientSummary & { mcp_servers?: ClientMCPConfig[] }) | null>(null);
  const [agents, setAgents] = useState<AgentSummary[]>([]);
  const [loading, setLoading] = useState(true);

  // Add MCP form
  const [showAddMCP, setShowAddMCP] = useState(false);
  const [mcpName, setMcpName] = useState('');
  const [mcpPackage, setMcpPackage] = useState('');
  const [mcpEnvVars, setMcpEnvVars] = useState('');

  function load() {
    setLoading(true);
    Promise.all([
      api.getClient(clientId).catch(() => null),
      api.getClientAgents(clientId).catch(() => []),
    ]).then(([c, a]) => {
      setClient(c);
      setAgents(a);
    }).finally(() => setLoading(false));
  }

  useEffect(() => { load(); }, [clientId]);

  async function addMCP() {
    if (!mcpName.trim() || !mcpPackage.trim()) return;
    let envVars: Record<string, string> = {};
    if (mcpEnvVars.trim()) {
      try {
        envVars = JSON.parse(mcpEnvVars);
      } catch {
        alert('Invalid JSON for environment variables');
        return;
      }
    }
    try {
      await api.addClientMCPServer(clientId, {
        server_name: mcpName.trim(),
        package: mcpPackage.trim(),
        env_vars: envVars,
      });
      setMcpName('');
      setMcpPackage('');
      setMcpEnvVars('');
      setShowAddMCP(false);
      load();
    } catch (e: any) {
      alert(e.message);
    }
  }

  async function removeMCP(serverName: string) {
    try {
      await api.deleteClientMCPServer(clientId, serverName);
      load();
    } catch (e: any) {
      alert(e.message);
    }
  }

  if (loading) return <p className="text-gray-400">Loading...</p>;
  if (!client) return <p className="text-gray-500">Client not found.</p>;

  const mcpServers = client.mcp_servers || [];

  return (
    <div>
      <div className="flex items-center gap-3 mb-6">
        <Link href="/clients" className="text-gray-500 hover:text-white text-sm">&larr; Clients</Link>
        <span className="text-gray-700">/</span>
        <h1 className="text-2xl font-bold text-white">{client.name}</h1>
        <span className={`text-xs px-2 py-0.5 rounded ${
          client.status === 'active' ? 'bg-green-500/20 text-green-400' : 'bg-gray-700 text-gray-400'
        }`}>{client.status}</span>
      </div>

      {/* MCP Servers Section */}
      <div className="mb-8">
        <div className="flex items-center justify-between mb-3">
          <h2 className="text-lg font-semibold text-white">MCP Servers</h2>
          <button onClick={() => setShowAddMCP(!showAddMCP)}
            className="px-3 py-1.5 bg-purple-600 hover:bg-purple-500 text-white text-xs rounded-lg">
            + Add Server
          </button>
        </div>

        {showAddMCP && (
          <div className="bg-gray-900 border border-gray-800 rounded-xl p-4 mb-4 space-y-3">
            <div className="grid grid-cols-2 gap-3">
              <div>
                <label className="text-xs text-gray-400 block mb-1">Server Name</label>
                <input value={mcpName} onChange={(e) => setMcpName(e.target.value)} placeholder="jira"
                  className="w-full px-3 py-2 bg-white text-gray-900 border border-gray-300 rounded-lg text-sm" />
              </div>
              <div>
                <label className="text-xs text-gray-400 block mb-1">Package</label>
                <input value={mcpPackage} onChange={(e) => setMcpPackage(e.target.value)} placeholder="@anthropic/mcp-server-jira"
                  className="w-full px-3 py-2 bg-white text-gray-900 border border-gray-300 rounded-lg text-sm" />
              </div>
            </div>
            <div>
              <label className="text-xs text-gray-400 block mb-1">Environment Variables (JSON)</label>
              <textarea value={mcpEnvVars} onChange={(e) => setMcpEnvVars(e.target.value)}
                placeholder='{"JIRA_URL": "https://acme.atlassian.net", "JIRA_TOKEN": "..."}'
                className="w-full px-3 py-2 bg-white text-gray-900 border border-gray-300 rounded-lg text-sm h-20 font-mono" />
            </div>
            <button onClick={addMCP} className="px-4 py-2 bg-purple-600 hover:bg-purple-500 text-white text-sm rounded-lg">
              Add MCP Server
            </button>
          </div>
        )}

        {mcpServers.length > 0 ? (
          <div className="space-y-2">
            {mcpServers.map((s) => (
              <div key={s.server_name} className="bg-gray-900 border border-gray-800 rounded-xl p-4 flex items-center justify-between">
                <div>
                  <div className="flex items-center gap-2 mb-1">
                    <span className="text-white font-medium font-mono">{s.server_name}</span>
                    <span className={`text-xs px-2 py-0.5 rounded ${s.enabled ? 'bg-green-500/20 text-green-400' : 'bg-gray-700 text-gray-400'}`}>
                      {s.enabled ? 'enabled' : 'disabled'}
                    </span>
                  </div>
                  <p className="text-gray-500 text-xs font-mono">{s.package}</p>
                  {Object.keys(s.env_vars).length > 0 && (
                    <p className="text-gray-600 text-xs mt-1">{Object.keys(s.env_vars).length} env vars configured</p>
                  )}
                </div>
                <button onClick={() => removeMCP(s.server_name)} className="text-red-400 hover:text-red-300 text-xs">Remove</button>
              </div>
            ))}
          </div>
        ) : (
          <p className="text-gray-500 text-sm">No MCP servers configured. Add servers to connect this client&apos;s tools.</p>
        )}
      </div>

      {/* Agents Section */}
      <div>
        <div className="flex items-center justify-between mb-3">
          <h2 className="text-lg font-semibold text-white">Agents ({agents.length})</h2>
          <Link href={`/agents/create?client_id=${clientId}`}
            className="px-3 py-1.5 bg-sky-600 hover:bg-sky-500 text-white text-xs rounded-lg">
            + Deploy Agent
          </Link>
        </div>

        {agents.length > 0 ? (
          <div className="space-y-2">
            {agents.map((a) => (
              <Link key={a.agent_id} href={`/agents/${a.agent_id}`}
                className="bg-gray-900 border border-gray-800 rounded-xl p-4 block hover:border-sky-500 transition-colors">
                <div className="flex items-center gap-2 mb-1">
                  <span className="text-white font-medium">{a.name}</span>
                  <span className="text-xs px-2 py-0.5 rounded bg-sky-500/20 text-sky-400">{a.stack}</span>
                  <span className="text-xs px-2 py-0.5 rounded bg-gray-700 text-gray-400">{a.execution_type}</span>
                  <span className={`text-xs px-2 py-0.5 rounded ${
                    a.status === 'running' ? 'bg-green-500/20 text-green-400' : 'bg-gray-700 text-gray-400'
                  }`}>{a.status}</span>
                </div>
                <p className="text-gray-400 text-sm">{a.description}</p>
              </Link>
            ))}
          </div>
        ) : (
          <p className="text-gray-500 text-sm">No agents deployed for this client yet.</p>
        )}
      </div>
    </div>
  );
}
