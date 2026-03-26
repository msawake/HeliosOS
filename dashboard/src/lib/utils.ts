import { clsx, type ClassValue } from 'clsx';
import { twMerge } from 'tailwind-merge';

export function cn(...inputs: ClassValue[]) {
  return twMerge(clsx(inputs));
}

export const STACKS = ['forgeos', 'crewai', 'adk', 'openclaw'] as const;
export const EXEC_TYPES = ['always_on', 'scheduled', 'event_driven', 'reflex', 'autonomous'] as const;
export const OWNERSHIP_TYPES = ['personal', 'shared'] as const;

export const STACK_LABELS: Record<string, string> = {
  forgeos: 'ForgeOS',
  crewai: 'CrewAI',
  adk: 'Google ADK',
  openclaw: 'OpenClaw',
};

export const EXEC_LABELS: Record<string, string> = {
  always_on: 'Always-On',
  scheduled: 'Scheduled',
  event_driven: 'Event-Driven',
  reflex: 'Reflex',
  autonomous: 'Autonomous',
};

export const STACK_COLORS: Record<string, string> = {
  forgeos: 'blue',
  crewai: 'purple',
  adk: 'green',
  openclaw: 'orange',
};
