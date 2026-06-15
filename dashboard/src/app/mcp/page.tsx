'use client';

import { useCallback, useEffect, useState } from 'react';
import { PlugsConnected, Plus } from '@phosphor-icons/react';
import { api, type McpServer } from '@/lib/api';
import { PageHeader } from '@/components/layout/PageHeader';
import { Card } from '@/components/ui/card';
import { Button } from '@/components/ui/button';
import { Input, Textarea, Select } from '@/components/ui/input';
import { Field, FieldLabel } from '@/components/ui/field';
import { Badge } from '@/components/ui/badge';
import { Skeleton } from '@/components/ui/skeleton';
import { EmptyState } from '@/components/ui/empty-state';
import { ErrorState } from '@/components/ui/error-state';
import {
  Table,
  TableHeader,
  TableBody,
  TableRow,
  TableHead,
  TableCell,
  TableCellMono,
} from '@/components/ui/table';
import {
  Dialog,
  DialogTrigger,
  DialogContent,
  DialogHeader,
  DialogTitle,
  DialogDescription,
  DialogFooter,
  DialogClose,
} from '@/components/ui/dialog';

/** Parse "KEY=value" lines into a record. */
function parseKv(text: string): Record<string, string> {
  const out: Record<string, string> = {};
  for (const line of text.split('\n')) {
    const t = line.trim();
    if (!t) continue;
    const eq = t.indexOf('=');
    if (eq === -1) continue;
    out[t.slice(0, eq).trim()] = t.slice(eq + 1).trim();
  }
  return out;
}

function parseLines(text: string): string[] {
  return text
    .split('\n')
    .map((l) => l.trim())
    .filter(Boolean);
}

