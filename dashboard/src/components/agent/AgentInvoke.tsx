'use client';

import { useState } from 'react';
import { Play } from '@phosphor-icons/react';
import { api, type RunHandle } from '@/lib/api';
import { useRun } from '@/lib/hooks/useRun';
import { Button } from '@/components/ui/button';
import { Textarea } from '@/components/ui/input';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import { RunPanel } from '@/components/RunPanel';

export function AgentInvoke({ agentId }: { agentId: string }) {
  const [prompt, setPrompt] = useState('');
  const [submitting, setSubmitting] = useState(false);
  const [launchError, setLaunchError] = useState<string | null>(null);
  // Initial handle from the invoke response; the poller takes over once we
  // have a run_id (mirrors `invoke` then `runs watch`).
  const [handle, setHandle] = useState<RunHandle | null>(null);
  const [runId, setRunId] = useState<string | null>(null);

  const { run: liveRun, polling, error: pollError } = useRun(runId);
  const run = liveRun ?? handle;

  const launch = async () => {
    if (!prompt.trim()) return;
    setSubmitting(true);
    setLaunchError(null);
    setHandle(null);
    setRunId(null);
    try {
      const res = await api.invoke(agentId, { prompt, async: true });
      setHandle(res);
      if (res.run_id) setRunId(res.run_id);
    } catch (e) {
      setLaunchError(e instanceof Error ? e.message : 'Invoke failed');
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <div className="space-y-6">
      <Card>
        <CardHeader>
          <CardTitle>Invoke</CardTitle>
        </CardHeader>
        <CardContent className="space-y-3">
          <Textarea
            value={prompt}
            onChange={(e) => setPrompt(e.target.value)}
            placeholder="Prompt the agent…"
            className="min-h-28 font-mono text-[13px]"
            onKeyDown={(e) => {
              if ((e.metaKey || e.ctrlKey) && e.key === 'Enter') launch();
            }}
          />
          <div className="flex items-center gap-3">
            <Button onClick={launch} disabled={submitting || !prompt.trim()}>
              <Play className="h-4 w-4" aria-hidden />
              {submitting ? 'Starting…' : 'Run'}
            </Button>
            <span className="text-xs text-tertiary">⌘/Ctrl + Enter</span>
          </div>
          {launchError ? <p className="text-[13px] text-danger">{launchError}</p> : null}
        </CardContent>
      </Card>

      {(run || polling) && (
        <Card>
          <CardHeader>
            <CardTitle>Run</CardTitle>
          </CardHeader>
          <CardContent>
            <RunPanel run={run} polling={polling} error={pollError} />
          </CardContent>
        </Card>
      )}
    </div>
  );
}
