'use client';

import { useEffect, useState, useRef, use } from 'react';
import Link from 'next/link';
import { api } from '@/lib/api';

const STATUS_COLORS: Record<string, string> = {
  running: 'bg-green-500/20 text-green-400',
  pending: 'bg-yellow-500/20 text-yellow-400',
  stopped: 'bg-gray-500/20 text-gray-400',
  failed: 'bg-red-500/20 text-red-400',
  started: 'bg-green-500/20 text-green-400',
  completed: 'bg-blue-500/20 text-blue-400',
};

interface EnvDetail {
  env_id: string;
  name: string;
  status: string;
  pod_status: string;
  agent_ids: string[];
  agents: Array<{ agent_id: string; status: string; model: string; loop_mode: boolean }>;
  service_url: string;
}

interface AgentInEnv {
  agent_id: string;
  name: string;
  status: string;
  environment_id: string;
}

export default function EnvironmentDetailPage({ params }: { params: Promise<{ id: string }> }) {
  const { id: envId } = use(params);
  const [env, setEnv] = useState<EnvDetail | null>(null);
  const [agents, setAgents] = useState<AgentInEnv[]>([]);
  const [tab, setTab] = useState<'agents' | 'logs' | 'config'>('agents');
  const [logs, setLogs] = useState('');
  const [logStatus, setLogStatus] = useState('');
  const [podName, setPodName] = useState('');
  const [loading, setLoading] = useState(true);

  // Deploy agent form
  const [showDeploy, setShowDeploy] = useState(false);
  const [agentName, setAgentName] = useState('');
  const [agentModel, setAgentModel] = useState('gemini-2.0-flash');
  const [agentProvider, setAgentProvider] = useState('google');
  const [agentPrompt, setAgentPrompt] = useState('');
  const [agentSystemPrompt, setAgentSystemPrompt] = useState('');
  const [deploying, setDeploying] = useState(false);

  const logsEndRef = useRef<HTMLDivElement>(null);

  const loadEnv = async () => {
    try {
      const [envData, agentsData] = await Promise.all([
        api.getEnvironment(envId),
        api.getEnvironmentAgents(envId),
      ]);
      setEnv(envData);
      setAgents(agentsData);
    } catch (e) {
      console.error(e);
    } finally {
      setLoading(false);
    }
  };

  const loadLogs = async () => {
    try {
      const data = await api.getEnvironmentLogs(envId);
      setLogs(data.logs);
      setLogStatus(data.status);
      setPodName(data.pod_name);
    } catch (e) {
      console.error(e);
    }
  };

  useEffect(() => {
    loadEnv();
  }, [envId]);

  useEffect(() => {
    if (tab === 'agents') {
      const interval = setInterval(loadEnv, 5000);
      return () => clearInterval(interval);
    }
    if (tab === 'logs') {
      loadLogs();
      const interval = setInterval(loadLogs, 3000);
      return () => clearInterval(interval);
    }
  }, [tab, envId]);

  useEffect(() => {
    if (tab === 'logs') {
      logsEndRef.current?.scrollIntoView({ behavior: 'smooth' });
    }
  }, [logs, tab]);

  const handleDeploy = async () => {
    if (!agentName.trim()) return;
    setDeploying(true);
    try {
      await api.deployAgentToEnvironment(envId, {
        name: agentName.trim(),
        chat_model: agentModel,
        provider: agentProvider,
        system_prompt: agentSystemPrompt,
        prompt: agentPrompt || `Standing duties for ${agentName.trim()}`,
        loop_mode: true,
        loop_interval: 120,
      });
      setAgentName('');
      setAgentPrompt('');
      setAgentSystemPrompt('');
      setShowDeploy(false);
      await loadEnv();
    } catch (e) {
      console.error(e);
    } finally {
      setDeploying(false);
    }
  };

  const handleRemoveAgent = async (agentId: string) => {
    try {
      await api.removeAgentFromEnvironment(envId, agentId);
      await loadEnv();
    } catch (e) {
      console.error(e);
    }
  };

  if (loading) return <div className="p-6 text-[#8e8ea0]">Loading...</div>;
  if (!env) return <div className="p-6 text-red-400">Environment not found</div>;

  return (
    <div className="p-6 max-w-6xl">
      {/* Header */}
      <div className="mb-6">
        <Link href="/environments" className="text-xs text-[#8e8ea0] hover:text-white transition">
          &larr; Environments
        </Link>
        <div className="flex items-center gap-3 mt-2">
          <div className="w-10 h-10 rounded-lg bg-[#10A37F]/20 flex items-center justify-center text-[#10A37F] font-bold">
            E
          </div>
          <div>
            <h1 className="text-xl font-semibold text-white">{env.name}</h1>
            <p className="text-xs text-[#6e6e80]">{envId}</p>
          </div>
          <span className={`ml-2 px-2 py-0.5 rounded text-xs font-medium ${STATUS_COLORS[env.status] || STATUS_COLORS.stopped}`}>
            {env.status}
          </span>
          <span className={`px-2 py-0.5 rounded text-xs font-medium ${STATUS_COLORS[env.pod_status] || STATUS_COLORS.stopped}`}>
            pod: {env.pod_status}
          </span>
        </div>
      </div>

      {/* Tabs */}
      <div className="flex gap-1 mb-4 border-b border-white/10">
        {(['agents', 'logs', 'config'] as const).map((t) => (
          <button
            key={t}
            onClick={() => setTab(t)}
            className={`px-4 py-2 text-sm font-medium capitalize transition ${
              tab === t
                ? 'text-white border-b-2 border-[#10A37F]'
                : 'text-[#8e8ea0] hover:text-white'
            }`}
          >
            {t}
          </button>
        ))}
      </div>

      {/* Agents Tab */}
      {tab === 'agents' && (
        <div>
          <div className="flex items-center justify-between mb-4">
            <p className="text-sm text-[#8e8ea0]">
              {agents.length} agent{agents.length !== 1 ? 's' : ''} deployed
            </p>
            <button
              onClick={() => setShowDeploy(!showDeploy)}
              className="px-3 py-1.5 bg-[#10A37F] text-white text-xs font-medium rounded-lg hover:bg-[#0e9371] transition"
            >
              Deploy Agent
            </button>
          </div>

          {showDeploy && (
            <div className="mb-4 p-4 bg-[#1a1a2e] border border-white/10 rounded-lg space-y-3">
              <input
                type="text"
                value={agentName}
                onChange={(e) => setAgentName(e.target.value)}
                placeholder="Agent name"
                className="w-full px-3 py-2 bg-[#0d0d0d] border border-white/10 rounded-lg text-sm text-white placeholder-[#6e6e80] focus:outline-none focus:border-[#10A37F]"
              />
              <div className="grid grid-cols-2 gap-3">
                <select
                  value={agentModel}
                  onChange={(e) => setAgentModel(e.target.value)}
                  className="px-3 py-2 bg-[#0d0d0d] border border-white/10 rounded-lg text-sm text-white focus:outline-none"
                >
                  <option value="gemini-2.0-flash">Gemini 2.0 Flash</option>
                  <option value="gpt-4o">GPT-4o</option>
                  <option value="gpt-4o-mini">GPT-4o Mini</option>
                  <option value="claude-sonnet-4-6">Claude Sonnet 4.6</option>
                </select>
                <select
                  value={agentProvider}
                  onChange={(e) => setAgentProvider(e.target.value)}
                  className="px-3 py-2 bg-[#0d0d0d] border border-white/10 rounded-lg text-sm text-white focus:outline-none"
                >
                  <option value="google">Google</option>
                  <option value="openai">OpenAI</option>
                  <option value="anthropic">Anthropic</option>
                </select>
              </div>
              <textarea
                value={agentSystemPrompt}
                onChange={(e) => setAgentSystemPrompt(e.target.value)}
                placeholder="System prompt"
                rows={2}
                className="w-full px-3 py-2 bg-[#0d0d0d] border border-white/10 rounded-lg text-sm text-white placeholder-[#6e6e80] focus:outline-none resize-none"
              />
              <textarea
                value={agentPrompt}
                onChange={(e) => setAgentPrompt(e.target.value)}
                placeholder="Initial prompt (optional)"
                rows={2}
                className="w-full px-3 py-2 bg-[#0d0d0d] border border-white/10 rounded-lg text-sm text-white placeholder-[#6e6e80] focus:outline-none resize-none"
              />
              <button
                onClick={handleDeploy}
                disabled={deploying || !agentName.trim()}
                className="px-4 py-2 bg-[#10A37F] text-white text-sm rounded-lg hover:bg-[#0e9371] disabled:opacity-50 transition"
              >
                {deploying ? 'Deploying...' : 'Deploy to Environment'}
              </button>
            </div>
          )}

          <div className="space-y-2">
            {agents.map((agent) => (
              <div
                key={agent.agent_id}
                className="flex items-center justify-between p-3 bg-[#1a1a2e] border border-white/10 rounded-lg"
              >
                <div className="flex items-center gap-3">
                  <div className="w-6 h-6 rounded bg-white/5 flex items-center justify-center text-[10px] text-[#8e8ea0]">
                    A
                  </div>
                  <div>
                    <p className="text-sm font-medium text-white">{agent.name}</p>
                    <p className="text-xs text-[#6e6e80]">{agent.agent_id}</p>
                  </div>
                </div>
                <div className="flex items-center gap-3">
                  <span className={`px-2 py-0.5 rounded text-xs font-medium ${STATUS_COLORS[agent.status] || STATUS_COLORS.stopped}`}>
                    {agent.status}
                  </span>
                  <button
                    onClick={() => handleRemoveAgent(agent.agent_id)}
                    className="text-xs text-red-400/60 hover:text-red-400 transition"
                  >
                    Remove
                  </button>
                </div>
              </div>
            ))}
          </div>
        </div>
      )}

      {/* Logs Tab */}
      {tab === 'logs' && (
        <div>
          <div className="flex items-center gap-3 mb-3">
            {podName && <span className="text-xs text-[#6e6e80]">Pod: {podName}</span>}
            <span className={`px-2 py-0.5 rounded text-xs font-medium ${STATUS_COLORS[logStatus] || STATUS_COLORS.stopped}`}>
              {logStatus}
            </span>
          </div>
          <div className="bg-[#0d0d0d] border border-white/10 rounded-lg p-4 overflow-y-auto max-h-[600px] font-mono">
            <pre className="text-xs text-[#00ff41] whitespace-pre-wrap">
              {logs || 'No logs available'}
            </pre>
            <div ref={logsEndRef} />
          </div>
        </div>
      )}

      {/* Config Tab */}
      {tab === 'config' && (
        <div className="bg-[#1a1a2e] border border-white/10 rounded-lg p-4">
          <pre className="text-xs text-[#8e8ea0] whitespace-pre-wrap">
            {JSON.stringify(env, null, 2)}
          </pre>
        </div>
      )}
    </div>
  );
}
