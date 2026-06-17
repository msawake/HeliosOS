import { useState } from 'react';

import { api, type Env, type ToolDef } from '@/lib/api';
import { Button } from '@/components/ui/button';
import { Input, Textarea } from '@/components/ui/input';
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select';
import { Badge } from '@/components/ui/badge';
import { CodeBlock } from '@/components/ui/code-block';
import {
  AUDIT_LEVELS, EXECUTION_TYPES, FRAMEWORKS, OWNERSHIP_TYPES, PII_POLICIES, PROVIDERS,
  ENDPOINT_PROVIDERS, type WizardState,
} from '@/lib/wizard/types';
import { manifestToYaml } from '@/lib/wizard/buildManifest';
import { LabeledField, ChipsInput, CheckboxRow } from './fields';
import { ToolPicker } from './ToolPicker';
import { SecretPicker } from './SecretPicker';
import { ApprovalRuleEditor, PolicyRefEditor } from './GovernanceEditors';

export interface StepProps {
  s: WizardState;
  patch: (p: Partial<WizardState>) => void;
  errors: Record<string, string>;
}

const BUDGET_FIELDS: { key: keyof WizardState['budgets']; label: string }[] = [
  { key: 'daily_usd', label: 'Daily USD' },
  { key: 'per_task_usd', label: 'Per-task USD' },
  { key: 'max_tokens_per_run', label: 'Max tokens / run' },
  { key: 'max_tool_calls_per_run', label: 'Max tool calls / run' },
  { key: 'max_concurrent_tasks', label: 'Max concurrent tasks' },
];

export function StepBasics({ s, patch, errors }: StepProps) {
  return (
    <div className="space-y-4">
      <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
        <LabeledField label="Name" htmlFor="w-name" error={errors.name}>
          <Input id="w-name" value={s.name} onChange={(e) => patch({ name: e.target.value })}
            placeholder="invoice-triage" />
        </LabeledField>
        <LabeledField label="Namespace" error={errors.namespace}>
          <Input value={s.namespace} onChange={(e) => patch({ namespace: e.target.value })} placeholder="default" />
        </LabeledField>
      </div>
      <LabeledField label="Description">
        <Input value={s.description} onChange={(e) => patch({ description: e.target.value })}
          placeholder="What this agent does" />
      </LabeledField>
      <div className="grid grid-cols-1 gap-4 sm:grid-cols-3">
        <LabeledField label="Framework">
          <Select value={s.stack} onValueChange={(v) => patch({ stack: v as WizardState['stack'] })}>
            <SelectTrigger><SelectValue /></SelectTrigger>
            <SelectContent>
              {FRAMEWORKS.map((f) => <SelectItem key={f} value={f}>{f}</SelectItem>)}
            </SelectContent>
          </Select>
        </LabeledField>
        <LabeledField label="Execution type">
          <Select value={s.executionType}
            onValueChange={(v) => patch({ executionType: v as WizardState['executionType'] })}>
            <SelectTrigger><SelectValue /></SelectTrigger>
            <SelectContent>
              {EXECUTION_TYPES.map((t) => <SelectItem key={t} value={t}>{t}</SelectItem>)}
            </SelectContent>
          </Select>
        </LabeledField>
        <LabeledField label="Ownership">
          <Select value={s.ownership} onValueChange={(v) => patch({ ownership: v as WizardState['ownership'] })}>
            <SelectTrigger><SelectValue /></SelectTrigger>
            <SelectContent>
              {OWNERSHIP_TYPES.map((o) => <SelectItem key={o} value={o}>{o}</SelectItem>)}
            </SelectContent>
          </Select>
        </LabeledField>
      </div>

      {s.ownership === 'client' ? (
        <LabeledField label="Owner id" error={errors.ownerId}>
          <Input value={s.ownerId} onChange={(e) => patch({ ownerId: e.target.value })} placeholder="client-123" />
        </LabeledField>
      ) : null}
      {s.executionType === 'scheduled' ? (
        <LabeledField label="Schedule (cron)" error={errors.schedule} description="5-field cron, e.g. 0 7 * * *">
          <Input value={s.schedule} onChange={(e) => patch({ schedule: e.target.value })}
            placeholder="0 7 * * *" className="font-mono text-[12px]" />
        </LabeledField>
      ) : null}
      {s.executionType === 'event_driven' ? (
        <LabeledField label="Event triggers" error={errors.eventTriggers} description="Event names (Enter to add)">
          <ChipsInput values={s.eventTriggers} onChange={(v) => patch({ eventTriggers: v })}
            placeholder="invoice.received" ariaLabel="event trigger" />
        </LabeledField>
      ) : null}
      {s.executionType === 'autonomous' ? (
        <LabeledField label="Goal" error={errors.goal}>
          <Textarea value={s.goal} onChange={(e) => patch({ goal: e.target.value })}
            placeholder="What this agent should accomplish autonomously" />
        </LabeledField>
      ) : null}

      <LabeledField label="System prompt">
        <Textarea value={s.systemPrompt} onChange={(e) => patch({ systemPrompt: e.target.value })}
          placeholder="You are a helpful Helios OS agent…" className="min-h-32" />
      </LabeledField>
    </div>
  );
}

