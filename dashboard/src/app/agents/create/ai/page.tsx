'use client';

import { useState, useRef, useEffect } from 'react';
import Link from 'next/link';
import { useRouter } from 'next/navigation';
import { api, type WizardChatMessage, type CreateAgentPayload } from '@/lib/api';
import { STACK_LABELS, EXEC_LABELS } from '@/lib/utils';

function getEditId(): string | null {
  if (typeof window === 'undefined') return null;
  return new URLSearchParams(window.location.search).get('edit');
}

export default function AiWizardPage() {
  const router = useRouter();
  const [editAgentId] = useState(getEditId);
  const bottomRef = useRef<HTMLDivElement>(null);
  const [messages, setMessages] = useState<WizardChatMessage[]>([]);
  const [input, setInput] = useState('');
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');
  const [lastProposal, setLastProposal] = useState<CreateAgentPayload | null>(null);
  const [readyToDeploy, setReadyToDeploy] = useState(false);
  const [warnings, setWarnings] = useState<string[]>([]);
  const [mode, setMode] = useState('');
  const [clarifying, setClarifying] = useState<string[]>([]);
  const [deploying, setDeploying] = useState(false);
  const [editingAgent, setEditingAgent] = useState<any>(null);

  // Load existing agent config when in edit mode
  useEffect(() => {
    if (editAgentId) {
      api.getAgent(editAgentId)
        .then((agent) => {
          setEditingAgent(agent);
        })
        .catch(() => setEditingAgent(null));
    }
  }, [editAgentId]);

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: 'smooth' });
  }, [messages, loading]);

  async function sendMessage(text: string) {
    const trimmed = text.trim();
    if (!trimmed || loading) return;
    setError('');
    const userMsg: WizardChatMessage = { role: 'user', content: trimmed };
    const nextHistory = [...messages, userMsg];
    setMessages(nextHistory);
    setInput('');
    setLoading(true);
    try {
      const trimmedHistory = nextHistory.map((m) => ({
        ...m,
        content: m.content.length > 10000 ? m.content.slice(0, 10000) + '\n...[trimmed]' : m.content,
      }));
      const recentHistory = trimmedHistory.length > 50 ? trimmedHistory.slice(-50) : trimmedHistory;
      const wizardContext = editingAgent
        ? { default_owner_id: 'demo-user', mode: 'edit', existing_agent: editingAgent }
        : { default_owner_id: 'demo-user' };
      const res = await api.wizardChat(recentHistory, wizardContext);
      setMessages((prev) => [...prev, { role: 'assistant', content: res.assistant_message }]);
      setLastProposal(res.proposal as CreateAgentPayload | null);
      setReadyToDeploy(res.ready_to_deploy);
      setWarnings(res.warnings || []);
      setMode(res.mode || '');
      setClarifying(res.clarifying_questions || []);
    } catch (e: unknown) {
      const msg = e instanceof Error ? e.message : 'Wizard request failed';
      setError(msg);
      setMessages((prev) => [...prev, { role: 'assistant', content: `**Error:** ${msg}` }]);
    } finally {
      setLoading(false);
    }
  }

  async function handleDeploy() {
    if (!lastProposal?.name || !lastProposal?.stack) {
      setError('No valid proposal to deploy.');
      return;
    }
    setDeploying(true);
    setError('');
    try {
      const payload: CreateAgentPayload = {
        name: lastProposal.name,
        stack: lastProposal.stack,
        execution_type: lastProposal.execution_type || 'reflex',
        ownership: lastProposal.ownership || 'shared',
        owner_id: lastProposal.owner_id,
        description: lastProposal.description,
        department: lastProposal.department,
        goal: lastProposal.goal || undefined,
        schedule: lastProposal.schedule || undefined,
        event_triggers: lastProposal.event_triggers?.length ? lastProposal.event_triggers : undefined,
        tools: lastProposal.tools?.length ? lastProposal.tools : undefined,
        metadata: lastProposal.metadata,
        llm_config: lastProposal.llm_config,
      };
      if (editAgentId) {
        // Edit mode: update existing agent
        await api.updateAgent(editAgentId, payload);
        router.push(`/agents/${editAgentId}`);
      } else {
        // Create mode: deploy new agent
        const data = await api.createAgent(payload);
        router.push(`/agents/${data.agent_id}`);
      }
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : 'Deploy failed');
    } finally {
      setDeploying(false);
    }
  }

  function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    sendMessage(input);
  }

  return (
    <div className="max-w-4xl">
      <Link href="/agents/create" className="text-sm text-gray-400 hover:text-gray-600 mb-4 inline-block">
        &larr; Manual create
      </Link>
      <h1 className="text-2xl font-bold mb-2">
        {editAgentId ? 'Edit agent with AI' : 'AI-assisted agent wizard'}
      </h1>
      {editingAgent ? (
        <div className="bg-[#f0fdf8] border border-[#10A37F]/20 rounded-lg px-4 py-3 mb-6">
          <p className="text-sm text-[#0a7a5e] font-medium">
            Editing: <span className="font-semibold">{editingAgent.name}</span>
            <span className="text-[#6e6e80] font-normal ml-2">
              ({editingAgent.stack} / {editingAgent.execution_type})
            </span>
          </p>
          <p className="text-xs text-[#6e6e80] mt-1">
            Describe what you want to change. The wizard will produce an updated proposal preserving your existing config.
          </p>
        </div>
      ) : (
        <p className="text-gray-500 text-sm mb-6">
          Describe what the agent should do. The server picks a stack and execution model (or uses offline
          heuristics when no LLM API key is configured). Review the draft, then deploy.
        </p>
      )}

      <div className="grid grid-cols-1 lg:grid-cols-5 gap-6">
        <div className="lg:col-span-3 card flex flex-col min-h-[420px] max-h-[560px]">
          <h2 className="font-semibold text-sm text-gray-500 mb-3">Conversation</h2>
          <div className="flex-1 overflow-y-auto space-y-3 mb-3 pr-1">
            {messages.length === 0 && (
              <p className="text-gray-400 text-sm">
                Example: “I need a personal agent that triages my inbox when new email arrives, OpenClaw style.”
              </p>
            )}
            {messages.map((m, i) => (
              <div
                key={i}
                className={`rounded-lg px-3 py-2 text-sm ${
                  m.role === 'user' ? 'bg-[#f0fdf8] text-gray-900 ml-8' : 'bg-gray-50 text-gray-800 mr-8'
                }`}
              >
                <span className="text-xs font-medium text-gray-400 block mb-1">
                  {m.role === 'user' ? 'You' : 'Architect'}
                </span>
                <div className="whitespace-pre-wrap">{m.content}</div>
              </div>
            ))}
            {loading && <p className="text-gray-400 text-sm">Thinking…</p>}
            <div ref={bottomRef} />
          </div>
          <form onSubmit={handleSubmit} className="flex gap-2">
            <textarea
              value={input}
              onChange={(e) => setInput(e.target.value)}
              rows={2}
              className="flex-1 rounded-lg border border-gray-300 text-sm p-2"
              placeholder="Describe your agent…"
              disabled={loading}
            />
            <button
              type="submit"
              disabled={loading || !input.trim()}
              className="self-end px-4 py-2 bg-[#10A37F] text-white rounded-lg text-sm font-medium hover:bg-[#0d8c6d] disabled:opacity-50"
            >
              Send
            </button>
          </form>
        </div>

        <div className="lg:col-span-2 space-y-4">
          <div className="card">
            <h2 className="font-semibold mb-2">Draft proposal</h2>
            {mode && (
              <p className="text-xs text-gray-400 mb-2">
                Mode: <span className="font-mono">{mode}</span>
              </p>
            )}
            {lastProposal ? (
              <ul className="text-sm space-y-1 text-gray-700">
                <li>
                  <span className="text-gray-400">Name:</span> {lastProposal.name}
                </li>
                <li>
                  <span className="text-gray-400">Stack:</span>{' '}
                  {STACK_LABELS[lastProposal.stack] || lastProposal.stack}
                </li>
                <li>
                  <span className="text-gray-400">Execution:</span>{' '}
                  {EXEC_LABELS[lastProposal.execution_type] || lastProposal.execution_type}
                </li>
                <li>
                  <span className="text-gray-400">Ownership:</span> {lastProposal.ownership}
                  {lastProposal.owner_id ? ` (${lastProposal.owner_id})` : ''}
                </li>
                {(lastProposal.description || '').slice(0, 200) && (
                  <li className="pt-1 text-gray-600">{lastProposal.description}</li>
                )}
              </ul>
            ) : (
              <p className="text-gray-400 text-sm">No proposal yet — keep chatting.</p>
            )}
            {clarifying.length > 0 && (
              <div className="mt-3 text-xs text-amber-800 bg-amber-50 rounded-lg p-2">
                <p className="font-medium mb-1">Suggestions</p>
                <ul className="list-disc list-inside space-y-0.5">
                  {clarifying.map((q, i) => (
                    <li key={i}>{q}</li>
                  ))}
                </ul>
              </div>
            )}
            {warnings.length > 0 && (
              <ul className="mt-2 text-xs text-amber-700 list-disc list-inside">
                {warnings.map((w, i) => (
                  <li key={i}>{w}</li>
                ))}
              </ul>
            )}
            <button
              type="button"
              onClick={handleDeploy}
              disabled={!lastProposal?.name || !lastProposal?.stack || deploying || loading}
              className="mt-4 w-full py-2 bg-green-600 text-white rounded-lg text-sm font-medium hover:bg-green-700 disabled:opacity-50"
            >
              {deploying
                ? (editAgentId ? 'Updating…' : 'Deploying…')
                : editAgentId
                  ? 'Update Agent'
                  : readyToDeploy
                    ? 'Deploy agent'
                    : 'Deploy draft'}
            </button>
            {!readyToDeploy && lastProposal && (
              <p className="text-xs text-gray-400 mt-2">
                The model did not mark this as fully confirmed; you can still deploy the draft or refine in chat.
              </p>
            )}
            <button
              type="button"
              disabled={!lastProposal || deploying}
              onClick={() => {
                if (!lastProposal) return;
                const q = new URLSearchParams({
                  name: lastProposal.name,
                  stack: lastProposal.stack,
                  execution_type: lastProposal.execution_type || 'reflex',
                  ownership: lastProposal.ownership || 'shared',
                });
                router.push(`/agents/create?${q.toString()}`);
              }}
              className="mt-2 w-full py-2 border border-gray-300 rounded-lg text-sm text-gray-700 hover:bg-gray-50 disabled:opacity-50"
            >
              Open in manual form (prefill query)
            </button>
          </div>
        </div>
      </div>

      {error && <p className="text-red-600 text-sm mt-4">{error}</p>}
    </div>
  );
}
