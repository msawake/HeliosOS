import { clsx, type ClassValue } from 'clsx';
import { twMerge } from 'tailwind-merge';

export function cn(...inputs: ClassValue[]) {
  return twMerge(clsx(inputs));
}

// The nine framework adapters the platform governs, plus the labels used
// across filters and detail views. Category labels only — no per-stack color
// (the design rations a single accent; stacks render as neutral badges).
export const STACKS = [
  'forgeos',
  'crewai',
  'adk',
  'langchain',
  'openclaw',
  'sandbox',
  'anthropic_agent',
  'anthropic_managed',
  'openai_agents',
] as const;

export const EXEC_TYPES = [
  'always_on',
  'scheduled',
  'event_driven',
  'reflex',
  'autonomous',
] as const;

export const OWNERSHIP_TYPES = ['personal', 'shared', 'client'] as const;

export const STACK_LABELS: Record<string, string> = {
  forgeos: 'Helios OS',
  crewai: 'CrewAI',
  adk: 'Google ADK',
  langchain: 'LangChain',
  openclaw: 'OpenClaw',
  sandbox: 'Sandbox',
  anthropic_agent: 'Anthropic SDK',
  anthropic_managed: 'Anthropic Managed',
  openai_agents: 'OpenAI Agents',
};

export const EXEC_LABELS: Record<string, string> = {
  always_on: 'Always-on',
  scheduled: 'Scheduled',
  event_driven: 'Event-driven',
  reflex: 'Reflex',
  autonomous: 'Autonomous',
};

/** Map a status string to a Badge variant (the only place status earns color). */
export function statusVariant(status?: string): 'default' | 'success' | 'danger' | 'warning' {
  switch ((status || '').toLowerCase()) {
    case 'running':
    case 'completed':
    case 'active':
      return 'success';
    case 'failed':
    case 'stopped':
    case 'error':
      return 'danger';
    case 'paused':
    case 'suspended':
    case 'awaiting_approval':
      return 'warning';
    default:
      return 'default';
  }
}

/** Compact, human relative time for log/event timestamps. */
export function relativeTime(ts?: string): string {
  if (!ts) return '';
  const then = new Date(ts).getTime();
  if (Number.isNaN(then)) return ts;
  const secs = Math.round((Date.now() - then) / 1000);
  if (secs < 5) return 'just now';
  if (secs < 60) return `${secs}s ago`;
  const mins = Math.round(secs / 60);
  if (mins < 60) return `${mins}m ago`;
  const hrs = Math.round(mins / 60);
  if (hrs < 24) return `${hrs}h ago`;
  return `${Math.round(hrs / 24)}d ago`;
}