export function StepLlm({ s, patch, errors }: StepProps) {
  const needsEndpoint = (ENDPOINT_PROVIDERS as readonly string[]).includes(s.provider);
  return (
    <div className="space-y-4">
      <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
        <LabeledField label="Chat model" error={errors.chatModel}>
          <Input value={s.chatModel} onChange={(e) => patch({ chatModel: e.target.value })}
            placeholder="claude-sonnet-4-6" />
        </LabeledField>
        <LabeledField label="Provider">
          <Select value={s.provider} onValueChange={(v) => patch({ provider: v as WizardState['provider'] })}>
            <SelectTrigger><SelectValue /></SelectTrigger>
            <SelectContent>
              {PROVIDERS.map((p) => <SelectItem key={p} value={p}>{p}</SelectItem>)}
            </SelectContent>
          </Select>
        </LabeledField>
      </div>
      {needsEndpoint ? (
        <LabeledField label="Endpoint" description="OpenAI-compatible gateway/proxy base URL">
          <Input value={s.endpoint} onChange={(e) => patch({ endpoint: e.target.value })}
            placeholder="https://gateway.example.com/v1" className="font-mono text-[12px]" />
        </LabeledField>
      ) : null}

      <LabeledField label="API key reference" error={errors.apiKeyRefName}
        description="How the model's key is resolved at invoke time (never inline).">
        <Select value={s.apiKeyRefKind}
          onValueChange={(v) => patch({ apiKeyRefKind: v as WizardState['apiKeyRefKind'] })}>
          <SelectTrigger><SelectValue /></SelectTrigger>
          <SelectContent>
            <SelectItem value="none">None (use platform default key)</SelectItem>
            <SelectItem value="secret">secret: — a stored scoped secret</SelectItem>
            <SelectItem value="env">env: — an environment variable</SelectItem>
          </SelectContent>
        </Select>
      </LabeledField>

      {s.apiKeyRefKind === 'secret' ? (
        <SecretPicker namespace={s.namespace} selected={s.apiKeyRefName}
          onPick={(name) => patch({ apiKeyRefName: name })} />
      ) : null}
      {s.apiKeyRefKind === 'env' ? (
        <LabeledField label="Environment variable">
          <Input value={s.apiKeyRefName} onChange={(e) => patch({ apiKeyRefName: e.target.value })}
            placeholder="OPENAI_API_KEY" className="font-mono text-[12px]" />
        </LabeledField>
      ) : null}
    </div>
  );
}

export function StepTools({ s, patch, tools, loading }: StepProps & { tools: ToolDef[]; loading: boolean }) {
  const usesMcp = [...s.toolsAllowed, ...s.toolsDenied].some((t) => t.startsWith('mcp__'));
  return (
    <div className="space-y-4">
      <LabeledField label="Allowed tools" description="What the agent may call. Leave empty for a no-tool agent.">
        <ToolPicker tools={tools} selected={s.toolsAllowed} loading={loading}
          onChange={(v) => patch({ toolsAllowed: v })} />
      </LabeledField>
      {s.toolsAllowed.length === 0 ? (
        <p className="text-xs text-warning">This agent will have no tools.</p>
      ) : null}
      <LabeledField label="Denied tools (optional)"
        description="Explicit deny-list (wins over allowed) — e.g. a destructive subset of a wildcard.">
        <ChipsInput values={s.toolsDenied} onChange={(v) => patch({ toolsDenied: v })}
          placeholder="mcp__atlassian__jira_delete_*" ariaLabel="denied tool" />
      </LabeledField>
      {usesMcp ? (
        <LabeledField label="MCP credentials"
          description="Which credentials MCP servers run with for this agent.">
          <Select value={s.mcpCredScope}
            onValueChange={(v) => patch({ mcpCredScope: v as WizardState['mcpCredScope'] })}>
            <SelectTrigger><SelectValue /></SelectTrigger>
            <SelectContent>
              <SelectItem value="user">Per-user — each invoker's own credentials</SelectItem>
              <SelectItem value="namespace">Namespace — shared team credentials (fallback to user)</SelectItem>
            </SelectContent>
          </Select>
        </LabeledField>
      ) : null}
    </div>
  );
}

