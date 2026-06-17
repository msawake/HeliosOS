'use client';

import { useEffect, useRef, useState } from 'react';
import { api } from '@/lib/api';

/**
 * Live agent status via authenticated REST polling of /api/platform/agents.
 *
 * The platform also exposes a /ws/agents WebSocket, but browsers can't attach
 * the bearer/API-key auth header to a WebSocket and the dashboard origin
 * doesn't proxy WS upgrades — so on the hosted deployment it never connects.
 * Polling the same data the agents page reads is reliable and auth-aware.
 *
 * `connected` reflects whether the last poll succeeded (so the Topbar shows a
 * real count instead of a misleading "Offline").
 */

export interface LiveAgentStatus {
  agent_id?: string;
  name?: string;
  status?: string;
  [key: string]: unknown;
}

export interface AgentStatusSnapshot {
  timestamp: string;
  total: number;
  running: number;
  agents: LiveAgentStatus[];
  connected: boolean;
}

const RUNNING_STATES = new Set(['running', 'active', 'busy', 'processing', 'executing']);

export function useAgentStatus(pollMs = 10_000): AgentStatusSnapshot {
  const [snapshot, setSnapshot] = useState<AgentStatusSnapshot>({
    timestamp: '',
    total: 0,
    running: 0,
    agents: [],
    connected: false,
  });
  const timer = useRef<number | null>(null);

  useEffect(() => {
    let cancelled = false;

    const poll = async () => {
      try {
        const data = await api.listAgents();
        if (cancelled) return;
        const list = Array.isArray(data) ? data : [];
        setSnapshot({
          timestamp: new Date().toISOString(),
          total: list.length,
          running: list.filter((a) => RUNNING_STATES.has(String(a.status ?? '').toLowerCase())).length,
          agents: list.map((a) => ({ agent_id: a.agent_id, name: a.name, status: a.status })),
          connected: true,
        });
      } catch {
        if (!cancelled) setSnapshot((prev) => ({ ...prev, connected: false }));
      } finally {
        if (!cancelled) timer.current = window.setTimeout(poll, pollMs);
      }
    };

    poll();
    return () => {
      cancelled = true;
      if (timer.current !== null) window.clearTimeout(timer.current);
    };
  }, [pollMs]);

  return snapshot;
}
