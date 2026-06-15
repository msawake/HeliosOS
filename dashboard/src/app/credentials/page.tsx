'use client';

import { useState } from 'react';
import Link from 'next/link';
import { GithubLogo, Info, PlugsConnected } from '@phosphor-icons/react';
import { api } from '@/lib/api';
import { PageHeader } from '@/components/layout/PageHeader';
import { Card, CardContent, CardHeader, CardTitle, CardDescription } from '@/components/ui/card';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Field, FieldLabel } from '@/components/ui/field';

type SaveState = 'idle' | 'saving' | 'saved' | 'error';

function GithubCard() {
  const [pat, setPat] = useState('');
  const [userId, setUserId] = useState('default');
  const [state, setState] = useState<SaveState>('idle');
  const [error, setError] = useState<string | null>(null);

  const save = async () => {
    if (!pat.trim()) return;
    setState('saving');
    setError(null);
    try {
      await api.putGithubCred({ pat, user_id: userId || 'default' });
      setState('saved');
      setPat('');
      setTimeout(() => setState('idle'), 2500);
    } catch (e) {
      setState('error');
      setError(e instanceof Error ? e.message : 'Failed to store credential');
    }
  };

  return (
    <Card>
      <CardHeader>
        <CardTitle className="flex items-center gap-2">
          <GithubLogo className="h-4 w-4" aria-hidden />
          GitHub
        </CardTitle>
        <CardDescription>Personal access token for the git and gh dev tools.</CardDescription>
      </CardHeader>
      <CardContent className="space-y-3">
        <Field>
          <FieldLabel htmlFor="gh-user">User id</FieldLabel>
          <Input id="gh-user" value={userId} onChange={(e) => setUserId(e.target.value)} />
        </Field>
        <Field>
          <FieldLabel htmlFor="gh-pat">Personal access token</FieldLabel>
          <Input
            id="gh-pat"
            type="password"
            value={pat}
            onChange={(e) => setPat(e.target.value)}
            placeholder="ghp_…"
            className="font-mono"
          />
        </Field>
        {error ? <p className="text-[13px] text-danger">{error}</p> : null}
        <div className="flex items-center gap-3">
          <Button onClick={save} disabled={state === 'saving' || !pat.trim()}>
            {state === 'saving' ? 'Storing…' : 'Store token'}
          </Button>
          {state === 'saved' ? <span className="text-[13px] text-success">Stored.</span> : null}
        </div>
      </CardContent>
    </Card>
  );
}

export default function CredentialsPage() {
  return (
    <div>
      <PageHeader
        title="Credentials"
        description="Per-user secrets agents use at runtime. Write-only — values are never read back."
      />
      <div className="mb-4 flex items-start gap-2 rounded-md border border-edge bg-inset px-4 py-3 text-[13px] text-tertiary">
        <Info className="mt-0.5 h-4 w-4 shrink-0" aria-hidden />
        Stored in the platform secret store and injected per invocation. The dashboard cannot display
        existing values.
      </div>
      <div className="grid grid-cols-1 gap-6 md:grid-cols-2">
        <GithubCard />

        {/* Jira, Atlassian, and other tool integrations connect as MCP servers,
            not standalone credentials — their tokens travel as the server's env/secrets. */}
        <Card>
          <CardHeader>
            <CardTitle className="flex items-center gap-2">
              <PlugsConnected className="h-4 w-4" aria-hidden />
              Jira & other integrations
            </CardTitle>
            <CardDescription>Connected as MCP servers, not standalone credentials.</CardDescription>
          </CardHeader>
          <CardContent className="space-y-3">
            <p className="text-[13px] text-secondary">
              Jira, Atlassian, and similar tools are bound by registering their MCP server — the site URL,
              email, and API token travel as that server&apos;s env vars and secrets.
            </p>
            <Button variant="secondary" asChild>
              <Link href="/mcp">
                <PlugsConnected className="h-4 w-4" aria-hidden />
                Go to MCP servers
              </Link>
            </Button>
          </CardContent>
        </Card>
      </div>
    </div>
  );
}
