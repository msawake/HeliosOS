const API = process.env.FORGEOS_API_URL ?? 'http://localhost:5000';

async function post(path: string, body: unknown): Promise<unknown> {
  const res = await fetch(`${API}${path}`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  });
  if (!res.ok) throw new Error(`POST ${path} → ${res.status}: ${await res.text()}`);
  return res.json();
}

async function del(path: string): Promise<void> {
  await fetch(`${API}${path}`, { method: 'DELETE' });
}

export async function seedAgent(opts: {
  name: string;
  stack?: string;
  execution_type?: string;
  description?: string;
  system_prompt?: string;
}): Promise<{ agentId: string }> {
  const data = (await post('/api/platform/agents', {
    name: `__e2e__${opts.name}`,
    stack: opts.stack ?? 'forgeos',
    execution_type: opts.execution_type ?? 'reflex',
    description: opts.description ?? 'E2E test agent',
    system_prompt: opts.system_prompt ?? 'You are a test agent for automated QA.',
  })) as { agent_id: string };
  return { agentId: data.agent_id };
}

export async function deleteAgent(agentId: string): Promise<void> {
  await del(`/api/platform/agents/${agentId}`);
}

export async function seedClient(id: string, name: string): Promise<void> {
  const res = await fetch(`${API}/api/clients`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ id: `__e2e__${id}`, name: `__e2e__ ${name}` }),
  });
  if (!res.ok && res.status !== 409) {
    throw new Error(`seedClient → ${res.status}: ${await res.text()}`);
  }
}

export async function deleteClient(id: string): Promise<void> {
  await del(`/api/clients/__e2e__${id}`);
}

export async function seedA2HRequest(opts: {
  title: string;
  agent: string;
  category?: string;
  sla_hours?: number;
}): Promise<{ requestId: string }> {
  const data = (await post('/api/a2h/requests', {
    title: `__e2e__${opts.title}`,
    agent: opts.agent,
    category: opts.category ?? 'approval',
    sla_hours: opts.sla_hours ?? 24,
    description: 'E2E test approval request',
  })) as { request_id: string };
  return { requestId: data.request_id };
}

export async function deleteAllE2EEntities(): Promise<void> {
  const agentsRes = await fetch(`${API}/api/platform/agents`);
  if (agentsRes.ok) {
    const agents = (await agentsRes.json()) as Array<{ name: string; agent_id: string }>;
    for (const agent of agents) {
      if (agent.name?.startsWith('__e2e__')) await del(`/api/platform/agents/${agent.agent_id}`);
    }
  }
  const clientsRes = await fetch(`${API}/api/clients`);
  if (clientsRes.ok) {
    const clients = (await clientsRes.json()) as Array<{ id: string; name: string }>;
    for (const c of clients) {
      if (c.id?.startsWith('__e2e__') || c.name?.startsWith('__e2e__')) {
        await del(`/api/clients/${c.id}`);
      }
    }
  }
}
