'use client';

import { useState } from 'react';
import { useAuth } from '@/lib/auth';
import { api, ApiError } from '@/lib/api';
import { LogoMark } from '@/components/brand/logo';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Field, FieldLabel } from '@/components/ui/field';

export default function LoginPage() {
  const { login } = useAuth();
  const [email, setEmail] = useState('');
  const [password, setPassword] = useState('');
  const [devMode, setDevMode] = useState(false);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    setLoading(true);
    setError('');
    try {
      let token: string;
      if (devMode) {
        // Break-glass dev login (password only → /api/auth/token).
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
        token = (await res.json()).token;
      } else {
        token = (await api.login(email, password)).token;
      }
      await login(token);
    } catch (err) {
      const msg =
        err instanceof ApiError && err.status === 401
          ? 'Invalid email or password'
          : err instanceof Error
            ? err.message
            : 'Login failed';
      setError(msg);
      setLoading(false);
    }
  }

  const canSubmit = devMode ? !!password : !!email && !!password;

  return (
    <div className="flex min-h-screen items-center justify-center bg-page px-4">
      <div className="w-full max-w-sm">
        <div className="mb-8 flex flex-col items-center text-center">
          <span className="mb-4 flex h-16 w-16 items-center justify-center rounded-full bg-ink">
            <LogoMark className="h-9 w-9 text-paper" />
          </span>
          <h1 className="font-display text-3xl font-semibold tracking-[0.04em] text-primary">Helios OS</h1>
          <p className="mt-1 text-sm text-tertiary">The agentic harness.</p>
        </div>

        <form onSubmit={handleSubmit} className="rounded-xl border border-edge bg-surface p-6 shadow-sm">
          {!devMode ? (
            <Field className="mb-3">
              <FieldLabel htmlFor="email">Email</FieldLabel>
              <Input
                id="email"
                type="email"
                value={email}
                onChange={(e) => setEmail(e.target.value)}
                autoFocus
                placeholder="you@company.com"
              />
            </Field>
          ) : null}

          <Field>
            <FieldLabel htmlFor="password">Password</FieldLabel>
            <Input
              id="password"
              type="password"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              autoFocus={devMode}
              placeholder="••••••••"
            />
          </Field>

          {error ? <p className="mt-3 text-xs text-danger">{error}</p> : null}

          <Button type="submit" disabled={loading || !canSubmit} className="mt-4 w-full">
            {loading ? 'Signing in…' : 'Sign in'}
          </Button>

          <button
            type="button"
            onClick={() => {
              setDevMode((d) => !d);
              setError('');
            }}
            className="mt-4 w-full text-center text-xs text-tertiary hover:text-secondary"
          >
            {devMode ? '← Sign in with email' : 'Use dev password (break-glass)'}
          </button>
        </form>
      </div>
    </div>
  );
}
