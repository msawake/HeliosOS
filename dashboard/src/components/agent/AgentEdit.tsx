'use client';

import { useMemo, useState } from 'react';
import yaml from 'js-yaml';
import { api, type Agent } from '@/lib/api';
import { Button } from '@/components/ui/button';
import { Textarea } from '@/components/ui/input';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';

/** The fields `forgeos edit` lets you change, surfaced as an editable YAML doc. */
function toEditable(agent: Agent): Record<string, unknown> {
  const metadata = Object.fromEntries(
    Object.entries(agent.metadata ?? {}).filter(([k]) => !k.startsWith('_'))
  );
  return {
    name: agent.name ?? '',
    description: agent.description ?? '',
    department: agent.department ?? '',
    execution_type: agent.execution_type ?? '',
    schedule: agent.schedule ?? '',
    chat_model: agent.llm_config?.chat_model ?? '',
    provider: agent.llm_config?.provider ?? '',
    // Gateway wiring for OpenAI-compatible providers (atlas/vllm). Surfaced so
    // an agent can be pointed at a real LLM gateway from the dashboard.
    endpoint: agent.llm_config?.endpoint ?? '',
    api_key_ref: agent.llm_config?.api_key_ref ?? '',
    goal: agent.goal ?? '',
    tools: agent.tools ?? [],
    event_triggers: agent.event_triggers ?? [],
    metadata,
    system_prompt: agent.system_prompt ?? '',
  };
}

export function AgentEdit({ agent, onSaved }: { agent: Agent; onSaved: () => void }) {
  const initial = useMemo(() => yaml.dump(toEditable(agent), { lineWidth: 100 }), [agent]);
  const [text, setText] = useState(initial);
  const [status, setStatus] = useState<'idle' | 'saving' | 'saved'>('idle');
  const [error, setError] = useState<string | null>(null);

  const dirty = text !== initial;

  const save = async () => {
    setError(null);
    let parsed: Record<string, unknown>;
    try {
      parsed = (yaml.load(text) as Record<string, unknown>) ?? {};
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Invalid YAML');
      return;
    }
    setStatus('saving');
    try {
      await api.updateAgent(agent.agent_id, parsed);
      setStatus('saved');
      onSaved();
      setTimeout(() => setStatus('idle'), 2000);
    } catch (e) {
      setStatus('idle');
      // Surface the server's validation detail (the authoritative check).
      setError(e instanceof Error ? e.message : 'Update failed');
    }
  };

  return (
    <Card>
      <CardHeader>
        <CardTitle>Edit manifest</CardTitle>
      </CardHeader>
      <CardContent className="space-y-3">
        <Textarea
          value={text}
          onChange={(e) => setText(e.target.value)}
          spellCheck={false}
          className="min-h-[26rem] font-mono text-xs leading-relaxed"
        />
        {error ? (
          <div role="alert" className="rounded-md border border-danger/20 bg-danger-wash px-4 py-3 text-[13px] text-danger">
            {error}
          </div>
        ) : null}
        <div className="flex items-center gap-3">
          <Button onClick={save} disabled={!dirty || status === 'saving'}>
            {status === 'saving' ? 'Saving…' : 'Save changes'}
          </Button>
          <Button variant="ghost" onClick={() => setText(initial)} disabled={!dirty}>
            Reset
          </Button>
          {status === 'saved' ? <span className="text-[13px] text-success">Saved.</span> : null}
        </div>
      </CardContent>
    </Card>
  );
}
