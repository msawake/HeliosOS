/**
 * ForgeOS dashboard API client — one method per `forgeos` CLI capability,
 * typed to the same backend contract the Rust CLI speaks (see forgeos-cli
 * src/api.rs). The dashboard is a pure HTTP client of the platform API.
 *
 * Base URL:
 *   Browser → same-origin `/api/*` (next.config rewrites to the platform :5000).
 *   SSR     → NEXT_PUBLIC_API_URL or INTERNAL_API_URL, else local :5000.
 *
 * Auth: Bearer token or X-API-Key from sessionStorage (matches the CLI's two
 * schemes), plus `x-forgeos-caller: forgeos-dashboard` for audit attribution.
 */

function apiBase(): string {
  if (process.env.NEXT_PUBLIC_API_URL) {
    return process.env.NEXT_PUBLIC_API_URL.replace(/\/$/, '');
  }
  if (typeof window !== 'undefined') return '';
  const internal = process.env.INTERNAL_API_URL || '';
  if (internal) return internal.replace(/\/$/, '');
  return 'http://127.0.0.1:5000';
}

function authHeaders(): Record<string, string> {
  if (typeof window === 'undefined') return {};
  const token = sessionStorage.getItem('forgeos_token');
  if (token) return { Authorization: `Bearer ${token}` };
  const key = sessionStorage.getItem('forgeos_api_key');
  if (key) return { 'X-API-Key': key };
  return {};
}

export class ApiError extends Error {
  status: number;
  detail?: string;
  constructor(status: number, message: string, detail?: string) {
    super(message);
    this.name = 'ApiError';
    this.status = status;
    this.detail = detail;
  }
}

interface RequestOptions {
  method?: string;
  /** JSON body (mutually exclusive with `yaml`). */
  body?: unknown;
  /** Raw YAML body sent as text/yaml (deploy). */
  yaml?: string;
  query?: Record<string, string | undefined>;
  signal?: AbortSignal;
}

async function request<T>(path: string, opts: RequestOptions = {}): Promise<T> {
  const qs = opts.query
    ? '?' +
      new URLSearchParams(
        Object.entries(opts.query).filter(([, v]) => v != null && v !== '') as [string, string][]
      ).toString()
    : '';
  const headers: Record<string, string> = {
    'x-forgeos-caller': 'forgeos-dashboard',
    ...authHeaders(),
  };
  let body: BodyInit | undefined;
  if (opts.yaml != null) {
    headers['Content-Type'] = 'text/yaml';
    body = opts.yaml;
  } else if (opts.body !== undefined) {
    headers['Content-Type'] = 'application/json';
    body = JSON.stringify(opts.body);
  }

  const res = await fetch(`${apiBase()}${path}${qs}`, {
    method: opts.method ?? 'GET',
    headers,
    body,
    signal: opts.signal,
    cache: 'no-store',
  });

  if (!res.ok) {
    const text = await res.text().catch(() => '');
    let detail: string | undefined;
    try {
      detail = JSON.parse(text)?.detail;
    } catch {
      detail = text || undefined;
    }
    throw new ApiError(res.status, detail || `${res.status} ${res.statusText}`, detail);
  }

  if (res.status === 204) return undefined as T;
  const text = await res.text();
  return (text ? JSON.parse(text) : undefined) as T;
}

// ─── Domain types (fields per the CLI serde structs; most optional) ──────────

export interface Agent {
  agent_id: string;
  name: string;
  description?: string;
  namespace?: string;
  stack?: string;
  execution_type?: string;
  status?: string;
  schedule?: string;
  department?: string;
  ownership?: string;
  goal?: string;
  llm_config?: { chat_model?: string; reasoning_model?: string; provider?: string; endpoint?: string; api_key_ref?: string };
  tools?: string[];
  event_triggers?: string[];
  metadata?: Record<string, unknown>;
  system_prompt?: string;
  [key: string]: unknown;
}

export interface PendingApproval {
  request_id?: string;
  tool?: string;
  tool_use_id?: string;
  args?: Record<string, unknown>;
}

export interface RunHandle {
  run_id?: string;
  status?: string;
  result?: string;
  error?: string;
  suspend_reason?: string;
  pending?: PendingApproval[];
  warnings?: string[];
  simulated?: boolean;
}

export interface LogEvent {
  ts?: string;
  agent_id?: string;
  run_id?: string;
  type?: string;
  description?: string;
  details?: Record<string, unknown>;
}

/** SSE frames emitted by `/api/platform/agents/{id}/chat/stream`. */
export type ChatStreamEvent =
  | { type: 'session'; session_id: string }
  | { type: 'text_delta'; content: string }
  | { type: 'tool_call'; name: string; input?: Record<string, unknown> }
  | { type: 'tool_result'; name: string; result?: unknown }
  | {
      type: 'hitl_request';
      request_id: string;
      title?: string;
      description?: string;
      risk?: string;
      category?: string;
    }
  | { type: 'done'; tokens_used?: number; text?: string }
  | { type: 'error'; error: string };

