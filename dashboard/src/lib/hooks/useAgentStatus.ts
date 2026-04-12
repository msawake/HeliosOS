'use client';

import { useEffect, useRef, useState } from 'react';

/**
 * Live agent status stream via the FastAPI /ws/agents WebSocket.
 *
 * The backend broadcasts a snapshot of all agents every 5 seconds:
 *   { timestamp, agents: [...], total, running }
 *
 * This hook keeps a React state map keyed by agent_id so components can
 * render live status badges without polling. It auto-reconnects on drop
 * with exponential backoff.
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

function wsUrl(path: string): string {
  if (typeof window === 'undefined') return '';
  // Env override: NEXT_PUBLIC_WS_URL=ws://localhost:5000 (dev) or wss://api.example.com (prod)
  const envBase = process.env.NEXT_PUBLIC_WS_URL;
  if (envBase) {
    return `${envBase.replace(/\/$/, '')}${path}`;
  }
  // Dev fallback: connect to Next.js host swapping http->ws. Next.js rewrites
  // don't proxy WebSocket upgrades, so this only works if the backend is on
  // the same host (e.g., production ingress). For dev, set NEXT_PUBLIC_WS_URL.
  const { protocol, hostname } = window.location;
  const wsProto = protocol === 'https:' ? 'wss:' : 'ws:';
  // Default dev backend port is 5000
  const port = process.env.NEXT_PUBLIC_API_PORT || '5000';
  return `${wsProto}//${hostname}:${port}${path}`;
}

export function useAgentStatus(): AgentStatusSnapshot {
  const [snapshot, setSnapshot] = useState<AgentStatusSnapshot>({
    timestamp: '',
    total: 0,
    running: 0,
    agents: [],
    connected: false,
  });
  const reconnectTimer = useRef<number | null>(null);
  const backoff = useRef<number>(1000);

  useEffect(() => {
    if (typeof window === 'undefined') return;
    let cancelled = false;
    let ws: WebSocket | null = null;

    const connect = () => {
      try {
        ws = new WebSocket(wsUrl('/ws/agents'));
      } catch {
        // Scheduled reconnect
        scheduleReconnect();
        return;
      }

      ws.onopen = () => {
        backoff.current = 1000;
        setSnapshot((prev) => ({ ...prev, connected: true }));
      };

      ws.onmessage = (event: MessageEvent) => {
        try {
          const data = JSON.parse(event.data);
          setSnapshot({
            timestamp: data.timestamp || new Date().toISOString(),
            total: data.total || (Array.isArray(data.agents) ? data.agents.length : 0),
            running: data.running || 0,
            agents: data.agents || [],
            connected: true,
          });
        } catch {
          // Ignore malformed frames
        }
      };

      ws.onerror = () => {
        // onclose will fire next
      };

      ws.onclose = () => {
        setSnapshot((prev) => ({ ...prev, connected: false }));
        if (!cancelled) scheduleReconnect();
      };
    };

    const scheduleReconnect = () => {
      if (cancelled) return;
      const delay = Math.min(backoff.current, 30_000);
      backoff.current = delay * 2;
      reconnectTimer.current = window.setTimeout(connect, delay);
    };

    connect();

    return () => {
      cancelled = true;
      if (reconnectTimer.current !== null) {
        window.clearTimeout(reconnectTimer.current);
      }
      if (ws) {
        try {
          ws.close();
        } catch {
          // ignore
        }
      }
    };
  }, []);

  return snapshot;
}
