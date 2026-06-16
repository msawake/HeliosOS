'use client';

import { useCallback, useEffect, useState } from 'react';
import { HardDrives, Plus } from '@phosphor-icons/react';
import { api, type Env } from '@/lib/api';
import { PageHeader } from '@/components/layout/PageHeader';
import { Card } from '@/components/ui/card';
import { Button } from '@/components/ui/button';
import { Input, Textarea } from '@/components/ui/input';
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

function CreateDialog({ onCreated }: { onCreated: () => void }) {
  const [open, setOpen] = useState(false);
  const [name, setName] = useState('');
  const [image, setImage] = useState('');
  const [env, setEnv] = useState('');
  const [cpu, setCpu] = useState('');
  const [memory, setMemory] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const reset = () => {
    setName('');
    setImage('');
    setEnv('');
    setCpu('');
    setMemory('');
  };

  const submit = async () => {
    if (!name.trim() || !image.trim()) {
      setError('Name and image are required.');
      return;
    }
    setBusy(true);
    setError(null);
    try {
      const resources: Record<string, string> = {};
      if (cpu.trim()) resources.cpu = cpu.trim();
      if (memory.trim()) resources.memory = memory.trim();
      await api.createEnv({
        name: name.trim(),
        image: image.trim(),
        env_vars: parseKv(env),
        resources,
      });
      onCreated();
      setOpen(false);
      reset();
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Create failed');
    } finally {
      setBusy(false);
    }
  };

  return (
    <Dialog open={open} onOpenChange={setOpen}>
      <DialogTrigger asChild>
        <Button>
          <Plus className="h-4 w-4" aria-hidden />
          New environment
        </Button>
      </DialogTrigger>
      <DialogContent className="max-w-xl">
        <DialogHeader>
          <DialogTitle>New environment</DialogTitle>
          <DialogDescription>
            A reusable pod template. Attach it to agents — their shell, file, and git tools run inside a
            pod cloned from this template.
          </DialogDescription>
        </DialogHeader>
        <div className="space-y-4">
          <div className="grid grid-cols-2 gap-3">
            <Field>
              <FieldLabel htmlFor="env-name">Name</FieldLabel>
              <Input id="env-name" value={name} onChange={(e) => setName(e.target.value)} placeholder="python-builder" />
            </Field>
            <Field>
              <FieldLabel htmlFor="env-image">Image</FieldLabel>
              <Input id="env-image" value={image} onChange={(e) => setImage(e.target.value)} placeholder="python:3.12" />
            </Field>
          </div>
          <Field>
            <FieldLabel htmlFor="env-vars">Env vars (KEY=value per line)</FieldLabel>
            <Textarea
              id="env-vars"
              value={env}
              onChange={(e) => setEnv(e.target.value)}
              className="min-h-20 font-mono text-xs"
              placeholder={'NODE_ENV=production'}
            />
          </Field>
          <div className="grid grid-cols-2 gap-3">
            <Field>
              <FieldLabel htmlFor="env-cpu">CPU limit</FieldLabel>
              <Input id="env-cpu" value={cpu} onChange={(e) => setCpu(e.target.value)} placeholder="500m" />
            </Field>
            <Field>
              <FieldLabel htmlFor="env-mem">Memory limit</FieldLabel>
              <Input id="env-mem" value={memory} onChange={(e) => setMemory(e.target.value)} placeholder="512Mi" />
            </Field>
          </div>
          {error ? <p className="text-[13px] text-danger">{error}</p> : null}
        </div>
        <DialogFooter>
          <DialogClose asChild>
            <Button variant="ghost">Cancel</Button>
          </DialogClose>
          <Button onClick={submit} disabled={busy}>
            {busy ? 'Creating…' : 'Create'}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

function DeleteButton({ env, onDeleted }: { env: Env; onDeleted: () => void }) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const attached = env.attached_agents?.length ?? 0;
  const remove = async () => {
    setBusy(true);
    setError(null);
    try {
      await api.deleteEnv(env.env_def_id);
      onDeleted();
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Delete failed');
    } finally {
      setBusy(false);
    }
  };
  return (
    <Dialog>
      <DialogTrigger asChild>
        <Button variant="ghost" size="sm">
          Delete
        </Button>
      </DialogTrigger>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>Delete this environment?</DialogTitle>
          <DialogDescription>
            <span className="font-mono text-primary">{env.name}</span> will be removed.
            {attached > 0
              ? ` It is attached to ${attached} agent${attached === 1 ? '' : 's'} — detach them first.`
              : ''}
          </DialogDescription>
        </DialogHeader>
        {error ? <p className="text-[13px] text-danger">{error}</p> : null}
        <DialogFooter>
          <DialogClose asChild>
            <Button variant="ghost">Cancel</Button>
          </DialogClose>
          <Button variant="destructive" onClick={remove} disabled={busy || attached > 0}>
            Delete
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

export default function EnvironmentsPage() {
  const [envs, setEnvs] = useState<Env[] | null>(null);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    setEnvs(null);
    setError(null);
    try {
      const data = await api.listEnvs();
      setEnvs(Array.isArray(data) ? data : []);
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Failed to load environments');
    }
  }, []);

  useEffect(() => {
    load();
  }, [load]);

  return (
    <div>
      <PageHeader
        title="Environments"
        description="Reusable pod templates. Attach one to an agent and its shell/file/git tools run inside that pod."
        actions={<CreateDialog onCreated={load} />}
      />

      {error ? (
        <ErrorState title="Couldn't load environments" detail={error} onRetry={load} />
      ) : envs === null ? (
        <div className="space-y-2">
          {Array.from({ length: 4 }).map((_, i) => (
            <Skeleton key={i} className="h-12 w-full" />
          ))}
        </div>
      ) : envs.length === 0 ? (
        <EmptyState
          icon={HardDrives}
          title="No environments"
          description="Create a pod template (image + env vars + limits) and attach it to agents."
        />
      ) : (
        <Card>
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>Name</TableHead>
                <TableHead>Image</TableHead>
                <TableHead>Env</TableHead>
                <TableHead>Attached</TableHead>
                <TableHead className="text-right">Actions</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {envs.map((env) => {
                const envKeys = Object.keys(env.env_vars ?? {});
                const attached = env.attached_agents?.length ?? 0;
                return (
                  <TableRow key={env.env_def_id}>
                    <TableCell className="font-medium text-primary">{env.name}</TableCell>
                    <TableCellMono>{env.image}</TableCellMono>
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
                    <TableCell>
                      {attached > 0 ? (
                        <Badge variant="outline">{attached} agent{attached === 1 ? '' : 's'}</Badge>
                      ) : (
                        <span className="text-muted">—</span>
                      )}
                    </TableCell>
                    <TableCell className="text-right">
                      <DeleteButton env={env} onDeleted={load} />
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
