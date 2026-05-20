import { useMemo, useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";

import { AgentDetailSheet } from "@/components/AgentDetailSheet";
import { FleetBar } from "@/components/FleetBar";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { api } from "@/lib/api";
import type {
  Agent,
  AgentProcess,
  Approval,
  AuditEvent,
  BillingMetering,
  FleetResponse,
} from "@/lib/api";
import { shortName } from "@/lib/utils";
import { BillingTab } from "@/tabs/BillingTab";
import { CostTab } from "@/tabs/CostTab";
import { FleetTab } from "@/tabs/FleetTab";
import { GovernanceTab } from "@/tabs/GovernanceTab";
import { ManifestTab } from "@/tabs/ManifestTab";
import { MCPTab } from "@/tabs/MCPTab";
import { TopologyTab } from "@/tabs/TopologyTab";

function unwrap<T>(
  raw: T[] | { agents?: T[]; requests?: T[]; items?: T[]; events?: T[] } | null,
): T[] {
  if (!raw) return [];
  if (Array.isArray(raw)) return raw;
  return raw.agents ?? raw.requests ?? raw.items ?? raw.events ?? [];
}

export default function App() {
  const qc = useQueryClient();
  const [activeTab, setActiveTab] = useState("fleet");
  const [openAgent, setOpenAgent] = useState<string | undefined>();
  const [manifestSelected, setManifestSelected] = useState<string | undefined>();

  const fleetQ = useQuery({ queryKey: ["fleet"], queryFn: () => api.fleet() });
  const agentsQ = useQuery({ queryKey: ["agents"], queryFn: () => api.agents() });
  const approvalsQ = useQuery({ queryKey: ["approvals"], queryFn: () => api.approvals() });
  const auditQ = useQuery({ queryKey: ["audit"], queryFn: () => api.audit() });
  const adminQ = useQuery({ queryKey: ["admin"], queryFn: () => api.adminEvents() });
  const billingQ = useQuery({ queryKey: ["billing"], queryFn: () => api.billing() });

  const fleet = (fleetQ.data ?? null) as FleetResponse | null;
  const agentList = unwrap<Agent>(agentsQ.data ?? null);
  const hitlList = unwrap<Approval>(approvalsQ.data ?? null);
  const audit = [
    ...unwrap<AuditEvent>(auditQ.data ?? null),
    ...unwrap<AuditEvent>(adminQ.data ?? null),
  ].sort((a, b) =>
    (b.created_at ?? b.timestamp ?? "") > (a.created_at ?? a.timestamp ?? "") ? 1 : -1,
  );

  const agentMap = useMemo(() => {
    const m: Record<string, Agent> = {};
    agentList.forEach((a) => (m[a.name] = a));
    return m;
  }, [agentList]);

  const procs: AgentProcess[] = (fleet?.agents ?? []).map((p) => {
    const r = agentMap[shortName(p.name)] ?? ({} as Agent);
    return { ...p, stack: r.stack ?? "unknown", execution_type: r.execution_type ?? "" };
  });

  const pending = hitlList.filter(
    (h) => h.status === "pending" || h.status === "open" || !h.status,
  );
  const kernelEvts = audit.filter((e) => {
    const a = (e.action ?? e.event ?? e.type ?? "").toLowerCase();
    return (
      a.includes("tool") ||
      a.includes("check") ||
      a.includes("deny") ||
      a.includes("admit") ||
      a.includes("deploy") ||
      a.includes("invoke") ||
      a.includes("agent") ||
      a.includes("kernel")
    );
  });
  const stackCount = new Set(procs.map((p) => p.stack)).size;

  const billing = (billingQ.data ?? null) as BillingMetering | null;

  const refreshAll = () => qc.invalidateQueries();

  const openManifestFor = (name: string) => {
    setManifestSelected(name);
    setActiveTab("manifest");
  };

  return (
    <div className="flex h-screen flex-col">
      {/* Header */}
      <div className="flex flex-shrink-0 items-center justify-between border-b border-border bg-surface px-4 py-2">
        <h1 className="text-[14px] text-bright">
          <span className="text-ok">■</span> ForgeOS Mission Control
        </h1>
        <div className="flex items-center gap-3 text-[10px] text-dim">
          <a
            href="/docs/"
            target="_blank"
            rel="noreferrer"
            className="text-bright hover:text-ok"
          >
            ⚲ Docs
          </a>
          <span className="text-border">|</span>
          <span>
            <span className="pulse-dot mr-1 inline-block h-[6px] w-[6px] rounded-full bg-ok" />
            {fleetQ.isFetching ? "refreshing..." : new Date().toLocaleTimeString()}
          </span>
        </div>
      </div>

      <FleetBar procs={procs} summary={fleet?.summary ?? {}} />

      <Tabs
        value={activeTab}
        onValueChange={setActiveTab}
        className="flex flex-1 flex-col overflow-hidden"
      >
        <TabsList>
          <TabsTrigger value="fleet">
            ▶ Fleet
            <Count v={procs.length} />
          </TabsTrigger>
          <TabsTrigger value="gov">
            ⚠ Governance
            <Count v={pending.length > 0 ? `${pending.length} !` : kernelEvts.length} alert={pending.length > 0} />
          </TabsTrigger>
          <TabsTrigger value="cost">
            ■ Cost
            <Count v={`${stackCount} stacks`} />
          </TabsTrigger>
          <TabsTrigger value="topo">
            ◆ Topology
            <Count v={agentList.length} />
          </TabsTrigger>
          <TabsTrigger value="manifest">
            ☰ Manifest
            <Count v={agentList.length} />
          </TabsTrigger>
          <TabsTrigger value="mcp">
            ⚙ MCP
          </TabsTrigger>
          <TabsTrigger value="billing">
            € Billing
            <Count v={billing?.companies?.length ? `€${billing.total_revenue_eur}` : "0"} />
          </TabsTrigger>
        </TabsList>

        <div className="flex-1 overflow-hidden">
          <TabsContent value="fleet">
            <FleetTab
              procs={procs}
              onSelect={(name) => setOpenAgent(name)}
              onChange={refreshAll}
            />
          </TabsContent>
          <TabsContent value="gov">
            <GovernanceTab pending={pending} kernelEvts={kernelEvts} onChange={refreshAll} />
          </TabsContent>
          <TabsContent value="cost">
            <CostTab procs={procs} />
          </TabsContent>
          <TabsContent value="topo">
            <TopologyTab
              agents={agentList}
              procs={procs}
              onSelect={(name) => setOpenAgent(name)}
              onOpenManifest={openManifestFor}
            />
          </TabsContent>
          <TabsContent value="manifest">
            <ManifestTab
              agents={agentList}
              procs={procs}
              selected={manifestSelected}
              onSelect={setManifestSelected}
            />
          </TabsContent>
          <TabsContent value="mcp">
            <MCPTab />
          </TabsContent>
          <TabsContent value="billing">
            <BillingTab billing={billing} />
          </TabsContent>
        </div>
      </Tabs>

      <AgentDetailSheet
        open={!!openAgent}
        onClose={() => setOpenAgent(undefined)}
        agent={openAgent ? agentMap[openAgent] : undefined}
        proc={openAgent ? procs.find((p) => shortName(p.name) === openAgent) : undefined}
      />
    </div>
  );
}

function Count({ v, alert = false }: { v: React.ReactNode; alert?: boolean }) {
  return (
    <span
      className={`ml-1 rounded-lg px-[5px] py-[1px] text-[9px] ${
        alert ? "bg-danger/30 text-bright" : "bg-border text-dim"
      }`}
    >
      {v}
    </span>
  );
}
