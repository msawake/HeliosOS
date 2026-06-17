'use client';

import { useEffect, useState } from 'react';
import { SignOut } from '@phosphor-icons/react';
import { cn } from '@/lib/utils';
import { useAuth } from '@/lib/auth';
import { useAgentStatus } from '@/lib/hooks/useAgentStatus';

type HealthState = 'ok' | 'degraded' | 'down' | 'unknown';

/** Mirrors `forgeos health` — polls the unauthenticated /api/health endpoint. */
function HealthBadge() {
  const [state, setState] = useState<HealthState>('unknown');

  useEffect(() => {
    let cancelled = false;
    const ping = async () => {
      try {
        const res = await fetch('/api/health', { cache: 'no-store' });
        if (cancelled) return;
        if (!res.ok) {
          setState('down');
          return;
        }
        const body = await res.json().catch(() => ({}));
        const status = String(body.status ?? 'ok').toLowerCase();
        setState(status === 'ok' || status === 'healthy' ? 'ok' : 'degraded');
      } catch {
        if (!cancelled) setState('down');
      }
    };
    ping();
    const id = setInterval(ping, 15_000);
    return () => {
      cancelled = true;
      clearInterval(id);
    };
  }, []);

  const dot =
    state === 'ok'
      ? 'bg-success'
      : state === 'degraded'
        ? 'bg-warning'
        : state === 'down'
          ? 'bg-danger'
          : 'bg-muted';
  const label =
    state === 'ok'
      ? 'Healthy'
      : state === 'degraded'
        ? 'Degraded'
        : state === 'down'
          ? 'Unreachable'
          : 'Checking…';

  return (
    <span className="inline-flex items-center gap-1.5 rounded-full border border-edge bg-surface px-2.5 py-1 text-xs text-secondary">
      <span className={cn('h-1.5 w-1.5 rounded-full', dot)} />
      {label}
    </span>
  );
}

export function Topbar() {
  const { isAuthenticated, logout } = useAuth();
  const live = useAgentStatus();

  return (
    <header className="flex h-(--topbar-height) shrink-0 items-center justify-end gap-3 border-b border-edge bg-page/80 px-8 backdrop-blur-sm">
      <span
        className="inline-flex items-center gap-1.5 text-xs text-tertiary"
        title={live.connected ? `${live.running} running of ${live.total} agents` : 'Loading agent status…'}
      >
        <span
          className={cn('h-1.5 w-1.5 rounded-full', live.connected ? 'bg-accent' : 'bg-muted')}
        />
        {live.connected
          ? live.running > 0
            ? `${live.running}/${live.total} running`
            : `${live.total} agents`
          : '…'}
      </span>

      <HealthBadge />

      {isAuthenticated ? (
        <button
          onClick={logout}
          className="inline-flex items-center gap-1.5 rounded-md px-2 py-1 text-xs text-tertiary transition-colors hover:bg-surface-hover hover:text-primary"
        >
          <SignOut className="h-4 w-4" aria-hidden />
          Sign out
        </button>
      ) : null}
    </header>
  );
}
