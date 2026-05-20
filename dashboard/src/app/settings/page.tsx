'use client';

import { useEffect, useState } from 'react';
import { api } from '@/lib/api';
import { STACKS, STACK_LABELS } from '@/lib/utils';

interface ProviderStatus {
  configured: boolean;
  client_initialized: boolean;
  env_var: string;
  sdk_installed?: boolean;
}

export default function SettingsPage() {
  const [providers, setProviders] = useState<Record<string, ProviderStatus>>({});
  const [flags, setFlags] = useState<Record<string, boolean>>({});
  const [available, setAvailable] = useState<string[]>([]);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    api.getProviderStatus()
      .then((data) => {
        setProviders(data.providers || {});
        setFlags(data.feature_flags || {});
        setAvailable(data.available_providers || []);
      })
      .catch(() => {})
      .finally(() => setLoading(false));
  }, []);

  return (
    <div className="max-w-2xl">
      <h1 className="text-2xl font-semibold text-[#0d0d0d] mb-2">Settings</h1>
      <p className="text-sm text-gray-400 mb-6">
        Provider and feature-flag status for this deployment. Values are configured via
        environment variables or Kubernetes secrets — not editable from the browser.
      </p>

      <div className="bg-white border border-[#e5e5e5] rounded-xl p-5 mb-6">
        <h2 className="text-[#0d0d0d] font-semibold mb-4">LLM Providers</h2>
        {loading ? (
          <p className="text-gray-500 text-sm">Loading…</p>
        ) : Object.keys(providers).length === 0 ? (
          <p className="text-gray-500 text-sm">No provider status available.</p>
        ) : (
          <div className="space-y-3">
            {Object.entries(providers).map(([name, info]) => (
              <div key={name} className="flex items-center justify-between border-b border-[#e5e5e5] last:border-0 pb-3 last:pb-0">
                <div>
                  <p className="text-[#0d0d0d] font-medium capitalize">{name}</p>
                  <p className="text-xs text-gray-500 mt-0.5 font-mono">
                    {info.env_var}
                    {info.sdk_installed === false ? ' • SDK not installed' : ''}
                  </p>
                </div>
                <div className="flex items-center gap-2">
                  {info.client_initialized ? (
                    <span className="text-xs px-2 py-1 rounded bg-emerald-50 text-emerald-700 border border-emerald-200 font-medium">
                      Active
                    </span>
                  ) : info.configured ? (
                    <span className="text-xs px-2 py-1 rounded bg-amber-50 text-amber-700 border border-amber-200 font-medium">
                      Key set
                    </span>
                  ) : (
                    <span className="text-xs px-2 py-1 rounded bg-gray-100 text-gray-500 border border-gray-200">
                      Not configured
                    </span>
                  )}
                </div>
              </div>
            ))}
          </div>
        )}
        <p className="text-xs text-gray-500 mt-4">
          To enable a provider, set the environment variable (see{' '}
          <code className="text-xs bg-[#f7f7f8] px-1 py-0.5 rounded">.env.example</code>) and restart the backend.
        </p>
      </div>

      <div className="bg-white border border-[#e5e5e5] rounded-xl p-5 mb-6">
        <h2 className="text-[#0d0d0d] font-semibold mb-4">Feature Flags</h2>
        <div className="space-y-2">
          {Object.entries(flags).map(([name, enabled]) => (
            <div key={name} className="flex items-center justify-between text-sm">
              <code className="text-gray-400">FORGEOS_ENABLE_{name.toUpperCase()}</code>
              <span className={`text-xs px-2 py-0.5 rounded ${
                enabled ? 'bg-emerald-50 text-emerald-700 border border-emerald-200' : 'bg-gray-100 text-gray-500 border border-gray-200'
              }`}>
                {enabled ? 'ON' : 'OFF'}
              </span>
            </div>
          ))}
        </div>
      </div>

      <div className="bg-white border border-[#e5e5e5] rounded-xl p-5 mb-6">
        <h2 className="text-[#0d0d0d] font-semibold mb-4">Registered Stacks</h2>
        <div className="space-y-2">
          {STACKS.map((s) => (
            <div key={s} className="flex items-center justify-between text-sm py-1">
              <span className="text-[#0d0d0d]">{STACK_LABELS[s]}</span>
              <span className="text-xs text-emerald-700 font-medium">Active</span>
            </div>
          ))}
        </div>
      </div>

      <div className="bg-white border border-[#e5e5e5] rounded-xl p-5">
        <h2 className="text-[#0d0d0d] font-semibold mb-4">Platform Info</h2>
        <div className="text-sm space-y-1 text-gray-400">
          <p><span className="text-gray-600">Version:</span> ForgeOS v3.1</p>
          <p><span className="text-gray-600">Stacks:</span> 4 (ForgeOS, CrewAI, ADK, OpenClaw)</p>
          <p><span className="text-gray-600">Execution types:</span> 5 (always-on, scheduled, event-driven, reflex, autonomous)</p>
          <p><span className="text-gray-600">Dashboard:</span> Next.js 15 + Tailwind</p>
          <p><span className="text-gray-600">API:</span> FastAPI + uvicorn</p>
          <p><span className="text-gray-600">Available LLM providers:</span> {available.join(', ') || 'simulated'}</p>
        </div>
      </div>
    </div>
  );
}
