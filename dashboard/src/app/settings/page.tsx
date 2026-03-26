'use client';

import { useState } from 'react';
import { STACKS, STACK_LABELS } from '@/lib/utils';

export default function SettingsPage() {
  const [keys, setKeys] = useState({
    anthropic: '',
    openai: '',
    google: '',
  });

  return (
    <div className="max-w-2xl">
      <h1 className="text-2xl font-bold mb-6">Settings</h1>

      <div className="card mb-6">
        <h2 className="font-semibold mb-4">LLM API Keys</h2>
        <div className="space-y-4">
          {(['anthropic', 'openai', 'google'] as const).map((provider) => (
            <div key={provider}>
              <label className="block text-sm font-medium text-gray-700 mb-1">
                {provider.charAt(0).toUpperCase() + provider.slice(1)} API Key
              </label>
              <input
                type="password"
                value={keys[provider]}
                onChange={(e) => setKeys((prev) => ({ ...prev, [provider]: e.target.value }))}
                className="w-full rounded-lg border-gray-300 text-sm"
                placeholder={`Enter ${provider} API key`}
              />
            </div>
          ))}
        </div>
        <button className="mt-4 px-4 py-2 bg-brand-600 text-white rounded-lg text-sm font-medium">
          Save Keys
        </button>
      </div>

      <div className="card mb-6">
        <h2 className="font-semibold mb-4">Registered Stacks</h2>
        <div className="space-y-2">
          {STACKS.map((s) => (
            <div key={s} className="flex items-center justify-between py-2 border-b border-gray-100 last:border-0">
              <div className="flex items-center gap-2">
                <span className={`badge badge-${s}`}>{STACK_LABELS[s]}</span>
              </div>
              <span className="text-xs text-green-600 font-medium">Active</span>
            </div>
          ))}
        </div>
      </div>

      <div className="card">
        <h2 className="font-semibold mb-4">Platform Info</h2>
        <div className="text-sm space-y-1 text-gray-600">
          <p><span className="text-gray-400">Version:</span> ForgeOS v3.0</p>
          <p><span className="text-gray-400">Stacks:</span> 4 (ForgeOS, CrewAI, ADK, OpenClaw)</p>
          <p><span className="text-gray-400">Exec Types:</span> 5 (always-on, scheduled, event-driven, reflex, autonomous)</p>
          <p><span className="text-gray-400">Dashboard:</span> Next.js 15 + Tailwind</p>
          <p><span className="text-gray-400">API:</span> Flask (port 5000)</p>
        </div>
      </div>
    </div>
  );
}