export interface Approval {
  id?: string;
  request_id?: string;
  run_id?: string;
  continuation_id?: string;
  from_agent?: string;
  /** Legacy company HITL fields (company__request_approval / hitl.get_pending()). */
  requesting_agent?: string;
  department?: string;
  category?: string;
  title?: string;
  description?: string;
  risk_assessment?: string;
  context?: Record<string, unknown>;
  content?: {
    question?: string;
    kind?: string;
    context?: Record<string, unknown>;
    [key: string]: unknown;
  };
  [key: string]: unknown;
}

export interface McpServer {
  server_name: string;
  package: string;
  env_vars?: Record<string, string>;
  args?: string[];
}

const TERMINAL = new Set(['completed', 'failed', 'cancelled', 'error']);
const NON_TERMINAL = new Set(['running', 'queued', 'resuming']);

/** A run is settled when it isn't actively progressing (terminal or paused). */
export function isRunSettled(status?: string): boolean {
  const s = (status || '').toLowerCase();
  if (!s) return false;
  return TERMINAL.has(s) || (!NON_TERMINAL.has(s));
}
export function isRunTerminal(status?: string): boolean {
  return TERMINAL.has((status || '').toLowerCase());
}

// ─── API surface (one method ≈ one CLI command) ─────────────────────────────

