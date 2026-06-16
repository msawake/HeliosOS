import yaml from 'js-yaml';

import type { ApprovalRuleState, PolicyRefState, WizardState } from './types';

// Builds an `agentos/v1` AgentContract from wizard state. Uses the k8s-style
// envelope + the rich nested sections the wizard exposes (capabilities.tools
// ACL, boundaries, full governance), while keeping the flat execution fields
// (stack/execution_type/schedule/…) that the SDK validators and executor read
// directly — so the generated manifest reliably validates and deploys.

type Obj = Record<string, unknown>;

const num = (s: string): number | undefined => {
  const t = (s ?? '').trim();
  if (!t) return undefined;
  const n = Number(t);
  return Number.isFinite(n) ? n : undefined;
};

function buildApproval(r: ApprovalRuleState): Obj {
  const out: Obj = { tool: r.tool.trim(), mode: r.mode };
  if (r.mode === 'conditional') {
    out.when = {
      ask_human_if: { op: r.whenOp, field: r.whenField.trim(), value: num(r.whenValue) ?? r.whenValue.trim() },
    };
  }
  if (r.mode !== 'never') {
    if (r.approvers.length) out.approvers = r.approvers;
    const sla = num(r.slaHours);
    if (sla !== undefined) out.sla_hours = sla;
    out.priority = r.priority;
    out.on_timeout = r.onTimeout;
  }
  if (r.reason.trim()) out.reason = r.reason.trim();
  return out;
}

function buildPolicy(p: PolicyRefState): Obj | null {
  const name = p.name.trim();
  if (!name) return null;
  if (p.kind === 'inline') {
    try {
      return { name, inline: JSON.parse(p.inline) };
    } catch {
      return { name, inline: {} }; // validate.ts blocks bad JSON before deploy
    }
  }
  return p.ref.trim() ? { name, ref: p.ref.trim() } : null;
}

/** Build the manifest object (omitting empty/default keys). */
export function buildManifest(s: WizardState): Obj {
  const metadata: Obj = { name: s.name.trim() || 'unnamed-agent' };
  if (s.namespace && s.namespace !== 'default') metadata.namespace = s.namespace.trim();
  if (s.description.trim()) metadata.description = s.description.trim();
  if (s.department.trim()) metadata.department = s.department.trim();

  const llm: Obj = { chat_model: s.chatModel.trim(), provider: s.provider };
  if (s.endpoint.trim()) llm.endpoint = s.endpoint.trim();
  if (s.apiKeyRefKind !== 'none' && s.apiKeyRefName.trim()) {
    llm.api_key_ref = `${s.apiKeyRefKind}:${s.apiKeyRefName.trim()}`;
  }

  const spec: Obj = {
    stack: s.stack,
    execution_type: s.executionType,
    ownership: s.ownership,
    llm,
  };
  if (s.ownership === 'client' && s.ownerId.trim()) spec.owner_id = s.ownerId.trim();
  if (s.executionType === 'scheduled' && s.schedule.trim()) spec.schedule = s.schedule.trim();
  if (s.executionType === 'event_driven' && s.eventTriggers.length) spec.event_triggers = s.eventTriggers;
  if (s.executionType === 'autonomous' && s.goal.trim()) spec.goal = s.goal.trim();

  // Tools → capabilities.tools ACL (the requested k8s-style form).
  if (s.toolsAllowed.length || s.toolsDenied.length) {
    const tools: Obj = {};
    if (s.toolsAllowed.length) tools.allowed = s.toolsAllowed;
    if (s.toolsDenied.length) tools.denied = s.toolsDenied;
    spec.capabilities = { tools };
  }

  // Boundaries (budgets + data) — emit only the keys the user filled.
  const budgets: Obj = {};
  for (const [k, v] of Object.entries(s.budgets)) {
    const n = num(v as string);
    if (n !== undefined) budgets[k] = n;
  }
  const dataBoundaries: Obj = {};
  if (s.piiPolicy !== 'detect') dataBoundaries.pii_policy = s.piiPolicy;
  if (Object.keys(budgets).length || Object.keys(dataBoundaries).length) {
    const boundaries: Obj = {};
    if (Object.keys(budgets).length) boundaries.budgets = budgets;
    if (Object.keys(dataBoundaries).length) boundaries.data = dataBoundaries;
    spec.boundaries = boundaries;
  }

  // Governance — emit only when non-default.
  const approvals = s.approvals.filter((r) => r.tool.trim()).map(buildApproval);
  const policies = s.policies.map(buildPolicy).filter(Boolean) as Obj[];
  const gov: Obj = {};
  if (s.auditLevel !== 'full') gov.audit_level = s.auditLevel;
  if (s.signingRequired) gov.signing_required = true;
  if (approvals.length) gov.approvals = approvals;
  if (policies.length) gov.policies = policies;
  if (Object.keys(gov).length) spec.governance = gov;

  // Per-agent embedded execution environment.
  if (s.envMode === 'embed' && s.envImage.trim()) spec.environment = { image: s.envImage.trim() };

  // MCP credential routing hint (consumed by build_agent_context).
  const usesMcp = [...s.toolsAllowed, ...s.toolsDenied].some((t) => t.startsWith('mcp__'));
  if (usesMcp) {
    spec.metadata = s.mcpCredScope === 'namespace' ? { namespace_mcp: true } : { per_user_mcp: true };
  }

  if (s.systemPrompt.trim()) spec.system_prompt = s.systemPrompt.trim();

  return { apiVersion: 'agentos/v1', kind: 'AgentContract', metadata, spec };
}

/** Serialize the manifest to YAML (matching AgentEdit's dump options). */
export function manifestToYaml(s: WizardState): string {
  try {
    return yaml.dump(buildManifest(s), { lineWidth: 100, noRefs: true });
  } catch (e) {
    return `# Could not render manifest: ${e instanceof Error ? e.message : 'error'}`;
  }
}
