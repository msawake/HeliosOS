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

  // MCP registry browser
  const [showBrowser, setShowBrowser] = useState(false);
  const [browserQuery, setBrowserQuery] = useState('');
  const [browserResults, setBrowserResults] = useState<Array<{ name: string; description: string; category: string }>>([]);
  const [browsing, setBrowsing] = useState(false);

  async function searchRegistry(query: string) {
    if (!query.trim()) return;
    setBrowsing(true);
    try {
      const data = await api.searchMCPs(query);
      setBrowserResults(data.packages || []);
    } catch {
      setBrowserResults([]);
    } finally {
      setBrowsing(false);
    }
  }

  function pickFromRegistry(pkg: { name: string; description: string }) {
    // Derive a server_name from the package name
    const shortName = pkg.name
      .replace(/^@[^/]+\//, '')  // strip scope
      .replace(/^mcp-server-/, '')
      .replace(/^server-/, '')
      .replace(/-mcp$/, '')
      .replace(/[^a-z0-9]/gi, '_')
      .toLowerCase();
    setMcpName(shortName || pkg.name);
    setMcpPackage(pkg.name);
    setMcpEnvVars('{}');
    setShowBrowser(false);
    setShowAddMCP(true);
  }

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
        <Link href="/clients" className="text-gray-500 hover:text-[#0d0d0d] text-sm">&larr; Clients</Link>
        <span className="text-gray-700">/</span>
        <h1 className="text-2xl font-semibold text-[#0d0d0d]">{client.name}</h1>
        <span className={`text-xs px-2 py-0.5 rounded ${
          client.status === 'active' ? 'bg-emerald-50 text-emerald-700 border border-emerald-200' : 'bg-gray-100 text-gray-500 border border-gray-200'
        }`}>{client.status}</span>
      </div>

      {/* MCP Servers Section */}
      <div className="mb-8">
        <div className="flex items-center justify-between mb-3">
          <h2 className="text-lg font-semibold text-[#0d0d0d]">MCP Servers</h2>
          <div className="flex gap-2">
            <button onClick={() => setShowBrowser(true)}
              className="px-3 py-1.5 bg-[#10A37F] hover:bg-[#0d8c6d] text-white text-xs rounded-lg">
              Browse Registry
            </button>
            <button onClick={() => setShowAddMCP(!showAddMCP)}
              className="px-3 py-1.5 bg-[#10A37F] hover:bg-[#0d8c6d] text-white text-xs rounded-lg">
              + Add Server
            </button>
          </div>
        </div>

        {showBrowser && (
          <div className="fixed inset-0 bg-black/70 z-50 flex items-center justify-center p-4"
               onClick={() => setShowBrowser(false)}>
            <div className="bg-white border border-[#e5e5e5] rounded-xl p-5 max-w-2xl w-full max-h-[80vh] overflow-hidden flex flex-col"
                 onClick={(e) => e.stopPropagation()}>
              <div className="flex items-center justify-between mb-4">
                <h3 className="text-[#0d0d0d] font-semibold">MCP Registry — 4,548 packages</h3>
                <button onClick={() => setShowBrowser(false)}
                  className="text-gray-400 hover:text-[#0d0d0d]">✕</button>
              </div>
              <div className="flex gap-2 mb-4">
                <input
                  value={browserQuery}
                  onChange={(e) => setBrowserQuery(e.target.value)}
                  onKeyDown={(e) => e.key === 'Enter' && searchRegistry(browserQuery)}
                  placeholder="Search: jira, slack, postgres, stripe..."
                  autoFocus
                  className="flex-1 px-3 py-2 bg-white text-gray-900 border border-gray-300 rounded-lg text-sm"
                />
                <button onClick={() => searchRegistry(browserQuery)}
                  className="px-4 py-2 bg-[#10A37F] hover:bg-[#0d8c6d] text-white text-sm rounded-lg font-medium">
                  Search
                </button>
              </div>
              <div className="flex-1 overflow-y-auto space-y-2">
                {browsing ? (
                  <p className="text-gray-500 text-sm text-center py-8">Searching…</p>
                ) : browserResults.length === 0 ? (
                  <p className="text-gray-500 text-sm text-center py-8">
                    {browserQuery ? `No results for "${browserQuery}"` : 'Enter a search term to browse the registry'}
                  </p>
                ) : (
                  browserResults.map((pkg) => (
                    <button
                      key={pkg.name}
                      onClick={() => pickFromRegistry(pkg)}
                      className="w-full text-left p-3 bg-[#f7f7f8] border border-[#e5e5e5] hover:border-[#10A37F] rounded-lg transition-colors"
                    >
                      <div className="flex items-center gap-2 mb-1">
                        <span className="text-[#0d0d0d] font-medium font-mono text-sm">{pkg.name}</span>
                        <span className="text-xs px-2 py-0.5 rounded bg-violet-50 text-violet-700">{pkg.category}</span>
                      </div>
                      <p className="text-gray-500 text-xs">{pkg.description}</p>
                    </button>
                  ))
                )}
              </div>
            </div>
          </div>
        )}

        {showAddMCP && (
          <div className="bg-white border border-[#e5e5e5] rounded-xl p-4 mb-4 space-y-3">
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
            <button onClick={addMCP} className="px-4 py-2 bg-[#10A37F] hover:bg-[#0d8c6d] text-white text-sm rounded-lg">
              Add MCP Server
            </button>
          </div>
        )}

        {mcpServers.length > 0 ? (
          <div className="space-y-2">
            {mcpServers.map((s) => (
              <div key={s.server_name} className="bg-white border border-[#e5e5e5] rounded-xl p-4 flex items-center justify-between">
                <div>
                  <div className="flex items-center gap-2 mb-1">
                    <span className="text-[#0d0d0d] font-medium font-mono">{s.server_name}</span>
                    <span className={`text-xs px-2 py-0.5 rounded ${s.enabled ? 'bg-emerald-50 text-emerald-700 border border-emerald-200' : 'bg-gray-100 text-gray-500 border border-gray-200'}`}>
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
          <h2 className="text-lg font-semibold text-[#0d0d0d]">Agents ({agents.length})</h2>
          <Link href={`/agents/create?client_id=${clientId}`}
            className="px-3 py-1.5 bg-[#10A37F] hover:bg-[#0d8c6d] text-white text-xs rounded-lg">
            + Deploy Agent
          </Link>
        </div>

        {agents.length > 0 ? (
          <div className="space-y-2">
            {agents.map((a) => (
              <Link key={a.agent_id} href={`/agents/${a.agent_id}`}
                className="bg-white border border-[#e5e5e5] rounded-xl p-4 block hover:border-[#10A37F] transition-colors">
                <div className="flex items-center gap-2 mb-1">
                  <span className="text-[#0d0d0d] font-medium">{a.name}</span>
                  <span className="text-xs px-2 py-0.5 rounded bg-cyan-50 text-cyan-700">{a.stack}</span>
                  <span className="text-xs px-2 py-0.5 rounded bg-gray-100 text-gray-500 border border-gray-200">{a.execution_type}</span>
                  <span className={`text-xs px-2 py-0.5 rounded ${
                    a.status === 'running' ? 'bg-emerald-50 text-emerald-700 border border-emerald-200' : 'bg-gray-100 text-gray-500 border border-gray-200'
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
