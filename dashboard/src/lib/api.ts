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

function getAuthHeaders(): Record<string, string> {
  if (typeof window === 'undefined') return {};
  const token = sessionStorage.getItem('forgeos_token');
  if (token) return { Authorization: `Bearer ${token}` };
  const key = sessionStorage.getItem('forgeos_api_key');
  if (key) return { 'X-API-Key': key };
  return {};
}

async function fetchJSON<T>(path: string, options?: RequestInit): Promise<T> {
  const res = await fetch(`${apiBase()}${path}`, {
    headers: { 'Content-Type': 'application/json', ...getAuthHeaders(), ...options?.headers },
    ...options,
  });
  if (res.status === 401) {
    throw new Error('API returned 401 — check that backend is running with --no-auth');
  }
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
  client_id?: string;
  system_prompt?: string;
}

export interface ClientSummary {
  id: string;
  name: string;
  status: string;
  config: Record<string, unknown>;
  created_at: string;
  agent_count: number;
  mcp_server_count: number;
}

export interface ClientMCPConfig {
  server_name: string;
  package: string;
  env_vars: Record<string, string>;
  args: string[];
  enabled: boolean;
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

export interface EventEntry {
  id: string;
  name: string;
  source: string;
  target_department?: string;
  status: string;
  priority?: string;
  payload?: Record<string, unknown>;
  timestamp?: string;
}

export interface SystemHealth {
  status: string;
  components: Record<string, unknown>;
}

export interface KnowledgeEntry {
  id: string;
  title: string;
  category: string;
  content: string;
  tags?: string[];
}

export interface ScheduledJob {
  agent_id: string;
  name: string;
  schedule: string;
  next_run?: string;
  last_run?: string;
  status: string;
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
  updateAgent: (id: string, payload: CreateAgentPayload) =>
    fetchJSON<Record<string, unknown>>(`/api/platform/agents/${encodeURIComponent(id)}`, {
      method: 'PUT',
      body: JSON.stringify(payload),
    }),

  wizardChat: (messages: WizardChatMessage[], context?: Record<string, unknown>) =>
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
    fetchJSON<any>(`/api/approvals/${id}/reject`, { method: 'POST' }),

  getWorkflows: () => fetchJSON<any[]>('/api/workflows'),

  getProviderStatus: () =>
    fetchJSON<{
      providers: Record<string, { configured: boolean; client_initialized: boolean; env_var: string; sdk_installed?: boolean }>;
      feature_flags: Record<string, boolean>;
      available_providers: string[];
    }>('/api/admin/providers'),

  adminChat: (message: string, session_id: string = 'admin') =>
    fetchJSON<{ response: string; session_id: string; turns: number }>('/api/admin/chat', {
      method: 'POST',
      body: JSON.stringify({ message, session_id }),
    }),

  intelligenceAsk: (question: string, session_id: string = 'intelligence') =>
    fetchJSON<{ response: string; session_id: string; turns: number }>('/api/intelligence/ask', {
      method: 'POST',
      body: JSON.stringify({ question, session_id }),
    }),

  // Admin
  getSystemHealth: () => fetchJSON<SystemHealth>('/api/admin/health'),
  getMetrics: () => fetchJSON<Record<string, unknown>>('/api/admin/metrics'),
  getEvents: (params?: Record<string, string>) => {
    const qs = params ? '?' + new URLSearchParams(params).toString() : '';
    return fetchJSON<EventEntry[]>(`/api/admin/events${qs}`);
  },
  searchKnowledge: (query?: string, category?: string) => {
    const p = new URLSearchParams();
    if (query) p.set('query', query);
    if (category) p.set('category', category);
    const qs = p.toString() ? `?${p}` : '';
    return fetchJSON<KnowledgeEntry[]>(`/api/admin/knowledge${qs}`);
  },
  getScheduledJobs: () => fetchJSON<ScheduledJob[]>('/api/platform/scheduler'),
  getAudit: (limit?: number) => fetchJSON<any[]>(`/api/audit${limit ? `?limit=${limit}` : ''}`),

  // Skills
  getSkillDomains: () => fetchJSON<{ total: number; domains: { domain: string; count: number }[] }>('/api/skills/domains'),
  searchSkills: (query: string, domain?: string) => {
    const p = new URLSearchParams({ query });
    if (domain) p.set('domain', domain);
    return fetchJSON<{ count: number; skills: any[] }>(`/api/skills/search?${p}`);
  },
  getSkill: (name: string) => fetchJSON<any>(`/api/skills/${encodeURIComponent(name)}`),

