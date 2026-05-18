'use client';

import { useEffect, useState, useRef, useCallback } from 'react';
import { useParams, useRouter } from 'next/navigation';
import Link from 'next/link';
import { Badge } from '@/components/Badge';
import { STACK_LABELS, EXEC_LABELS } from '@/lib/utils';
import type { AgentSummary } from '@/lib/api';

interface InvokeResult {
  agent_id: string;
  status: string;
  output: string;
  error: string | null;
  warnings: string[] | null;
  tool_calls: unknown[];
  tokens_used: number;
  elapsed_ms: number;
}

interface ActivityEntry {
  ts: string;
  event: string;
  detail: string;
}

interface LogsData {
  agent_id: string;
  logs: string;
  pod_name: string;
  status: string;
}

type Tab = 'activity' | 'logs' | 'config';


export default function AgentDetailPage() {
  const params = useParams();
  const agentId = typeof params.id === 'string' ? params.id : params.id?.[0] ?? '';
  const router = useRouter();
  const [agent, setAgent] = useState<AgentSummary | null>(null);
  const [loading, setLoading] = useState(true);
  const [invokePrompt, setInvokePrompt] = useState(
    'Summarize the agent’s role in one paragraph and suggest one next action.'
  );
  const [invokeLoading, setInvokeLoading] = useState(false);
  const [invokeError, setInvokeError] = useState('');
  const [invokeResult, setInvokeResult] = useState<InvokeResult | null>(null);
  const [activeTab, setActiveTab] = useState<Tab>('activity');
  const [activity, setActivity] = useState<ActivityEntry[]>([]);
  const [logsData, setLogsData] = useState<LogsData | null>(null);
  const logsEndRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!agentId) return;
    fetch(`/api/platform/agents/${agentId}`)
      .then((r) => (r.ok ? r.json() : null))
      .then(setAgent)
      .catch(() => setAgent(null))
      .finally(() => setLoading(false));
  }, [agentId]);

  const fetchActivity = useCallback(() => {
    if (!agentId) return;
    fetch(`/api/platform/agents/${agentId}/activity`)
      .then((r) => (r.ok ? r.json() : { activity: [] }))
      .then((data) => setActivity(data.activity || []))
      .catch(() => {});
  }, [agentId]);

  const fetchLogs = useCallback(() => {
    if (!agentId) return;
    fetch(`/api/platform/agents/${agentId}/logs?tail=500`)
      .then((r) => (r.ok ? r.json() : null))
      .then((data) => {
        if (data) setLogsData(data);
      })
      .catch(() => {});
  }, [agentId]);

  useEffect(() => {
    if (activeTab === 'activity') {
      fetchActivity();
      const iv = setInterval(fetchActivity, 5000);
      return () => clearInterval(iv);
    }
    if (activeTab === 'logs') {
      fetchLogs();
      const iv = setInterval(fetchLogs, 3000);
      return () => clearInterval(iv);
    }
  }, [activeTab, fetchActivity, fetchLogs]);

  useEffect(() => {
    if (activeTab === 'logs' && logsEndRef.current) {
      logsEndRef.current.scrollIntoView({ behavior: 'smooth' });
    }
  }, [logsData, activeTab]);


  async function handleStop() {
    await fetch(`/api/platform/agents/${agentId}/stop`, { method: 'POST' });
    const r = await fetch(`/api/platform/agents/${agentId}`);
    if (r.ok) setAgent(await r.json());
  }

  async function handleDelete() {
    if (!confirm('Are you sure you want to undeploy this agent?')) return;
    await fetch(`/api/platform/agents/${agentId}`, { method: 'DELETE' });
    router.push('/agents');
  }

  async function handleInvoke(e: React.FormEvent) {
    e.preventDefault();
    const prompt = invokePrompt.trim();
    if (!prompt || !agentId) return;
    setInvokeLoading(true);
    setInvokeError('');
    setInvokeResult(null);
    try {
      const res = await fetch(`/api/platform/agents/${agentId}/invoke`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ prompt }),
      });
      const text = await res.text();
      if (!res.ok) {
        setInvokeError(text || `${res.status} ${res.statusText}`);
        return;
      }
      setInvokeResult(JSON.parse(text) as InvokeResult);
      const r = await fetch(`/api/platform/agents/${agentId}`);
      if (r.ok) setAgent(await r.json());
    } catch (err: unknown) {
      setInvokeError(err instanceof Error ? err.message : 'Invoke failed');
    } finally {
      setInvokeLoading(false);
    }
  }

  if (!agentId) return <div className="text-gray-400">Invalid agent id</div>;
  if (loading) return <div className="text-gray-400">Loading...</div>;
  if (!agent) return <div className="text-gray-400">Agent not found</div>;

  const tabs: { key: Tab; label: string }[] = [
    { key: 'activity', label: 'Activity' },
    { key: 'logs', label: 'Logs' },
    { key: 'config', label: 'Config' },
  ];

  return (
    <div>
      <button onClick={() => router.push('/agents')} className="text-sm text-gray-400 hover:text-gray-600 mb-4">
        &larr; Back to Agents
      </button>

      <div className="flex items-start justify-between mb-6">
        <div>
          <h1 className="text-2xl font-bold">{agent.name}</h1>
          <p className="text-gray-500 mt-1">{agent.description || 'No description'}</p>
          <p className="text-xs text-gray-400 font-mono mt-1">{agent.agent_id}</p>
        </div>
        <div className="flex gap-2">
          <Link href={`/agents/${agent.agent_id}/chat`}
            className="px-4 py-1.5 text-sm bg-[#10A37F] text-white rounded-lg hover:bg-[#0d8c6d] font-medium">
            Chat
          </Link>
          <Link href={`/agents/create/ai?edit=${agent.agent_id}`}
            className="px-4 py-1.5 text-sm bg-white border border-[#d1d1d1] text-[#0d0d0d] rounded-lg hover:bg-gray-50 font-medium">
            Edit with AI
          </Link>
          <button onClick={handleStop} className="px-3 py-1.5 text-sm bg-amber-100 text-amber-800 rounded-lg hover:bg-amber-200">
            Stop
          </button>
          <button onClick={handleDelete} className="px-3 py-1.5 text-sm bg-red-100 text-red-800 rounded-lg hover:bg-red-200">
            Undeploy
          </button>
        </div>
      </div>

      <div className="grid grid-cols-2 lg:grid-cols-4 gap-4 mb-8">
        <div className="card">
          <p className="text-xs text-gray-500 mb-1">Stack</p>
          <Badge label={STACK_LABELS[agent.stack] || agent.stack} variant={agent.stack} />
        </div>
        <div className="card">
          <p className="text-xs text-gray-500 mb-1">Execution Type</p>
          <Badge label={EXEC_LABELS[agent.execution_type] || agent.execution_type} variant={agent.execution_type} />
        </div>
        <div className="card">
          <p className="text-xs text-gray-500 mb-1">Ownership</p>
          <Badge label={agent.ownership} variant={agent.ownership} />
        </div>
        <div className="card">
          <p className="text-xs text-gray-500 mb-1">Status</p>
          <Badge label={agent.status} variant={agent.status} />
        </div>
      </div>

      <div className="card mb-6" data-testid="invoke-panel">
        <h2 className="font-semibold mb-3">Test invoke</h2>
        <p className="text-sm text-gray-500 mb-3">
          Sends a one-off prompt through the platform executor (<code className="text-xs bg-gray-100 px-1 rounded">POST /api/platform/agents/…/invoke</code>
          ). Uses your configured LLM providers or simulated mode if no API keys are set.
        </p>
        <form onSubmit={handleInvoke} className="space-y-3">
          <textarea
            value={invokePrompt}
            onChange={(e) => setInvokePrompt(e.target.value)}
            rows={4}
            className="w-full rounded-lg border border-gray-300 text-sm p-3 font-mono"
            placeholder="Enter a prompt..."
            disabled={invokeLoading}
          />
          <div className="flex items-center gap-3">
            <button
              type="submit"
              disabled={invokeLoading || !invokePrompt.trim()}
              className="px-4 py-2 bg-[#10A37F] text-white rounded-lg text-sm font-medium hover:bg-[#0d8c6d] disabled:opacity-50"
            >
              {invokeLoading ? 'Running…' : 'Run invoke'}
            </button>
            {invokeError && <span className="text-sm text-red-600">{invokeError}</span>}
          </div>
        </form>
        {invokeResult && (
          <div className="mt-4 space-y-2" data-testid="invoke-result">
            <div className="flex flex-wrap gap-2 text-xs">
              <Badge label={invokeResult.status} variant={invokeResult.status} />
              {invokeResult.tokens_used > 0 && (
                <span className="text-gray-500">Tokens: {invokeResult.tokens_used}</span>
              )}
              {invokeResult.elapsed_ms > 0 && (
                <span className="text-gray-500">{invokeResult.elapsed_ms.toFixed(0)} ms</span>
              )}
            </div>
            {invokeResult.warnings && invokeResult.warnings.length > 0 && (
              <div className="bg-amber-50 border border-amber-200 text-amber-800 rounded-lg p-3 text-xs">
                <p className="font-semibold mb-1">Warnings</p>
                {invokeResult.warnings.map((w, i) => (
                  <p key={i}>{w}</p>
                ))}
              </div>
            )}
            {invokeResult.error && !invokeResult.warnings?.length && (
              <pre className="bg-red-50 text-red-900 rounded-lg p-3 text-xs overflow-auto whitespace-pre-wrap">
                {invokeResult.error}
              </pre>
            )}
            {invokeResult.output && (
              <pre className="bg-gray-50 rounded-lg p-3 text-xs overflow-auto whitespace-pre-wrap">
                {invokeResult.output}
              </pre>
            )}
          </div>
        )}
      </div>

      {/* Tab bar */}
      <div className="border-b border-gray-200 mb-4">
        <div className="flex gap-0">
          {tabs.map((t) => (
            <button
              key={t.key}
              onClick={() => setActiveTab(t.key)}
              data-testid={`tab-${t.key}`}
              className={`px-4 py-2 text-sm font-medium border-b-2 transition-colors ${
                activeTab === t.key
                  ? 'border-[#10A37F] text-[#10A37F]'
                  : 'border-transparent text-gray-500 hover:text-gray-700 hover:border-gray-300'
              }`}
            >
              {t.label}
            </button>
          ))}
        </div>
      </div>

      {/* Activity tab */}
      {activeTab === 'activity' && (
        <div className="card">
          <h2 className="font-semibold mb-3">Activity Feed</h2>
          {activity.length === 0 ? (
            <p className="text-sm text-gray-400">No activity recorded yet. Deploy the agent to see events.</p>
          ) : (
            <div className="space-y-2 max-h-[500px] overflow-y-auto">
              {activity.map((entry, i) => (
                <div key={i} className="flex items-start gap-3 text-sm border-b border-gray-100 pb-2">
                  <span className="text-xs text-gray-400 font-mono whitespace-nowrap min-w-[140px]">
                    {entry.ts?.replace('T', ' ').replace('.000Z', '')}
                  </span>
                  <Badge
                    label={entry.event}
                    variant={
                      entry.event.includes('error') || entry.event.includes('failed') || entry.event.includes('timeout')
                        ? 'failed'
                        : entry.event.includes('completed')
                        ? 'completed'
                        : entry.event.includes('tool')
                        ? 'running'
                        : 'idle'
                    }
                  />
                  <span className="text-gray-600 truncate">{entry.detail}</span>
                </div>
              ))}
            </div>
          )}
        </div>
      )}

      {/* Logs tab */}
      {activeTab === 'logs' && (
        <div className="card">
          <div className="flex items-center justify-between mb-3">
            <h2 className="font-semibold">Container Logs</h2>
            {logsData && (
              <div className="flex gap-2 items-center text-xs">
                {logsData.pod_name && (
                  <span className="text-gray-400 font-mono">{logsData.pod_name}</span>
                )}
                <Badge label={logsData.status} variant={logsData.status === 'running' ? 'running' : logsData.status === 'succeeded' ? 'completed' : 'idle'} />
              </div>
            )}
          </div>
          <div className="bg-[#0d0d0d] rounded-lg p-4 max-h-[600px] overflow-y-auto">
            {logsData?.logs ? (
              <pre className="text-[#00ff41] text-xs font-mono whitespace-pre-wrap leading-relaxed">
                {logsData.logs}
                <div ref={logsEndRef} />
              </pre>
            ) : (
              <p className="text-gray-500 text-sm">No logs available. The agent may not have started yet.</p>
            )}
          </div>
        </div>
      )}

      {/* Config tab */}
      {activeTab === 'config' && (
        <div className="card">
          <h2 className="font-semibold mb-3">Agent Configuration</h2>
          <pre className="bg-gray-50 rounded-lg p-4 text-xs overflow-auto">
            {JSON.stringify(agent, null, 2)}
          </pre>
        </div>
      )}
    </div>
  );
}
