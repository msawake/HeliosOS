import { useCallback, useEffect, useState } from 'react';
import { Plus } from '@phosphor-icons/react';

import { api, type SecretRef, type SecretScope } from '@/lib/api';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select';
import { Badge } from '@/components/ui/badge';
import {
  Dialog, DialogContent, DialogHeader, DialogTitle, DialogDescription, DialogFooter,
} from '@/components/ui/dialog';
import { cn } from '@/lib/utils';
import { LabeledField } from './fields';

const SCOPES: SecretScope[] = ['user', 'namespace', 'platform'];

/** Pick a secret name across the three tiers (names only) + create new inline.
 *  Calls onPick with the chosen logical name (referenced as secret:<name>). */
export function SecretPicker({
  namespace,
  selected,
  onPick,
}: {
  namespace: string;
  selected?: string;
  onPick: (name: string) => void;
}) {
  const [scope, setScope] = useState<SecretScope>('user');
  const [secrets, setSecrets] = useState<SecretRef[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [creating, setCreating] = useState(false);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const res = await api.listSecrets(scope, scope === 'namespace' ? namespace : undefined);
      setSecrets(res.secrets ?? []);
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Could not list secrets');
      setSecrets([]);
    } finally {
      setLoading(false);
    }
  }, [scope, namespace]);

  useEffect(() => {
    load();
  }, [load]);

  return (
    <div className="space-y-2.5 rounded-lg border border-edge bg-surface p-3">
      <div className="flex items-center gap-2">
        <Select value={scope} onValueChange={(v) => setScope(v as SecretScope)}>
          <SelectTrigger aria-label="Secret scope" className="h-8 w-36 text-[13px]">
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            {SCOPES.map((sc) => (
              <SelectItem key={sc} value={sc}>
                {sc === 'namespace' ? `namespace (${namespace})` : sc}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
        <span className="flex-1" />
        <Button type="button" size="sm" variant="secondary" onClick={() => setCreating(true)}>
          <Plus className="h-3.5 w-3.5" aria-hidden /> New
        </Button>
      </div>

      {error ? (
        <p className="text-xs text-danger">{error}</p>
      ) : loading ? (
        <p className="text-[13px] text-tertiary">Loading…</p>
      ) : secrets.length === 0 ? (
        <p className="text-[13px] text-tertiary">No {scope} secrets yet — create one.</p>
      ) : (
        <div className="flex flex-wrap gap-1.5">
          {secrets.map((s) => (
            <button
              key={`${s.scope}:${s.namespace}:${s.name}`}
              type="button"
              onClick={() => onPick(s.name)}
              className={cn(
                'rounded-full border px-2.5 py-1 font-mono text-[12px] transition-colors',
                selected === s.name
                  ? 'border-accent/40 bg-accent-wash text-accent'
                  : 'border-edge text-secondary hover:bg-surface-hover',
              )}
            >
              {s.name}
              {s.kind && s.kind !== 'generic' ? (
                <span className="ml-1.5 text-[10px] text-muted">{s.kind}</span>
              ) : null}
            </button>
          ))}
        </div>
      )}

      {selected ? (
        <p className="text-xs text-tertiary">
          Selected: <Badge variant="brand" className="font-mono">{selected}</Badge>
        </p>
      ) : null}

      <CreateSecretDialog
        open={creating}
        onOpenChange={setCreating}
        defaultScope={scope}
        namespace={namespace}
        onCreated={(name) => {
          setCreating(false);
          load();
          onPick(name);
        }}
      />
    </div>
  );
}

function CreateSecretDialog({
  open,
  onOpenChange,
  defaultScope,
  namespace,
  onCreated,
}: {
  open: boolean;
  onOpenChange: (v: boolean) => void;
  defaultScope: SecretScope;
  namespace: string;
  onCreated: (name: string) => void;
}) {
  const [scope, setScope] = useState<SecretScope>(defaultScope);
  const [name, setName] = useState('');
  const [value, setValue] = useState('');
  const [kind, setKind] = useState('generic');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (open) {
      setScope(defaultScope);
      setName('');
      setValue('');
      setKind('generic');
      setError(null);
    }
  }, [open, defaultScope]);

  const submit = async () => {
    setBusy(true);
    setError(null);
    try {
      await api.putScopedSecret({
        scope,
        namespace: scope === 'namespace' ? namespace : undefined,
        name: name.trim(),
        value,
        kind: kind.trim() || 'generic',
      });
      onCreated(name.trim());
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Could not store secret');
    } finally {
      setBusy(false);
    }
  };

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>Create secret</DialogTitle>
          <DialogDescription>
            Stored encrypted and write-only — the value is never read back. Referenced from the
            manifest as <span className="font-mono">secret:&lt;name&gt;</span>.
          </DialogDescription>
        </DialogHeader>
        <div className="space-y-3">
          <LabeledField label="Scope">
            <Select value={scope} onValueChange={(v) => setScope(v as SecretScope)}>
              <SelectTrigger><SelectValue /></SelectTrigger>
              <SelectContent>
                {SCOPES.map((sc) => (
                  <SelectItem key={sc} value={sc}>
                    {sc === 'namespace' ? `namespace (${namespace})` : sc}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </LabeledField>
          <LabeledField label="Name" description="[A-Za-z0-9_-], e.g. litellm-gateway-key">
            <Input value={name} onChange={(e) => setName(e.target.value)} placeholder="gateway-key" />
          </LabeledField>
          <LabeledField label="Value">
            <Input type="password" value={value} onChange={(e) => setValue(e.target.value)} placeholder="••••••••" />
          </LabeledField>
          <LabeledField label="Kind" description="A classification label (optional)">
            <Input value={kind} onChange={(e) => setKind(e.target.value)} placeholder="llm_gateway" />
          </LabeledField>
          {error ? <p className="text-xs text-danger">{error}</p> : null}
        </div>
        <DialogFooter>
          <Button variant="ghost" onClick={() => onOpenChange(false)} disabled={busy}>
            Cancel
          </Button>
          <Button onClick={submit} disabled={busy || !name.trim() || !value}>
            {busy ? 'Storing…' : 'Store secret'}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