export const api = {
  // health
  health: () => request<Record<string, unknown>>('/api/health'),

  // list / describe
  listAgents: (filters?: { stack?: string; execution_type?: string; ownership?: string }) =>
    request<Agent[]>('/api/platform/agents', { query: filters }),
  getAgent: (id: string) => request<Agent>(`/api/platform/agents/${encodeURIComponent(id)}`),

  // deploy (raw YAML, like `forgeos deploy`)
  deployYaml: (yaml: string) =>
    request<{ agent_id: string }>('/api/platform/agents/from-yaml', { method: 'POST', yaml }),

  // edit
  updateAgent: (id: string, body: Record<string, unknown>) =>
    request<Agent>(`/api/platform/agents/${encodeURIComponent(id)}`, { method: 'PUT', body }),
  // edit (raw YAML manifest — preserves the uploaded source verbatim)
  updateAgentYaml: (id: string, yamlText: string) =>
    request<Agent>(`/api/platform/agents/${encodeURIComponent(id)}/from-yaml`, {
      method: 'PUT',
      yaml: yamlText,
    }),

  // invoke + run watch
  invoke: (
    id: string,
    opts: { prompt: string; async?: boolean; sessionId?: string; context?: Record<string, unknown> }
  ) =>
    request<RunHandle>(`/api/platform/agents/${encodeURIComponent(id)}/invoke`, {
      method: 'POST',
      query: opts.async ? { async_mode: 'true' } : undefined,
      body: {
        prompt: opts.prompt,
        context: opts.context ?? {},
        ...(opts.sessionId ? { session_id: opts.sessionId } : {}),
      },
    }),
  getRun: (runId: string, signal?: AbortSignal) =>
    request<RunHandle>(`/api/platform/runs/${encodeURIComponent(runId)}`, { signal }),

  // logs
  getAgentLogs: (id: string, limit = 200, signal?: AbortSignal) =>
    request<{ events: LogEvent[] }>('/api/platform/agent-logs', {
      query: { agent_id: id, limit: String(limit) },
      signal,
    }),

  // lifecycle
  stopAgent: (id: string) =>
    request<{ ok?: boolean }>(`/api/platform/agents/${encodeURIComponent(id)}/stop`, {
      method: 'POST',
      body: {},
    }),
  undeployAgent: (id: string) =>
    request<{ removed?: boolean }>(`/api/platform/agents/${encodeURIComponent(id)}`, {
      method: 'DELETE',
    }),

  // approvals + answer
  listApprovals: (fromAgent?: string) =>
    request<Approval[]>('/api/approvals', { query: fromAgent ? { from_agent: fromAgent } : undefined }),
  approve: (requestId: string, notes?: string) =>
    request<unknown>(`/api/approvals/${encodeURIComponent(requestId)}/approve`, {
      method: 'POST',
      body: notes ? { notes } : {},
    }),
  reject: (requestId: string, reason?: string) =>
    request<unknown>(`/api/approvals/${encodeURIComponent(requestId)}/reject`, {
      method: 'POST',
      body: reason ? { reason } : {},
    }),
  answer: (
    requestId: string,
    opts: { text?: string; value?: string; respondedBy?: string }
  ) =>
    request<unknown>(`/api/a2h/requests/${encodeURIComponent(requestId)}/respond`, {
      method: 'POST',
      body: {
        response: { text: opts.text, value: opts.value },
        responded_by: opts.respondedBy ?? 'dashboard',
        channel: 'dashboard',
      },
    }),

  // chat (A2H protocol)
  openChat: (payload: {
    agent_pid?: string;
    agent_namespace?: string;
    agent_name?: string;
    human_name?: string;
    human_namespace?: string;
    topic?: string;
  }) => request<{ id: string }>('/api/a2h/v1/chats', { method: 'POST', body: payload }),
  postChatMessage: (
    chatId: string,
    payload: { role: 'human' | 'agent'; sender: string; content: string; client_drives?: boolean }
  ) =>
    request<unknown>(`/api/a2h/v1/chats/${encodeURIComponent(chatId)}/messages`, {
      method: 'POST',
      body: payload,
    }),
  closeChat: (chatId: string, reason = 'user exit') =>
    request<unknown>(`/api/a2h/v1/chats/${encodeURIComponent(chatId)}/close`, {
      method: 'POST',
      body: { reason },
    }),

  /**
   * Stream a multi-turn chat turn over SSE. Invokes `onEvent` for every frame
   * (text deltas, tool_call / tool_result, hitl_request, done, error) as it
   * arrives. Resolves when the stream closes; rejects on a non-2xx response.
   * EventSource can't POST, so we read the fetch body stream by hand.
   */
  async streamChat(
    agentId: string,
    opts: { message: string; sessionId?: string; signal?: AbortSignal },
    onEvent: (ev: ChatStreamEvent) => void
  ): Promise<void> {
    const res = await fetch(
      `${apiBase()}/api/platform/agents/${encodeURIComponent(agentId)}/chat/stream`,
      {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          'x-forgeos-caller': 'forgeos-dashboard',
          ...authHeaders(),
        },
        body: JSON.stringify({
          message: opts.message,
          ...(opts.sessionId ? { session_id: opts.sessionId } : {}),
        }),
        signal: opts.signal,
        cache: 'no-store',
      }
    );

    if (!res.ok || !res.body) {
      const text = await res.text().catch(() => '');
      let detail: string | undefined;
      try {
        detail = JSON.parse(text)?.detail;
      } catch {
        detail = text || undefined;
      }
      throw new ApiError(res.status, detail || `${res.status} ${res.statusText}`, detail);
    }

    const reader = res.body.getReader();
    const decoder = new TextDecoder();
    let buf = '';
    for (;;) {
      const { value, done } = await reader.read();
      if (done) break;
      buf += decoder.decode(value, { stream: true });
      // SSE frames are separated by a blank line.
      let sep: number;
      while ((sep = buf.indexOf('\n\n')) >= 0) {
        const frame = buf.slice(0, sep);
        buf = buf.slice(sep + 2);
        const data = frame
          .split('\n')
          .filter((l) => l.startsWith('data:'))
          .map((l) => l.slice(5).trimStart())
          .join('\n');
        if (!data) continue;
        try {
          onEvent(JSON.parse(data) as ChatStreamEvent);
        } catch {
          // Skip malformed frames rather than tearing down the stream.
        }
      }
    }
  },

  // mcp servers
  listMcp: () => request<McpServer[]>('/api/platform/mcp/servers'),
  registerMcp: (payload: {
    server_name: string;
    package: string;
    env_vars?: Record<string, string>;
    args?: string[];
  }) =>
    request<{ connected?: boolean; tools_discovered?: number; detail?: string }>(
      '/api/platform/mcp/servers',
      { method: 'POST', body: payload },
    ),
  registerUserMcp: (
    user: string,
    serverName: string,
    payload: { package: string; env_vars?: Record<string, string>; secrets?: Record<string, string>; args?: string[] }
  ) =>
    request<unknown>(`/api/users/${encodeURIComponent(user)}/mcp/${encodeURIComponent(serverName)}`, {
      method: 'POST',
      body: payload,
    }),
  removeMcp: (serverName: string) =>
    request<unknown>(`/api/platform/mcp/servers/${encodeURIComponent(serverName)}`, {
      method: 'DELETE',
    }),

  // credentials (write-only)
  putGithubCred: (payload: { pat: string; user_id?: string }) =>
    request<unknown>('/api/credentials/github', {
      method: 'POST',
      body: { pat: payload.pat, user_id: payload.user_id ?? 'default' },
    }),
  putJiraCred: (payload: { url: string; email: string; token: string; user_id?: string }) =>
    request<unknown>('/api/credentials/jira', {
      method: 'POST',
      body: { ...payload, user_id: payload.user_id ?? 'default' },
    }),
};
