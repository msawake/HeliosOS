import type { WizardState } from './types';

/** k8s-style name rule from the SDK manifest (metadata.name / namespace). */
const NAME_RE = /^[a-zA-Z][a-zA-Z0-9_-]{1,63}$/;

export function isValidName(s: string): boolean {
  return NAME_RE.test((s ?? '').trim());
}

export interface StepResult {
  ok: boolean;
  errors: Record<string, string>;
}

export const STEP_KEYS = [
  'basics', 'llm', 'tools', 'secrets', 'environment', 'governance', 'review',
] as const;
export type StepKey = (typeof STEP_KEYS)[number];

function ok(errors: Record<string, string>): StepResult {
  return { ok: Object.keys(errors).length === 0, errors };
}

export function validateStep(step: StepKey, s: WizardState): StepResult {
  const e: Record<string, string> = {};
  switch (step) {
    case 'basics': {
      if (!isValidName(s.name)) e.name = 'Required: 2–64 chars, start with a letter, [A-Za-z0-9_-].';
      if (!isValidName(s.namespace)) e.namespace = 'Invalid namespace (same rule as name).';
      if (s.ownership === 'client' && !s.ownerId.trim()) e.ownerId = 'owner_id is required for client ownership.';
      if (s.executionType === 'scheduled' && !s.schedule.trim()) e.schedule = 'A cron schedule is required.';
      if (s.executionType === 'event_driven' && s.eventTriggers.length === 0)
        e.eventTriggers = 'At least one event trigger is required.';
      if (s.executionType === 'autonomous' && !s.goal.trim()) e.goal = 'A goal is required for autonomous agents.';
      return ok(e);
    }
    case 'llm': {
      if (!s.chatModel.trim()) e.chatModel = 'A chat model is required.';
      if (s.apiKeyRefKind !== 'none' && !s.apiKeyRefName.trim())
        e.apiKeyRefName = 'Pick or enter a name for the API key reference.';
      return ok(e);
    }
    case 'tools':
    case 'secrets':
      return ok(e); // nothing required; tools-empty is a soft warning in the UI
    case 'environment': {
      if (s.envMode === 'embed' && !s.envImage.trim()) e.envImage = 'Choose an environment (image is empty).';
      if (s.envMode === 'attach' && !s.envDefId.trim()) e.envDefId = 'Choose an environment to attach.';
      return ok(e);
    }
    case 'governance': {
      s.approvals.forEach((r, i) => {
        if (!r.tool.trim()) e[`approval_${i}_tool`] = 'Rule needs a tool (name or wildcard).';
        if (r.mode === 'conditional') {
          if (!r.whenField.trim()) e[`approval_${i}_when`] = 'Conditional rules need a field.';
          if (!r.whenValue.trim()) e[`approval_${i}_whenValue`] = 'Conditional rules need a value.';
        }
      });
      s.policies.forEach((p, i) => {
        if (!p.name.trim()) e[`policy_${i}_name`] = 'Policy needs a name.';
        if (p.kind === 'ref' && !p.ref.trim()) e[`policy_${i}_ref`] = 'Policy needs a file path.';
        if (p.kind === 'inline') {
          if (!p.inline.trim()) e[`policy_${i}_inline`] = 'Inline policy needs JSON.';
          else {
            try {
              JSON.parse(p.inline);
            } catch {
              e[`policy_${i}_inline`] = 'Inline policy must be valid JSON.';
            }
          }
        }
      });
      return ok(e);
    }
    case 'review':
      return ok(e);
  }
}
