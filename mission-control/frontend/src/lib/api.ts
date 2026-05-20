// Typed fetch wrappers for the Mission Control proxy endpoints.

export interface AgentProcess {
  name: string;
  pid?: string | number;
  phase: string;
  namespace?: string;
  tokens?: number;
  dollars?: number;
  tool_calls?: number;
  last_heartbeat?: string;
  last_error?: string;
  stack?: string;
  execution_type?: string;
}

export interface FleetResponse {
  agents?: AgentProcess[];
  summary?: {
    total?: number;
    running?: number;
    failed?: number;
    quarantined?: number;
  };
}

export interface Agent {
  name: string;
  stack?: string;
  execution_type?: string;
  namespace?: string;
  model?: string;
  tools?: string[];
  system_prompt?: string;
  metadata?: Record<string, unknown>;
}

export interface Approval {
  id: string;
  status?: string;
  priority?: string;
  from_agent?: string;
  agent_id?: string;
  question?: string;
  message?: string;
  body?: string;
  created_at?: string;
  timestamp?: string;
}

export interface AuditEvent {
  action?: string;
  event?: string;
  type?: string;
  actor?: string;
  agent_id?: string;
  resource_id?: string;
  outcome?: string;
  created_at?: string;
  timestamp?: string;
  details?: Record<string, unknown>;
}

export interface BillingCompany {
  company_id: string;
  active_agents: number;
  included_agents: number;
  overage_agents: number;
  total_tokens: number;
  total_cost_usd: number;
  total_tool_calls: number;
  running_agents: number;
  pricing: {
    estimated_monthly_eur: number;
    overage_per_agent_eur: number;
  };
  agents: Array<{
    name: string;
    namespace: string;
    phase: string;
    tokens: number;
    dollars: number;
  }>;
}

export interface BillingMetering {
  companies?: BillingCompany[];
  total_companies?: number;
  total_agents?: number;
  total_revenue_eur?: number;
  pricing_model?: { example_200_agents_eur?: number };
}

async function getJSON<T>(path: string): Promise<T | null> {
  try {
    const r = await fetch(path);
    return (await r.json()) as T;
  } catch {
    return null;
  }
}

async function postJSON<T>(path: string, body?: unknown): Promise<T | null> {
  try {
    const r = await fetch(path, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: body ? JSON.stringify(body) : undefined,
    });
    return (await r.json()) as T;
  } catch {
    return null;
  }
}

export const api = {
  fleet: () => getJSON<FleetResponse>("/api/platform/fleet"),
  agents: () => getJSON<Agent[] | { agents: Agent[] }>("/api/platform/agents"),
  approvals: () =>
    getJSON<Approval[] | { requests?: Approval[]; items?: Approval[] }>("/api/approvals"),
  audit: () =>
    getJSON<AuditEvent[] | { events?: AuditEvent[]; items?: AuditEvent[] }>("/api/audit"),
  adminEvents: () =>
    getJSON<AuditEvent[] | { events?: AuditEvent[]; items?: AuditEvent[] }>(
      "/api/admin/events",
    ),
  billing: () => getJSON<BillingMetering>("/api/billing/metering"),
  approve: (id: string) => postJSON(`/api/approvals/${id}/approve`),
  reject: (id: string) => postJSON(`/api/approvals/${id}/reject`),
  stop: (name: string) => postJSON(`/api/platform/agents/${name}/stop`),
  remove: (name: string) =>
    fetch(`/api/platform/agents/${name}`, { method: "DELETE" }),
};
