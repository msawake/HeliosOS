'use client';

import { useCallback, useEffect, useMemo, useState } from 'react';
import { Plus, Trash } from '@phosphor-icons/react';

import { api, ApiError, type Agent, type ManagedUser, type NamespaceDef } from '@/lib/api';
import { useAuth } from '@/lib/auth';
import { PageHeader } from '@/components/layout/PageHeader';
import { Tabs, TabsList, TabsTrigger, TabsContent } from '@/components/ui/tabs';
import { Card, CardContent } from '@/components/ui/card';
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
import { Field, FieldLabel } from '@/components/ui/field';
import {
  Table, TableHeader, TableBody, TableRow, TableHead, TableCell,
} from '@/components/ui/table';
import {
  Dialog, DialogContent, DialogHeader, DialogTitle, DialogFooter,
} from '@/components/ui/dialog';
import { EmptyState } from '@/components/ui/empty-state';

const ROLES = ['admin', 'operator', 'viewer'];

function errText(e: unknown): string {
  if (e instanceof ApiError) return e.detail || e.message;
  return e instanceof Error ? e.message : 'Something went wrong';
}

export default function AccessPage() {
  const { user } = useAuth();

  if (user && user.role !== 'admin') {
    return (
      <div>
        <PageHeader title="Access" />
        <EmptyState title="Admins only" description="You need the admin role to manage access." />
      </div>
    );
  }

  return (
    <div>
      <PageHeader title="Access" description="Manage users, namespaces, and namespace admins." />
      <Tabs defaultValue="users">
        <TabsList>
          <TabsTrigger value="users">Users</TabsTrigger>
          <TabsTrigger value="namespaces">Namespaces</TabsTrigger>
          <TabsTrigger value="nsadmins">Namespace admins</TabsTrigger>
        </TabsList>
        <TabsContent value="users"><UsersTab /></TabsContent>
        <TabsContent value="namespaces"><NamespacesTab /></TabsContent>
        <TabsContent value="nsadmins"><NamespaceAdminsTab /></TabsContent>
      </Tabs>
    </div>
  );
}

// ─── Users ──────────────────────────────────────────────────────────────────

function UsersTab() {
  const [users, setUsers] = useState<ManagedUser[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [creating, setCreating] = useState(false);

  const load = useCallback(async () => {
    setError(null);
    try {
      setUsers((await api.listUsers()).users ?? []);
    } catch (e) {
      setError(errText(e));
    }
  }, []);
  useEffect(() => { load(); }, [load]);

  const setRole = async (u: ManagedUser, role: string) => {
    setError(null);
    try {
      await api.updateUser(u.id!, { role });
      load();
    } catch (e) {
      setError(errText(e));
    }
  };
  const remove = async (u: ManagedUser) => {
    setError(null);
    try {
      await api.deleteUser(u.id!);
      load();
    } catch (e) {
      setError(errText(e));
    }
  };

  return (
    <Card>
      <CardContent className="space-y-3 pt-5">
        <div className="flex items-center justify-between">
          <p className="text-[13px] text-tertiary">{users.length} user(s)</p>
          <Button size="sm" onClick={() => setCreating(true)}>
            <Plus className="h-3.5 w-3.5" aria-hidden /> New user
          </Button>
        </div>
        {error ? <p className="text-xs text-danger">{error}</p> : null}
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead>Email</TableHead>
              <TableHead>Role</TableHead>
              <TableHead>Name</TableHead>
              <TableHead />
            </TableRow>
          </TableHeader>
          <TableBody>
            {users.map((u) => (
              <TableRow key={u.id}>
                <TableCell className="font-mono text-xs">
                  {u.email}
                  {u.is_federated ? <Badge variant="outline" className="ml-2">SSO</Badge> : null}
                </TableCell>
                <TableCell>
                  <Select value={u.role} onValueChange={(v) => setRole(u, v)}>
                    <SelectTrigger className="h-8 w-32 text-[13px]">
                      <SelectValue />
                    </SelectTrigger>
                    <SelectContent>
                      {ROLES.map((r) => <SelectItem key={r} value={r}>{r}</SelectItem>)}
                    </SelectContent>
                  </Select>
                </TableCell>
                <TableCell className="text-secondary">{u.name || '—'}</TableCell>
                <TableCell className="text-right">
                  <Button size="icon-sm" variant="ghost" aria-label={`Delete ${u.email}`} onClick={() => remove(u)}>
                    <Trash className="h-3.5 w-3.5 text-danger" aria-hidden />
                  </Button>
                </TableCell>
              </TableRow>
            ))}
          </TableBody>
        </Table>
      </CardContent>
      <CreateUserDialog open={creating} onOpenChange={setCreating} onCreated={() => { setCreating(false); load(); }} />
    </Card>
  );
}

function CreateUserDialog({
  open, onOpenChange, onCreated,
}: { open: boolean; onOpenChange: (v: boolean) => void; onCreated: () => void }) {
  const [email, setEmail] = useState('');
  const [password, setPassword] = useState('');
  const [role, setRole] = useState('viewer');
  const [name, setName] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (open) { setEmail(''); setPassword(''); setRole('viewer'); setName(''); setError(null); }
  }, [open]);

  const submit = async () => {
    setBusy(true); setError(null);
    try {
      await api.createUser({ email: email.trim(), password, role, name: name.trim() });
      onCreated();
    } catch (e) {
      setError(errText(e));
    } finally {
      setBusy(false);
    }
  };

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent>
        <DialogHeader><DialogTitle>New user</DialogTitle></DialogHeader>
        <div className="space-y-3">
          <Field><FieldLabel>Email</FieldLabel><Input value={email} onChange={(e) => setEmail(e.target.value)} placeholder="you@company.com" /></Field>
          <Field><FieldLabel>Password</FieldLabel><Input type="password" value={password} onChange={(e) => setPassword(e.target.value)} placeholder="min 8 chars" /></Field>
          <Field><FieldLabel>Role</FieldLabel>
            <Select value={role} onValueChange={(v) => setRole(v)}>
              <SelectTrigger><SelectValue /></SelectTrigger>
              <SelectContent>
                {ROLES.map((r) => <SelectItem key={r} value={r}>{r}</SelectItem>)}
              </SelectContent>
            </Select>
          </Field>
          <Field><FieldLabel>Name (optional)</FieldLabel><Input value={name} onChange={(e) => setName(e.target.value)} /></Field>
          {error ? <p className="text-xs text-danger">{error}</p> : null}
        </div>
        <DialogFooter>
          <Button variant="ghost" onClick={() => onOpenChange(false)} disabled={busy}>Cancel</Button>
          <Button onClick={submit} disabled={busy || !email.trim() || password.length < 8}>
            {busy ? 'Creating…' : 'Create user'}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

