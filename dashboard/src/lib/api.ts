/**
 * Browser: empty base → same-origin `/api/*` (Next.js rewrites to Flask :5000).
 * Server (SSR): set NEXT_PUBLIC_API_URL or falls back to direct Flask.
 */
function apiBase(): string {
  if (process.env.NEXT_PUBLIC_API_URL) {
    return process.env.NEXT_PUBLIC_API_URL.replace(/\/$/, '');
  }
  if (typeof window !== 'undefined') {
    return '';
  }
  // Server-side (SSR) in Docker/K8s: set INTERNAL_API_URL=http://forgeos-api:5000
  const internal = process.env.INTERNAL_API_URL || '';
  if (internal) {
    return internal.replace(/\/$/, '');
  }
  return 'http://127.0.0.1:5000';
}

async function fetchJSON<T>(path: string, options?: RequestInit): Promise<T> {
  const res = await fetch(`${apiBase()}${path}`, {
    headers: { 'Content-Type': 'application/json', ...options?.headers },
    ...options,
  });
  if (!res.ok) throw new Error(`API error: ${res.status} ${res.statusText}`);
  return res.json();
}

export interface AgentSummary {
  agent_id: string;
  name: string;
  stack: string;
  execution_type: string;
  ownership: string;
  owner_id: string | null;
  status: string;
  description: string;
  department: string;
}

export interface PlatformOverview {
  total: number;
  by_stack: Record<string, number>;
  by_execution_type: Record<string, number>;
  by_ownership: Record<string, number>;
  running: number;
}

export interface CreateAgentPayload {
  name: string;
  stack: string;
  execution_type: string;
  ownership: string;
  owner_id?: string;
  description?: string;
  department?: string;
  goal?: string;
  schedule?: string;
  event_triggers?: string[];
  tools?: string[];
  metadata?: Record<string, unknown>;
  llm_config?: {
    chat_model: string;
    reasoning_model?: string;
    provider: string;
  };
}

export interface WizardChatMessage {
  role: 'user' | 'assistant';
  content: string;
}

export interface WizardChatResponse {
  assistant_message: string;
  proposal: CreateAgentPayload | null;
  clarifying_questions: string[];
  ready_to_deploy: boolean;
  warnings: string[];
  mode: string;
}

export const api = {
  getOverview: () => fetchJSON<PlatformOverview>('/api/platform/overview'),
  getAgents: (params?: Record<string, string>) => {
    const qs = params ? '?' + new URLSearchParams(params).toString() : '';
    return fetchJSON<AgentSummary[]>(`/api/platform/agents${qs}`);
  },
  getAgent: (id: string) => fetchJSON<AgentSummary>(`/api/platform/agents/${id}`),
  createAgent: (payload: CreateAgentPayload) =>
    fetchJSON<{ agent_id: string; name?: string; stack?: string }>('/api/platform/agents', {
      method: 'POST',
      body: JSON.stringify(payload),
    }),

  wizardChat: (messages: WizardChatMessage[], context?: Record<string, string>) =>
    fetchJSON<WizardChatResponse>('/api/platform/wizard/chat', {
      method: 'POST',
      body: JSON.stringify({ messages, context }),
    }),
  stopAgent: (id: string) =>
    fetchJSON<{ ok: boolean }>(`/api/platform/agents/${id}/stop`, { method: 'POST' }),
  deleteAgent: (id: string) =>
    fetchJSON<{ ok: boolean }>(`/api/platform/agents/${id}`, { method: 'DELETE' }),

  getApprovals: () => fetchJSON<any[]>('/api/approvals'),
  approveItem: (id: string) =>
    fetchJSON<any>(`/api/approvals/${id}/approve`, { method: 'POST' }),
  denyItem: (id: string) =>
    fetchJSON<any>(`/api/approvals/${id}/deny`, { method: 'POST' }),

  getWorkflows: () => fetchJSON<any[]>('/api/workflows'),
};