  // MCPs
  getMCPCategories: () => fetchJSON<{ total: number; categories: { category: string; count: number }[] }>('/api/mcps/categories'),
  searchMCPs: (query: string, category?: string) => {
    const p = new URLSearchParams({ query });
    if (category) p.set('category', category);
    return fetchJSON<{ count: number; packages: any[] }>(`/api/mcps/search?${p}`);
  },
  getMCPPackage: (name: string) => fetchJSON<any>(`/api/mcps/${encodeURIComponent(name)}`),

  // Clients
  getClients: () => fetchJSON<ClientSummary[]>('/api/clients'),
  getClient: (id: string) => fetchJSON<ClientSummary & { mcp_servers: ClientMCPConfig[] }>(`/api/clients/${encodeURIComponent(id)}`),
  createClient: (id: string, name: string, config?: Record<string, unknown>) =>
    fetchJSON<ClientSummary>('/api/clients', {
      method: 'POST',
      body: JSON.stringify({ id, name, config: config || {} }),
    }),
  archiveClient: (id: string) =>
    fetchJSON<{ ok: boolean }>(`/api/clients/${encodeURIComponent(id)}`, { method: 'DELETE' }),
  getClientMCPServers: (clientId: string) =>
    fetchJSON<ClientMCPConfig[]>(`/api/clients/${encodeURIComponent(clientId)}/mcp-servers`),
  addClientMCPServer: (clientId: string, config: { server_name: string; package: string; env_vars?: Record<string, string>; args?: string[] }) =>
    fetchJSON<ClientMCPConfig>(`/api/clients/${encodeURIComponent(clientId)}/mcp-servers`, {
      method: 'POST',
      body: JSON.stringify(config),
    }),
  deleteClientMCPServer: (clientId: string, serverName: string) =>
    fetchJSON<{ ok: boolean }>(`/api/clients/${encodeURIComponent(clientId)}/mcp-servers/${encodeURIComponent(serverName)}`, {
      method: 'DELETE',
    }),
  getClientAgents: (clientId: string) =>
    fetchJSON<AgentSummary[]>(`/api/clients/${encodeURIComponent(clientId)}/agents`),

  // Environments
  getEnvironments: () =>
    fetchJSON<any[]>('/api/platform/environments'),
  getEnvironment: (id: string) =>
    fetchJSON<any>(`/api/platform/environments/${id}`),
  createEnvironment: (payload: { name: string; namespace?: string; cpu_request?: string; cpu_limit?: string; mem_request?: string; mem_limit?: string; metadata?: Record<string, unknown> }) =>
    fetchJSON<{ env_id: string; name: string; status: string }>('/api/platform/environments', {
      method: 'POST',
      body: JSON.stringify(payload),
    }),
  deleteEnvironment: (id: string) =>
    fetchJSON<{ ok: boolean }>(`/api/platform/environments/${id}`, { method: 'DELETE' }),
  getEnvironmentLogs: (id: string, tail = 500) =>
    fetchJSON<{ env_id: string; logs: string; pod_name: string; status: string }>(
      `/api/platform/environments/${id}/logs?tail=${tail}`
    ),
  getEnvironmentAgents: (id: string) =>
    fetchJSON<any[]>(`/api/platform/environments/${id}/agents`),
  deployAgentToEnvironment: (envId: string, payload: { name: string; chat_model?: string; provider?: string; system_prompt?: string; tools?: string[]; prompt?: string; loop_mode?: boolean; loop_interval?: number; metadata?: Record<string, unknown> }) =>
    fetchJSON<{ agent_id: string; name: string; environment_id: string }>(
      `/api/platform/environments/${envId}/agents`,
      { method: 'POST', body: JSON.stringify(payload) },
    ),
  removeAgentFromEnvironment: (envId: string, agentId: string) =>
    fetchJSON<{ ok: boolean }>(`/api/platform/environments/${envId}/agents/${agentId}`, { method: 'DELETE' }),
  getAgentLogsInEnv: (envId: string, agentId: string, tail = 500) =>
    fetchJSON<{ agent_id: string; logs: string; status: string }>(
      `/api/platform/environments/${envId}/agents/${agentId}/logs?tail=${tail}`
    ),

  getAgentActivity: (id: string) =>
    fetchJSON<{ agent_id: string; activity: Array<{ ts: string; event: string; detail: string }> }>(
      `/api/platform/agents/${id}/activity`
    ),
  getAgentLogs: (id: string, tail = 500) =>
    fetchJSON<{ agent_id: string; logs: string; pod_name: string; status: string }>(
      `/api/platform/agents/${id}/logs?tail=${tail}`
    ),
};