function RegisterDialog({ onRegistered }: { onRegistered: () => void }) {
  const [open, setOpen] = useState(false);
  const [scope, setScope] = useState<'platform' | 'user'>('platform');
  const [userId, setUserId] = useState('default');
  const [name, setName] = useState('');
  const [pkg, setPkg] = useState('');
  const [env, setEnv] = useState('');
  const [secrets, setSecrets] = useState('');
  const [args, setArgs] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [warning, setWarning] = useState<string | null>(null);

  const reset = () => {
    setName('');
    setPkg('');
    setEnv('');
    setSecrets('');
    setArgs('');
  };

  const submit = async () => {
    if (!name.trim() || !pkg.trim()) {
      setError('Server name and package are required.');
      return;
    }
    setBusy(true);
    setError(null);
    setWarning(null);
    try {
      if (scope === 'platform') {
        const res = await api.registerMcp({
          server_name: name,
          package: pkg,
          env_vars: parseKv(env),
          args: parseLines(args),
        });
        onRegistered();
        // The config is stored regardless, but connecting may fail or yield no
        // tools (e.g. missing credentials). Surface that instead of a silent
        // "success" that gives agents nothing to call.
        if (res?.connected === false) {
          setWarning(
            `Saved, but the server didn't connect: ${res.detail ?? 'unknown error'}`,
          );
          setBusy(false);
          return;
        }
        if (res && res.tools_discovered === 0) {
          setWarning('Saved and connected, but 0 tools were discovered — check credentials/env vars.');
          setBusy(false);
          return;
        }
      } else {
        await api.registerUserMcp(userId, name, {
          package: pkg,
          env_vars: parseKv(env),
          secrets: parseKv(secrets),
          args: parseLines(args),
        });
        onRegistered();
      }
      setOpen(false);
      reset();
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Registration failed');
    } finally {
      setBusy(false);
    }
  };

  return (
    <Dialog open={open} onOpenChange={setOpen}>
      <DialogTrigger asChild>
        <Button>
          <Plus className="h-4 w-4" aria-hidden />
          Register server
        </Button>
      </DialogTrigger>
      <DialogContent className="max-w-xl">
        <DialogHeader>
          <DialogTitle>Register MCP server</DialogTitle>
          <DialogDescription>Bind a Model Context Protocol server package.</DialogDescription>
        </DialogHeader>
        <div className="space-y-4">
          <div className="grid grid-cols-2 gap-3">
            <Field>
              <FieldLabel htmlFor="mcp-scope">Scope</FieldLabel>
              <Select
                id="mcp-scope"
                value={scope}
                onChange={(e) => setScope(e.target.value as 'platform' | 'user')}
              >
                <option value="platform">Platform</option>
                <option value="user">Per-user</option>
              </Select>
            </Field>
            {scope === 'user' ? (
              <Field>
                <FieldLabel htmlFor="mcp-user">User id</FieldLabel>
                <Input id="mcp-user" value={userId} onChange={(e) => setUserId(e.target.value)} />
              </Field>
            ) : (
              <div />
            )}
          </div>
          <div className="grid grid-cols-2 gap-3">
            <Field>
              <FieldLabel htmlFor="mcp-name">Server name</FieldLabel>
              <Input id="mcp-name" value={name} onChange={(e) => setName(e.target.value)} placeholder="atlassian" />
            </Field>
            <Field>
              <FieldLabel htmlFor="mcp-pkg">Package</FieldLabel>
              <Input id="mcp-pkg" value={pkg} onChange={(e) => setPkg(e.target.value)} placeholder="mcp-atlassian" />
            </Field>
          </div>
          <Field>
            <FieldLabel htmlFor="mcp-env">Env vars (KEY=value per line)</FieldLabel>
            <Textarea
              id="mcp-env"
              value={env}
              onChange={(e) => setEnv(e.target.value)}
              className="min-h-20 font-mono text-xs"
              placeholder={'BASE_URL=https://example.atlassian.net'}
            />
          </Field>
          {scope === 'user' ? (
            <Field>
              <FieldLabel htmlFor="mcp-secrets">Secrets (KEY=value per line)</FieldLabel>
              <Textarea
                id="mcp-secrets"
                value={secrets}
                onChange={(e) => setSecrets(e.target.value)}
                className="min-h-16 font-mono text-xs"
                placeholder={'API_TOKEN=…'}
              />
            </Field>
          ) : null}
          <Field>
            <FieldLabel htmlFor="mcp-args">Args (one per line)</FieldLabel>
            <Textarea
              id="mcp-args"
              value={args}
              onChange={(e) => setArgs(e.target.value)}
              className="min-h-16 font-mono text-xs"
            />
          </Field>
          {error ? <p className="text-[13px] text-danger">{error}</p> : null}
          {warning ? <p className="text-[13px] text-warning">{warning}</p> : null}
        </div>
        <DialogFooter>
          <DialogClose asChild>
            <Button variant="ghost">Cancel</Button>
          </DialogClose>
          <Button onClick={submit} disabled={busy}>
            {busy ? 'Registering…' : 'Register'}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

function RemoveButton({ name, onRemoved }: { name: string; onRemoved: () => void }) {
  const [busy, setBusy] = useState(false);
  const remove = async () => {
    setBusy(true);
    try {
      await api.removeMcp(name);
      onRemoved();
    } finally {
      setBusy(false);
    }
  };
  return (
    <Dialog>
      <DialogTrigger asChild>
        <Button variant="ghost" size="sm">
          Remove
        </Button>
      </DialogTrigger>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>Remove this server?</DialogTitle>
          <DialogDescription>
            <span className="font-mono text-primary">{name}</span> will be unbound from the platform.
          </DialogDescription>
        </DialogHeader>
        <DialogFooter>
          <DialogClose asChild>
            <Button variant="ghost">Cancel</Button>
          </DialogClose>
          <DialogClose asChild>
            <Button variant="destructive" onClick={remove} disabled={busy}>
              Remove
            </Button>
          </DialogClose>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

export default function McpPage() {
  const [servers, setServers] = useState<McpServer[] | null>(null);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    setServers(null);
    setError(null);
    try {
      const data = await api.listMcp();
      setServers(Array.isArray(data) ? data : []);
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Failed to load MCP servers');
    }
  }, []);

  useEffect(() => {
    load();
  }, [load]);

  return (
    <div>
      <PageHeader
        title="MCP servers"
        description="Model Context Protocol servers bound to the platform."
        actions={<RegisterDialog onRegistered={load} />}
      />

      {error ? (
        <ErrorState title="Couldn't load MCP servers" detail={error} onRetry={load} />
      ) : servers === null ? (
        <div className="space-y-2">
          {Array.from({ length: 4 }).map((_, i) => (
            <Skeleton key={i} className="h-12 w-full" />
          ))}
        </div>
      ) : servers.length === 0 ? (
        <EmptyState
          icon={PlugsConnected}
          title="No MCP servers"
          description="Register a Model Context Protocol server to give agents new tools."
        />
      ) : (
        <Card>
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>Server</TableHead>
                <TableHead>Package</TableHead>
                <TableHead>Env</TableHead>
                <TableHead className="text-right">Actions</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {servers.map((s) => {
                const envKeys = Object.keys(s.env_vars ?? {});
                return (
                  <TableRow key={s.server_name}>
                    <TableCell className="font-medium text-primary">{s.server_name}</TableCell>
                    <TableCellMono>{s.package}</TableCellMono>
                    <TableCell>
                      {envKeys.length ? (
                        <span className="flex flex-wrap gap-1">
                          {envKeys.map((k) => (
                            <Badge key={k} variant="outline" className="font-mono">
                              {k}=***
                            </Badge>
                          ))}
                        </span>
                      ) : (
                        <span className="text-muted">—</span>
                      )}
                    </TableCell>
                    <TableCell className="text-right">
                      <RemoveButton name={s.server_name} onRemoved={load} />
                    </TableCell>
                  </TableRow>
                );
              })}
            </TableBody>
          </Table>
        </Card>
      )}
    </div>
  );
}
