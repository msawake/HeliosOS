'use client';

import { useCallback, useEffect, useRef, useState } from 'react';
import { api, type LogEvent } from '@/lib/api';

// Mirrors `forgeos logs [--follow]`: fetch GET /agent-logs?agent_id=&limit=,
// dedupe on (ts, type, description, run_id), and (when following) re-poll every
// 2s, merging only the new events. Displayed newest-first.

const POLL_MS = 2000;

function sig(e: LogEvent): string {
  return `${e.ts ?? ''}|${e.type ?? ''}|${e.description ?? ''}|${e.run_id ?? ''}`;
}

export function useAgentLogs(
  agentId: string | null,
  opts: { tail?: number; follow?: boolean } = {}
) {
  const { tail = 200, follow = false } = opts;
  const [events, setEvents] = useState<LogEvent[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const seen = useRef<Set<string>>(new Set());
  const [nonce, setNonce] = useState(0);

  const refetch = useCallback(() => {
    seen.current = new Set();
    setEvents([]);
    setLoading(true);
    setNonce((n) => n + 1);
  }, []);

  useEffect(() => {
    if (!agentId) return;
    let cancelled = false;
    let timer: ReturnType<typeof setTimeout>;
    const controller = new AbortController();

    const tick = async () => {
      try {
        const { events: incoming } = await api.getAgentLogs(agentId, tail, controller.signal);
        if (cancelled) return;
        const fresh = (incoming ?? []).filter((e) => {
          const s = sig(e);
          if (seen.current.has(s)) return false;
          seen.current.add(s);
          return true;
        });
        if (fresh.length) {
          setEvents((prev) =>
            [...fresh, ...prev].sort((a, b) => (b.ts ?? '').localeCompare(a.ts ?? ''))
          );
        }
        setError(null);
      } catch (e) {
        if (cancelled) return;
        setError(e instanceof Error ? e.message : 'Failed to load logs');
      } finally {
        if (!cancelled) setLoading(false);
      }
      if (!cancelled && follow) timer = setTimeout(tick, POLL_MS);
    };
    tick();

    return () => {
      cancelled = true;
      controller.abort();
      clearTimeout(timer);
    };
  }, [agentId, tail, follow, nonce]);

  return { events, error, loading, refetch };
}
