'use client';

import { useState } from 'react';
import { useAuth } from '@/lib/auth';
import { LogoMark } from '@/components/brand/logo';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Field, FieldLabel } from '@/components/ui/field';

export default function LoginPage() {
  const { login } = useAuth();
  const [password, setPassword] = useState('');
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    setLoading(true);
    setError('');
    try {
      const res = await fetch('/api/auth/token', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ password }),
      });
      if (!res.ok) {
        if (res.status === 401) throw new Error('Invalid password');
        if (res.status === 403) throw new Error('Dev login disabled on this deployment');
        throw new Error(`Login failed (${res.status})`);
      }
      const data = await res.json();
      await login(data.token);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Login failed');
      setLoading(false);
    }
  }

  return (
    <div className="flex min-h-screen items-center justify-center bg-page px-4">
      <div className="w-full max-w-sm">
        <div className="mb-8 flex flex-col items-center text-center">
          <span className="mb-4 flex h-16 w-16 items-center justify-center rounded-full bg-ink">
            <LogoMark className="h-9 w-9 text-paper" />
          </span>
          <h1 className="font-display text-3xl font-semibold tracking-[0.04em] text-primary">ForgeOS</h1>
          <p className="mt-1 text-sm text-tertiary">The agentic harness.</p>
        </div>

        <form
          onSubmit={handleSubmit}
          className="rounded-xl border border-edge bg-surface p-6 shadow-sm"
        >
          <Field>
            <FieldLabel htmlFor="password">Password</FieldLabel>
            <Input
              id="password"
              type="password"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              autoFocus
              placeholder="Enter password"
            />
          </Field>

          {error ? <p className="mt-3 text-xs text-danger">{error}</p> : null}

          <Button type="submit" disabled={loading || !password} className="mt-4 w-full">
            {loading ? 'Signing in…' : 'Sign in'}
          </Button>

          <p className="mt-4 text-center text-xs text-tertiary">
            Default dev password{' '}
            <code className="rounded bg-inset px-1 py-0.5 font-mono text-tertiary">forgeos</code>, override
            via <code className="font-mono">FORGEOS_DEV_PASSWORD</code>.
          </p>
        </form>
      </div>
    </div>
  );
}
