'use client';

import { useCallback, useEffect, useState } from 'react';
import { api, type Agent, type Env } from '@/lib/api';
import { Card, CardHeader, CardTitle, CardContent } from '@/components/ui/card';
import { Button } from '@/components/ui/button';
import { Select } from '@/components/ui/input';
import { Badge } from '@/components/ui/badge';

/**
 * Attach/detach a reusable environment to this agent. When attached, the
 * agent's shell/file/git tools run inside a pod cloned from the template.
 */
export function AgentEnvironment({ agent, onChanged }: { agent: Agent; onChanged: () => void }) {
  const attachedId = (agent.metadata?._env_def_id as string | undefined) ?? '';
  const [envs, setEnvs] = useState<Env[] | null>(null);
  const [selected, setSelected] = useState<string>('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [status, setStatus] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      const data = await api.listEnvs();
      setEnvs(Array.isArray(data) ? data : []);
    } catch {
      setEnvs([]);
    }
  }, []);

  useEffect(() => {
    load();
  }, [load]);

  const attachedEnv = envs?.find((e) => e.env_def_id === attachedId) ?? null;

  const attach = async () => {
    if (!selected) {
      setError('Pick an environment to attach.');
      return;
    }
    setBusy(true);
    setError(null);
    setStatus(null);
    try {
      const res = await api.attachEnv(agent.agent_id, selected);
      setStatus(res?.status ?? null);
      if (res?.error) setError(res.error);
      onChanged();
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Attach failed');
    } finally {
      setBusy(false);
    }
  };

  const detach = async () => {
    setBusy(true);
    setError(null);
    try {
      await api.detachEnv(agent.agent_id);
      setStatus(null);
      onChanged();
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Detach failed');
    } finally {
      setBusy(false);
    }
  };

  return (
    <Card>
      <CardHeader>
        <CardTitle>Environment</CardTitle>
      </CardHeader>
      <CardContent className="space-y-3">
        {attachedId ? (
          <div className="flex flex-wrap items-center gap-3">
            <span className="text-[13px] text-secondary">
              Attached to{' '}
              <span className="font-medium text-primary">{attachedEnv?.name ?? attachedId}</span>
              {attachedEnv?.image ? (
                <span className="font-mono text-xs text-tertiary"> · {attachedEnv.image}</span>
              ) : null}
            </span>
            {status ? <Badge variant="outline">{status}</Badge> : null}
            <Button variant="secondary" size="sm" onClick={detach} disabled={busy}>
              {busy ? 'Detaching…' : 'Detach'}
            </Button>
          </div>
        ) : (
          <div className="flex flex-wrap items-center gap-3">
            <Select
              value={selected}
              onChange={(e) => setSelected(e.target.value)}
              className="max-w-xs"
              aria-label="Environment"
            >
              <option value="">Select an environment…</option>
              {(envs ?? []).map((e) => (
                <option key={e.env_def_id} value={e.env_def_id}>
                  {e.name} ({e.image})
                </option>
              ))}
            </Select>
            <Button size="sm" onClick={attach} disabled={busy || !selected}>
              {busy ? 'Attaching…' : 'Attach'}
            </Button>
          </div>
        )}
        <p className="text-xs text-tertiary">
          When attached, the agent&apos;s shell, file, and git tools run inside a pod cloned from this
          environment instead of on the platform host.
        </p>
        {error ? <p className="text-[13px] text-danger">{error}</p> : null}
      </CardContent>
    </Card>
  );
}