// ─── Namespaces ───────────────────────────────────────────────────────────

interface NsRow { namespace: string; description?: string; registered: boolean; agents: number; }

function NamespacesTab() {
  const [registry, setRegistry] = useState<NamespaceDef[]>([]);
  const [agents, setAgents] = useState<Agent[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [creating, setCreating] = useState(false);
  const [name, setName] = useState('');
  const [description, setDescription] = useState('');

  const load = useCallback(async () => {
    setError(null);
    try {
      // Merge the formal registry with namespaces actually in use by agents —
      // a namespace can exist (agents reference it) without being registered.
      const [ns, ag] = await Promise.all([
        api.listNamespaces().then((r) => r.namespaces ?? []).catch(() => []),
        api.listAgents().then((a) => (Array.isArray(a) ? a : [])).catch(() => []),
      ]);
      setRegistry(ns);
      setAgents(ag);
    } catch (e) {
      setError(errText(e));
    }
  }, []);
  useEffect(() => { load(); }, [load]);

  const rows: NsRow[] = useMemo(() => {
    const m = new Map<string, NsRow>();
    for (const n of registry) m.set(n.namespace, { namespace: n.namespace, description: n.description, registered: true, agents: 0 });
    for (const a of agents) {
      const ns = a.namespace || 'default';
      const e = m.get(ns) ?? { namespace: ns, registered: false, agents: 0 };
      e.agents += 1;
      m.set(ns, e);
    }
    return [...m.values()].sort((a, b) => a.namespace.localeCompare(b.namespace));
  }, [registry, agents]);

  const create = async () => {
    setError(null);
    try {
      await api.createNamespace({ namespace: name.trim(), description: description.trim() });
      setCreating(false); setName(''); setDescription('');
      load();
    } catch (e) {
      setError(errText(e));
    }
  };
  const remove = async (ns: string) => {
    setError(null);
    try { await api.deleteNamespace(ns); load(); } catch (e) { setError(errText(e)); }
  };

  return (
    <Card>
      <CardContent className="space-y-3 pt-5">
        <div className="flex items-center justify-between">
          <p className="text-[13px] text-tertiary">{rows.length} namespace(s)</p>
          <Button size="sm" onClick={() => setCreating(true)}><Plus className="h-3.5 w-3.5" aria-hidden /> New namespace</Button>
        </div>
        {error ? <p className="text-xs text-danger">{error}</p> : null}
        <Table>
          <TableHeader><TableRow><TableHead>Namespace</TableHead><TableHead>Agents</TableHead><TableHead>Status</TableHead><TableHead>Description</TableHead><TableHead /></TableRow></TableHeader>
          <TableBody>
            {rows.map((n) => (
              <TableRow key={n.namespace}>
                <TableCell className="font-mono text-xs">{n.namespace}</TableCell>
                <TableCell className="text-secondary tabular-nums">{n.agents}</TableCell>
                <TableCell>
                  {n.registered
                    ? <Badge variant="success">Registered</Badge>
                    : <Badge variant="outline">In use</Badge>}
                </TableCell>
                <TableCell className="text-secondary">{n.description || '—'}</TableCell>
                <TableCell className="text-right">
                  {n.registered ? (
                    <Button size="icon-sm" variant="ghost" aria-label={`Delete ${n.namespace}`} onClick={() => remove(n.namespace)}>
                      <Trash className="h-3.5 w-3.5 text-danger" aria-hidden />
                    </Button>
                  ) : null}
                </TableCell>
              </TableRow>
            ))}
          </TableBody>
        </Table>
      </CardContent>
      <Dialog open={creating} onOpenChange={setCreating}>
        <DialogContent>
          <DialogHeader><DialogTitle>New namespace</DialogTitle></DialogHeader>
          <div className="space-y-3">
            <Field><FieldLabel>Name</FieldLabel><Input value={name} onChange={(e) => setName(e.target.value)} placeholder="treasury" /></Field>
            <Field><FieldLabel>Description (optional)</FieldLabel><Input value={description} onChange={(e) => setDescription(e.target.value)} /></Field>
          </div>
          <DialogFooter>
            <Button variant="ghost" onClick={() => setCreating(false)}>Cancel</Button>
            <Button onClick={create} disabled={!name.trim()}>Create</Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </Card>
  );
}

// ─── Namespace admins ─────────────────────────────────────────────────────

function NamespaceAdminsTab() {
  const [namespaces, setNamespaces] = useState<NamespaceDef[]>([]);
  const [ns, setNs] = useState('');
  const [admins, setAdmins] = useState<string[]>([]);
  const [grantId, setGrantId] = useState('');
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    api.listNamespaces().then((r) => {
      setNamespaces(r.namespaces ?? []);
      if (r.namespaces?.length && !ns) setNs(r.namespaces[0].namespace);
    }).catch((e) => setError(errText(e)));
  }, [ns]);

  const loadAdmins = useCallback(async (namespace: string) => {
    if (!namespace) return;
    setError(null);
    try { setAdmins((await api.listNamespaceAdmins(namespace)).admins ?? []); }
    catch (e) { setError(errText(e)); }
  }, []);
  useEffect(() => { loadAdmins(ns); }, [ns, loadAdmins]);

  const grant = async () => {
    if (!grantId.trim() || !ns) return;
    setError(null);
    try { await api.grantNamespaceAdmin(ns, grantId.trim()); setGrantId(''); loadAdmins(ns); }
    catch (e) { setError(errText(e)); }
  };
  const revoke = async (uid: string) => {
    setError(null);
    try { await api.revokeNamespaceAdmin(ns, uid); loadAdmins(ns); } catch (e) { setError(errText(e)); }
  };

  return (
    <Card>
      <CardContent className="space-y-4 pt-5">
        <Field>
          <FieldLabel>Namespace</FieldLabel>
          <Select value={ns} onValueChange={(v) => setNs(v)} disabled={namespaces.length === 0}>
            <SelectTrigger className="w-64">
              <SelectValue placeholder={namespaces.length === 0 ? 'No namespaces' : 'Select namespace…'} />
            </SelectTrigger>
            <SelectContent>
              {namespaces.map((n) => <SelectItem key={n.namespace} value={n.namespace}>{n.namespace}</SelectItem>)}
            </SelectContent>
          </Select>
        </Field>
        {error ? <p className="text-xs text-danger">{error}</p> : null}
        <div>
          <p className="mb-1.5 text-[13px] font-medium text-secondary">Admins of {ns || '—'}</p>
          {admins.length === 0 ? (
            <p className="text-[13px] text-tertiary">No namespace admins yet.</p>
          ) : (
            <div className="flex flex-wrap gap-1.5">
              {admins.map((a) => (
                <Badge key={a} variant="default" className="gap-1 font-mono">
                  {a}
                  <button type="button" aria-label={`Revoke ${a}`} onClick={() => revoke(a)} className="text-muted hover:text-danger">
                    <Trash className="h-3 w-3" aria-hidden />
                  </button>
                </Badge>
              ))}
            </div>
          )}
        </div>
        <div className="flex items-end gap-2">
          <Field className="flex-1">
            <FieldLabel>Grant admin (user id)</FieldLabel>
            <Input value={grantId} onChange={(e) => setGrantId(e.target.value)} placeholder="user id" />
          </Field>
          <Button onClick={grant} disabled={!grantId.trim() || !ns}>Grant</Button>
        </div>
      </CardContent>
    </Card>
  );
}
