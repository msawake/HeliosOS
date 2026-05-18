'use client';

import { useState, useEffect, Suspense } from 'react';
import Link from 'next/link';
import { useRouter, useSearchParams } from 'next/navigation';
import { STACKS, EXEC_TYPES, STACK_LABELS, EXEC_LABELS } from '@/lib/utils';

const PROVIDERS = ['anthropic', 'openai', 'google', 'ollama'] as const;

const DEFAULT_MODELS: Record<string, string> = {
  anthropic: 'claude-4-sonnet',
  openai: 'gpt-4o',
  google: 'gemini-2.0-flash',
  ollama: 'llama3',
};

export default function CreateAgentPage() {
  return (
    <Suspense fallback={<div className="max-w-2xl text-gray-400 py-12">Loading…</div>}>
      <CreateAgentForm />
    </Suspense>
  );
}

function CreateAgentForm() {
  const router = useRouter();
  const searchParams = useSearchParams();
  const [step, setStep] = useState(0);
  const [form, setForm] = useState({
    name: '',
    stack: 'forgeos',
    execution_type: 'reflex',
    ownership: 'shared' as 'personal' | 'shared',
    owner_id: '',
    description: '',
    department: '',
    goal: '',
    schedule: '',
    event_triggers: '',
    tools: '',
    provider: 'anthropic',
    chat_model: 'claude-4-sonnet',
    reasoning_model: '',
  });
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState('');

  useEffect(() => {
    const name = searchParams.get('name');
    const stack = searchParams.get('stack');
    const execution_type = searchParams.get('execution_type');
    const ownership = searchParams.get('ownership');
    if (!name && !stack && !execution_type && !ownership) return;
    setForm((prev) => ({
      ...prev,
      ...(name ? { name } : {}),
      ...(stack && STACKS.includes(stack as (typeof STACKS)[number]) ? { stack } : {}),
      ...(execution_type && EXEC_TYPES.includes(execution_type as (typeof EXEC_TYPES)[number])
        ? { execution_type }
        : {}),
      ...(ownership === 'personal' || ownership === 'shared' ? { ownership } : {}),
    }));
  }, [searchParams]);

  const steps = [
    { title: 'Stack', description: 'Choose the agent framework' },
    { title: 'Execution', description: 'How the agent runs' },
    { title: 'Identity', description: 'Name, role, and ownership' },
    { title: 'LLM', description: 'Model configuration' },
    { title: 'Review', description: 'Confirm and deploy' },
  ];

  function update(field: string, value: string) {
    setForm((prev) => ({ ...prev, [field]: value }));
    if (field === 'provider') {
      setForm((prev) => ({ ...prev, provider: value, chat_model: DEFAULT_MODELS[value] || '' }));
    }
  }

  async function handleSubmit() {
    setSubmitting(true);
    setError('');
    try {
      const payload = {
        name: form.name,
        stack: form.stack,
        execution_type: form.execution_type,
        ownership: form.ownership,
        owner_id: form.ownership === 'personal' ? form.owner_id || undefined : undefined,
        description: form.description,
        department: form.department,
        goal: form.goal || undefined,
        schedule: form.schedule || undefined,
        event_triggers: form.event_triggers ? form.event_triggers.split(',').map((s) => s.trim()) : undefined,
        tools: form.tools ? form.tools.split(',').map((s) => s.trim()) : undefined,
        llm_config: {
          chat_model: form.chat_model,
          reasoning_model: form.reasoning_model || undefined,
          provider: form.provider,
        },
      };
      const res = await fetch('/api/platform/agents', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload),
      });
      if (!res.ok) throw new Error(await res.text());
      const data = await res.json();
      router.push(`/agents/${data.agent_id}`);
    } catch (e: any) {
      setError(e.message || 'Failed to create agent');
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <div className="max-w-2xl">
      <h1 className="text-2xl font-bold mb-2">Create Agent</h1>
      <p className="text-gray-500 mb-2">Deploy a new agent across any stack</p>
      <p className="text-sm text-gray-500 mb-8">
        Prefer a guided chat?{' '}
        <Link href="/agents/create/ai" className="text-[#10A37F] font-medium hover:underline">
          Use the AI wizard
        </Link>
        .
      </p>

      {/* Step indicators */}
      <div className="flex gap-2 mb-8">
        {steps.map((s, i) => (
          <button
            key={i}
            onClick={() => setStep(i)}
            data-testid={`wizard-step-${i + 1}`}
            className={`flex-1 py-2 px-3 rounded-lg text-xs font-medium text-center transition-colors ${
              i === step ? 'bg-[#10A37F] text-white' : i < step ? 'bg-[#f0fdf8] text-[#0a7a5e]' : 'bg-gray-100 text-gray-400'
            }`}
          >
            {s.title}
          </button>
        ))}
      </div>

      <div className="card">
        {step === 0 && (
          <div>
            <h2 className="font-semibold mb-4">Choose Stack</h2>
            <div className="grid grid-cols-2 gap-3">
              {STACKS.map((s) => (
                <button
                  key={s}
                  onClick={() => { update('stack', s); setStep(1); }}
                  data-testid={`stack-option-${s}`}
                  className={`p-4 rounded-xl border-2 text-left transition-all ${
                    form.stack === s ? 'border-[#10A37F] bg-[#f0fdf8]' : 'border-gray-200 hover:border-gray-300'
                  }`}
                >
                  <span className={`badge badge-${s} mb-2`}>{STACK_LABELS[s]}</span>
                  <p className="text-xs text-gray-500 mt-1">
                    {s === 'forgeos' && 'Built-in native stack with platform LLM + tools'}
                    {s === 'crewai' && 'Role-based teams: researcher + writer + reviewer'}
                    {s === 'adk' && 'Enterprise hierarchical agents on Google Cloud'}
                    {s === 'openclaw' && 'File-first local agents with SOUL.md + heartbeat'}
                  </p>
                </button>
              ))}
            </div>
          </div>
        )}

        {step === 1 && (
          <div>
            <h2 className="font-semibold mb-4">Execution Type</h2>
            <div className="space-y-2">
              {EXEC_TYPES.map((t) => (
                <button
                  key={t}
                  onClick={() => { update('execution_type', t); setStep(2); }}
                  data-testid={`exec-type-${t}`}
                  className={`w-full p-3 rounded-lg border-2 text-left transition-all ${
                    form.execution_type === t ? 'border-[#10A37F] bg-[#f0fdf8]' : 'border-gray-200 hover:border-gray-300'
                  }`}
                >
                  <span className={`badge badge-${t}`}>{EXEC_LABELS[t]}</span>
                  <p className="text-xs text-gray-500 mt-1">
                    {t === 'always_on' && 'Runs continuously in a background loop (24/7 monitoring, support)'}
                    {t === 'scheduled' && 'Triggered on a cron/interval schedule (daily reports, nightly scans)'}
                    {t === 'event_driven' && 'Wakes when events fire (new email, webhook, CRM update)'}
                    {t === 'reflex' && 'Simple stimulus-response, no planning (instant replies, routing)'}
                    {t === 'autonomous' && 'Goal-directed loop — runs until objective is met'}
                  </p>
                </button>
              ))}
            </div>
          </div>
        )}

        {step === 2 && (
          <div>
            <h2 className="font-semibold mb-4">Agent Identity</h2>
            <div className="space-y-4">
              <div>
                <label className="block text-sm font-medium text-gray-700 mb-1">Name</label>
                <input type="text" value={form.name} onChange={(e) => update('name', e.target.value)} className="w-full rounded-lg border-gray-300" placeholder="e.g. inbox-manager" />
              </div>
              <div>
                <label className="block text-sm font-medium text-gray-700 mb-1">Description</label>
                <textarea value={form.description} onChange={(e) => update('description', e.target.value)} className="w-full rounded-lg border-gray-300" rows={2} placeholder="What does this agent do?" />
              </div>
              <div className="grid grid-cols-2 gap-4">
                <div>
                  <label className="block text-sm font-medium text-gray-700 mb-1">Ownership</label>
                  <select value={form.ownership} onChange={(e) => update('ownership', e.target.value)} className="w-full rounded-lg border-gray-300">
                    <option value="shared">Shared (Corporate)</option>
                    <option value="personal">Personal</option>
                  </select>
                </div>
                <div>
                  <label className="block text-sm font-medium text-gray-700 mb-1">Department</label>
                  <input type="text" value={form.department} onChange={(e) => update('department', e.target.value)} className="w-full rounded-lg border-gray-300" placeholder="e.g. marketing" />
                </div>
              </div>
              {form.ownership === 'personal' && (
                <div>
                  <label className="block text-sm font-medium text-gray-700 mb-1">Owner ID</label>
                  <input type="text" value={form.owner_id} onChange={(e) => update('owner_id', e.target.value)} className="w-full rounded-lg border-gray-300" placeholder="user email or ID" />
                </div>
              )}
              {form.execution_type === 'scheduled' && (
                <div>
                  <label className="block text-sm font-medium text-gray-700 mb-1">Schedule</label>
                  <input type="text" value={form.schedule} onChange={(e) => update('schedule', e.target.value)} className="w-full rounded-lg border-gray-300" placeholder="e.g. every 15m, */30 * * * *" />
                </div>
              )}
              {form.execution_type === 'event_driven' && (
                <div>
                  <label className="block text-sm font-medium text-gray-700 mb-1">Event Triggers (comma-separated)</label>
                  <input type="text" value={form.event_triggers} onChange={(e) => update('event_triggers', e.target.value)} className="w-full rounded-lg border-gray-300" placeholder="new_email, crm_update" />
                </div>
              )}
              {form.execution_type === 'autonomous' && (
                <div>
                  <label className="block text-sm font-medium text-gray-700 mb-1">Goal</label>
                  <textarea value={form.goal} onChange={(e) => update('goal', e.target.value)} className="w-full rounded-lg border-gray-300" rows={2} placeholder="What should this agent achieve?" />
                </div>
              )}
              <div>
                <label className="block text-sm font-medium text-gray-700 mb-1">MCP Tools (comma-separated)</label>
                <input type="text" value={form.tools} onChange={(e) => update('tools', e.target.value)} className="w-full rounded-lg border-gray-300" placeholder="gmail, calendar, hubspot" />
              </div>
            </div>
            <button onClick={() => setStep(3)} className="mt-6 px-4 py-2 bg-[#10A37F] text-white rounded-lg text-sm font-medium">Next: LLM Config</button>
          </div>
        )}

        {step === 3 && (
          <div>
            <h2 className="font-semibold mb-4">LLM Configuration</h2>
            <div className="space-y-4">
              <div>
                <label className="block text-sm font-medium text-gray-700 mb-1">Provider</label>
                <select value={form.provider} onChange={(e) => update('provider', e.target.value)} className="w-full rounded-lg border-gray-300">
                  {PROVIDERS.map((p) => (
                    <option key={p} value={p}>{p.charAt(0).toUpperCase() + p.slice(1)}</option>
                  ))}
                </select>
              </div>
              <div>
                <label className="block text-sm font-medium text-gray-700 mb-1">Chat Model</label>
                <input type="text" value={form.chat_model} onChange={(e) => update('chat_model', e.target.value)} className="w-full rounded-lg border-gray-300" />
              </div>
              <div>
                <label className="block text-sm font-medium text-gray-700 mb-1">Reasoning Model (optional)</label>
                <input type="text" value={form.reasoning_model} onChange={(e) => update('reasoning_model', e.target.value)} className="w-full rounded-lg border-gray-300" placeholder="For deep thinking tasks" />
              </div>
            </div>
            <button onClick={() => setStep(4)} className="mt-6 px-4 py-2 bg-[#10A37F] text-white rounded-lg text-sm font-medium">Next: Review</button>
          </div>
        )}

        {step === 4 && (
          <div>
            <h2 className="font-semibold mb-4">Review &amp; Deploy</h2>
            <div className="bg-gray-50 rounded-lg p-4 space-y-2 text-sm">
              <div className="flex justify-between"><span className="text-gray-500">Name</span><span className="font-medium">{form.name || '(unnamed)'}</span></div>
              <div className="flex justify-between"><span className="text-gray-500">Stack</span><span className={`badge badge-${form.stack}`}>{STACK_LABELS[form.stack]}</span></div>
              <div className="flex justify-between"><span className="text-gray-500">Execution</span><span className={`badge badge-${form.execution_type}`}>{EXEC_LABELS[form.execution_type]}</span></div>
              <div className="flex justify-between"><span className="text-gray-500">Ownership</span><span className={`badge badge-${form.ownership}`}>{form.ownership}</span></div>
              <div className="flex justify-between"><span className="text-gray-500">LLM</span><span>{form.provider} / {form.chat_model}</span></div>
              {form.description && <div className="flex justify-between"><span className="text-gray-500">Description</span><span className="text-right max-w-xs truncate">{form.description}</span></div>}
            </div>
            {error && <p className="text-red-600 text-sm mt-3">{error}</p>}
            <button
              onClick={handleSubmit}
              disabled={submitting || !form.name}
              className="mt-6 w-full py-3 bg-[#10A37F] text-white rounded-lg font-medium hover:bg-[#0d8c6d] disabled:opacity-50 transition-colors"
            >
              {submitting ? 'Deploying...' : 'Deploy Agent'}
            </button>
          </div>
        )}
      </div>
    </div>
  );
}
