import type { SecretScope } from '@/lib/api';

// Option lists mirror the SDK manifest enums (src/forgeos_sdk/manifest.py).
export const FRAMEWORKS = [
  'forgeos', 'crewai', 'adk', 'openclaw', 'anthropic-agent-sdk', 'anthropic-managed', 'openai-agents',
] as const;
export const EXECUTION_TYPES = ['reflex', 'always_on', 'scheduled', 'event_driven', 'autonomous'] as const;
export const OWNERSHIP_TYPES = ['shared', 'personal', 'client'] as const;
export const PROVIDERS = ['anthropic', 'openai', 'google', 'openclaw', 'atlas', 'vertex', 'vllm'] as const;
export const AUDIT_LEVELS = ['none', 'basic', 'full'] as const;
export const PII_POLICIES = ['allow', 'detect', 'mask', 'redact', 'block'] as const;
export const APPROVAL_MODES = ['always', 'never', 'conditional'] as const;
export const APPROVAL_PRIORITIES = ['low', 'medium', 'high', 'critical'] as const;
export const APPROVAL_ON_TIMEOUT = ['proceed', 'abort', 'reask'] as const;
export const WHEN_OPS = ['gt', 'gte', 'lt', 'lte', 'eq', 'ne'] as const;
/** Providers that take an OpenAI-compatible endpoint (gateway/proxy). */
export const ENDPOINT_PROVIDERS = ['atlas', 'vllm', 'openclaw', 'openai'] as const;

export type Framework = (typeof FRAMEWORKS)[number];
export type ExecutionType = (typeof EXECUTION_TYPES)[number];
export type Ownership = (typeof OWNERSHIP_TYPES)[number];
export type Provider = (typeof PROVIDERS)[number];

export interface ApprovalRuleState {
  tool: string;
  mode: (typeof APPROVAL_MODES)[number];
  whenField: string; // e.g. tool_input.amount_usd  (mode=conditional)
  whenOp: (typeof WHEN_OPS)[number];
  whenValue: string;
  approvers: string[];
  slaHours: string; // kept as string for the input; coerced on build
  priority: (typeof APPROVAL_PRIORITIES)[number];
  onTimeout: (typeof APPROVAL_ON_TIMEOUT)[number];
  reason: string;
}

export interface PolicyRefState {
  name: string;
  kind: 'ref' | 'inline';
  ref: string; // path to .rego/.json
  inline: string; // raw JSON-logic text (validated as JSON on build)
}

export type EnvMode = 'none' | 'embed' | 'attach' | 'new';

export interface WizardState {
  // Step 1 — Basics
  name: string;
  description: string;
  namespace: string;
  department: string;
  stack: Framework;
  executionType: ExecutionType;
  ownership: Ownership;
  ownerId: string;
  schedule: string;
  eventTriggers: string[];
  goal: string;
  systemPrompt: string;
  // Step 2 — LLM
  chatModel: string;
  provider: Provider;
  endpoint: string;
  apiKeyRefKind: 'none' | 'secret' | 'env';
  apiKeyRefName: string;
  // Step 3 — Tools
  toolsAllowed: string[];
  toolsDenied: string[];
  /** How MCP tools obtain credentials — drives spec.metadata.{namespace_mcp,per_user_mcp}. */
  mcpCredScope: 'namespace' | 'user';
  // Step 4 — Secrets (session registry of names created/referenced this run)
  knownSecrets: { scope: SecretScope; namespace?: string; name: string }[];
  // Step 5 — Environment
  envMode: EnvMode;
  envDefId: string;
  envImage: string;
  // Step 6 — Governance
  auditLevel: (typeof AUDIT_LEVELS)[number];
  piiPolicy: (typeof PII_POLICIES)[number];
  signingRequired: boolean;
  budgets: {
    daily_usd: string;
    per_task_usd: string;
    max_tokens_per_run: string;
    max_tool_calls_per_run: string;
    max_concurrent_tasks: string;
  };
  approvals: ApprovalRuleState[];
  policies: PolicyRefState[];
}

export function initialWizardState(): WizardState {
  return {
    name: '',
    description: '',
    namespace: 'default',
    department: '',
    stack: 'forgeos',
    executionType: 'reflex',
    ownership: 'shared',
    ownerId: '',
    schedule: '',
    eventTriggers: [],
    goal: '',
    systemPrompt: '',
    chatModel: 'claude-sonnet-4-6',
    provider: 'anthropic',
    endpoint: '',
    apiKeyRefKind: 'none',
    apiKeyRefName: '',
    toolsAllowed: [],
    toolsDenied: [],
    mcpCredScope: 'user',
    knownSecrets: [],
    envMode: 'none',
    envDefId: '',
    envImage: '',
    auditLevel: 'full',
    piiPolicy: 'detect',
    signingRequired: false,
    budgets: {
      daily_usd: '',
      per_task_usd: '',
      max_tokens_per_run: '',
      max_tool_calls_per_run: '',
      max_concurrent_tasks: '',
    },
    approvals: [],
    policies: [],
  };
}

export function emptyApprovalRule(): ApprovalRuleState {
  return {
    tool: '',
    mode: 'always',
    whenField: 'tool_input.amount_usd',
    whenOp: 'gt',
    whenValue: '',
    approvers: [],
    slaHours: '24',
    priority: 'medium',
    onTimeout: 'abort',
    reason: '',
  };
}

export function emptyPolicyRef(): PolicyRefState {
  return { name: '', kind: 'ref', ref: '', inline: '' };
}
