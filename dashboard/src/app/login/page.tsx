'use client';

import { useState } from 'react';
import { useAuth } from '@/lib/auth';

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
    } catch (err: any) {
      setError(err.message || 'Login failed');
      setLoading(false);
    }
  }

  return (
    <div className="min-h-screen flex items-center justify-center bg-[#f7f7f8]">
      <form onSubmit={handleSubmit} className="w-full max-w-sm p-8 bg-white border border-[#e5e5e5] rounded-xl">
        <h1 className="text-2xl font-semibold text-[#0d0d0d] mb-2 text-center">ForgeOS</h1>
        <p className="text-gray-400 text-sm mb-6 text-center">
          Sign in to the multi-stack agent platform.
        </p>

        <label className="block text-sm text-gray-400 mb-1">Password</label>
        <input
          type="password"
          value={password}
          onChange={(e) => setPassword(e.target.value)}
          autoFocus
          className="w-full px-3 py-2 bg-[#f7f7f8] border border-[#d1d1d1] rounded-lg text-[#0d0d0d] text-sm placeholder-[#8e8ea0] mb-3"
          placeholder="Enter password"
        />

        {error && (
          <p className="text-red-400 text-xs mb-3">{error}</p>
        )}

        <button
          type="submit"
          disabled={loading || !password}
          className="w-full py-2 bg-[#10A37F] hover:bg-[#0d8c6d] disabled:opacity-50 text-white font-medium rounded-lg transition-colors"
        >
          {loading ? 'Signing in…' : 'Sign in'}
        </button>

        <p className="text-xs text-gray-600 mt-4 text-center">
          Default dev password: <code className="text-gray-500 bg-[#f7f7f8] px-1 py-0.5 rounded">forgeos</code>
          <br />
          Override via <code className="text-gray-500">FORGEOS_DEV_PASSWORD</code>
        </p>
      </form>
    </div>
  );
}
