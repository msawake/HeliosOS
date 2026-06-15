'use client';

import { useEffect, useState } from 'react';
import { api, isRunSettled, type RunHandle } from '@/lib/api';

// Mirrors `forgeos invoke --wait` / `forgeos runs watch`: poll GET /runs/{id}
// every 2s until the run settles (terminal or paused), then stop.

const POLL_MS = 2000;

export function useRun(runId: string | null, opts: { active?: boolean } = {}) {
  const active = opts.active ?? true;
  const [run, setRun] = useState<RunHandle | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [polling, setPolling] = useState(false);

  useEffect(() => {
    if (!runId || !active) {
      setPolling(false);
      return;
    }
    let cancelled = false;
    let timer: ReturnType<typeof setTimeout>;
    const controller = new AbortController();
    setPolling(true);
    setError(null);

    const tick = async () => {
      try {
        const r = await api.getRun(runId, controller.signal);
        if (cancelled) return;
        setRun(r);
        setError(null);
        if (isRunSettled(r.status)) {
          setPolling(false);
          return;
        }
      } catch (e) {
        if (cancelled) return;
        // Transient errors keep the poll alive; surface the last one.
        setError(e instanceof Error ? e.message : 'Failed to read run');
      }
      timer = setTimeout(tick, POLL_MS);
    };
    tick();

    return () => {
      cancelled = true;
      controller.abort();
      clearTimeout(timer);
    };
  }, [runId, active]);

  return { run, error, polling };
}
