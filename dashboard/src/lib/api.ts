const API_BASE = process.env.NEXT_PUBLIC_API_URL || 'http://localhost:5000';

async function fetchJSON<T>(path: string, options?: RequestInit): Promise<T> {
  const res = await fetch(`${API_BASE}${path}`, {
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
  llm_config?: {
    chat_model: string;
    reasoning_model?: string;
    provider: string;
  };
}

export const api = {
  getOverview: () => fetchJSON<PlatformOverview>('/api/platform/overview'),
  getAgents: (params?: Record<string, string>) => {
    const qs = params ? '?' + new URLSearchParams(params).toString() : '';
    return fetchJSON<AgentSummary[]>(`/api/platform/agents${qs}`);
  },
  getAgent: (id: string) => fetchJSON<AgentSummary>(`/api/platform/agents/${id}`),
  createAgent: (payload: CreateAgentPayload) =>
    fetchJSON<{ agent_id: string }>('/api/platform/agents', {
      method: 'POST',
      body: JSON.stringify(payload),
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