export function StepSecrets({ s }: StepProps) {
  return (
    <div className="space-y-3">
      <p className="text-[13px] text-secondary">
        Secrets live in three tiers — <span className="font-medium">platform</span> (org-wide),{' '}
        <span className="font-medium">namespace</span> (team), and <span className="font-medium">user</span>{' '}
        (yours). Create them here (write-only), then reference them from the LLM key or MCP tools.
        Values are encrypted and never read back.
      </p>
      <SecretPicker namespace={s.namespace} onPick={() => {}} />
      <p className="text-xs text-tertiary">
        Manifest refs resolve <span className="font-mono">user → namespace → platform</span> (MCP credentials
        prefer the namespace tier). Pin a tier explicitly with{' '}
        <span className="font-mono">secret:ns/&lt;name&gt;</span>.
      </p>
    </div>
  );
}

export function StepEnvironment({ s, patch, errors, envs, loading }: StepProps & { envs: Env[]; loading: boolean }) {
  const [creating, setCreating] = useState(false);
  const [newName, setNewName] = useState('');
  const [newImage, setNewImage] = useState('');
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  const pickEnv = (id: string) => {
    const env = envs.find((e) => e.env_def_id === id);
    patch({ envDefId: id, envImage: env?.image ?? '' });
  };

  const createEnv = async () => {
    setBusy(true);
    setErr(null);
    try {
      const env = await api.createEnv({ name: newName.trim(), image: newImage.trim() });
      patch({ envMode: 'embed', envDefId: env.env_def_id, envImage: env.image });
      setCreating(false);
    } catch (e) {
      setErr(e instanceof Error ? e.message : 'Could not create environment');
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="space-y-4">
      <LabeledField label="Execution environment"
        description="Optionally run this agent's shell/exec inside a dedicated pod.">
        <Select value={s.envMode} onValueChange={(v) => patch({ envMode: v as WizardState['envMode'] })}>
          <SelectTrigger><SelectValue /></SelectTrigger>
          <SelectContent>
            <SelectItem value="none">None — run on the platform host</SelectItem>
            <SelectItem value="embed">Embed an image in the manifest</SelectItem>
            <SelectItem value="attach">Attach a reusable env after deploy</SelectItem>
            <SelectItem value="new">Create a new environment…</SelectItem>
          </SelectContent>
        </Select>
      </LabeledField>

      {(s.envMode === 'embed' || s.envMode === 'attach') ? (
        <LabeledField label="Environment"
          error={s.envMode === 'embed' ? errors.envImage : errors.envDefId}>
          {loading ? (
            <p className="text-[13px] text-tertiary">Loading environments…</p>
          ) : envs.length === 0 ? (
            <p className="text-[13px] text-tertiary">No environments defined — choose “Create a new environment”.</p>
          ) : (
            <Select value={s.envDefId} onValueChange={(v) => pickEnv(v)}>
              <SelectTrigger><SelectValue placeholder="Select an environment…" /></SelectTrigger>
              <SelectContent>
                {envs.map((e) => (
                  <SelectItem key={e.env_def_id} value={e.env_def_id}>{e.name} ({e.image})</SelectItem>
                ))}
              </SelectContent>
            </Select>
          )}
        </LabeledField>
      ) : null}

      {s.envMode === 'new' ? (
        <div className="space-y-3 rounded-lg border border-edge bg-surface p-3">
          <LabeledField label="Name">
            <Input value={newName} onChange={(e) => setNewName(e.target.value)} placeholder="python-tools" />
          </LabeledField>
          <LabeledField label="Image">
            <Input value={newImage} onChange={(e) => setNewImage(e.target.value)}
              placeholder="python:3.12" className="font-mono text-[12px]" />
          </LabeledField>
          {err ? <p className="text-xs text-danger">{err}</p> : null}
          <Button type="button" size="sm" onClick={createEnv}
            disabled={busy || !newName.trim() || !newImage.trim() || creating}>
            {busy ? 'Creating…' : 'Create + embed'}
          </Button>
        </div>
      ) : null}

      {s.envMode === 'attach' ? (
        <p className="text-xs text-tertiary">The pod is spawned right after the agent deploys.</p>
      ) : null}
    </div>
  );
}

export function StepGovernance({ s, patch, errors }: StepProps) {
  return (
    <div className="space-y-5">
      <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
        <LabeledField label="Audit level">
          <Select value={s.auditLevel} onValueChange={(v) => patch({ auditLevel: v as WizardState['auditLevel'] })}>
            <SelectTrigger><SelectValue /></SelectTrigger>
            <SelectContent>
              {AUDIT_LEVELS.map((a) => <SelectItem key={a} value={a}>{a}</SelectItem>)}
            </SelectContent>
          </Select>
        </LabeledField>
        <LabeledField label="PII policy">
          <Select value={s.piiPolicy} onValueChange={(v) => patch({ piiPolicy: v as WizardState['piiPolicy'] })}>
            <SelectTrigger><SelectValue /></SelectTrigger>
            <SelectContent>
              {PII_POLICIES.map((p) => <SelectItem key={p} value={p}>{p}</SelectItem>)}
            </SelectContent>
          </Select>
        </LabeledField>
      </div>

      <CheckboxRow checked={s.signingRequired} onChange={(v) => patch({ signingRequired: v })}
        label="Require manifest signing" description="Reject unsigned deploys of this contract." />

      <div>
        <p className="mb-2 text-[13px] font-medium text-secondary">Budgets</p>
        <div className="grid grid-cols-2 gap-3 sm:grid-cols-3">
          {BUDGET_FIELDS.map(({ key, label }) => (
            <LabeledField key={key} label={label}>
              <Input type="number" value={s.budgets[key]}
                onChange={(e) => patch({ budgets: { ...s.budgets, [key]: e.target.value } })} placeholder="—" />
            </LabeledField>
          ))}
        </div>
      </div>

      <div>
        <p className="mb-2 text-[13px] font-medium text-secondary">Approval rules</p>
        <p className="mb-2 text-xs text-tertiary">
          Per-tool human-in-the-loop. First matching rule wins — order matters.
        </p>
        <ApprovalRuleEditor rules={s.approvals} errors={errors} onChange={(v) => patch({ approvals: v })} />
      </div>

      <div>
        <p className="mb-2 text-[13px] font-medium text-secondary">Policies</p>
        <PolicyRefEditor policies={s.policies} errors={errors} onChange={(v) => patch({ policies: v })} />
      </div>
    </div>
  );
}

export function StepReview({ s }: StepProps) {
  const yamlText = manifestToYaml(s);
  const toolCount = s.toolsAllowed.length;
  return (
    <div className="grid grid-cols-1 gap-5 lg:grid-cols-[1fr_1.2fr]">
      <div className="space-y-2 text-[13px]">
        {[
          ['Name', s.name],
          ['Namespace', s.namespace],
          ['Framework', s.stack],
          ['Execution', s.executionType],
          ['Model', `${s.chatModel} (${s.provider})`],
        ].map(([label, value]) => (
          <div key={label} className="flex justify-between gap-3 border-b border-edge-subtle pb-2">
            <span className="text-tertiary">{label}</span>
            <span className="font-mono text-xs text-primary">{value || '—'}</span>
          </div>
        ))}
        <div className="flex flex-wrap items-center gap-2 pt-1">
          <Badge variant="outline">{toolCount} tools</Badge>
          <Badge variant="outline">{s.approvals.length} approval rules</Badge>
          <Badge variant="outline">{s.policies.length} policies</Badge>
          {s.auditLevel !== 'full' ? <Badge variant="warning">audit: {s.auditLevel}</Badge> : null}
        </div>
      </div>
      <CodeBlock label="agent.yaml (agentos/v1)" code={yamlText} wrap maxHeight={460} />
    </div>
  );
}
