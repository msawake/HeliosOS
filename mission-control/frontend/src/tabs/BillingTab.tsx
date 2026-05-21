import type { BillingMetering } from "@/lib/api";
import { fmt, usd } from "@/lib/utils";
import { Card } from "@/components/ui/card";
import { PhaseBadge } from "@/components/PhaseBadge";
import { Progress } from "@/components/ui/progress";
import { Table, Tbody, Td, Th, Thead, Tr } from "@/components/ui/table";
import { ResizableSplit } from "@/components/ui/resizable-split";

const PRICING_BLOCK = `Base fee:     €99/month per company
Included:     50 agents
Overage:      €1.50 per additional agent/month
Example:      200 agents = €99 + (150 × €1.50) = €324/month

License:      BSL 1.1 (kernel + runtime)
              Apache 2.0 (SDK, adapters, examples)
Change date:  2029-04-27 → Apache 2.0`;

export function BillingTab({ billing }: { billing: BillingMetering | null }) {
  const companies = billing?.companies ?? [];
  const pm = billing?.pricing_model ?? {};

  return (
    <ResizableSplit
      left={
        <div>
          <div className="border-b border-border bg-surface px-[14px] py-2 text-[10px] uppercase tracking-widest text-dim">
            Pricing Model
          </div>
          <div className="p-4">
            <div className="rounded-md border border-border bg-bg p-[10px]">
              <h4 className="mb-[6px] text-[11px] uppercase tracking-wider text-warn">
                ForgeOS Commercial License
              </h4>
              <pre className="whitespace-pre-wrap text-[10px] leading-relaxed text-text">
                {PRICING_BLOCK}
              </pre>
            </div>
          </div>
          <div className="border-b border-border bg-surface px-[14px] py-2 text-[10px] uppercase tracking-widest text-dim">
            Revenue Summary
          </div>
          <div className="p-4">
            {companies.length === 0 ? (
              <div className="p-10 text-center text-dim">No billing data available.</div>
            ) : (
              <>
                <DR k="Total Companies" v={billing?.total_companies ?? 0} />
                <DR k="Total Agents" v={billing?.total_agents ?? 0} />
                <DR
                  k="Estimated Monthly Revenue"
                  v={
                    <span className="text-[16px] font-bold text-ok">
                      €{billing?.total_revenue_eur}
                    </span>
                  }
                />
                <DR k="Example (200 agents)" v={`€${pm.example_200_agents_eur ?? "-"}`} />
              </>
            )}
          </div>
        </div>
      }
      right={
        <div>
          <div className="border-b border-border bg-surface px-[14px] py-2 text-[10px] uppercase tracking-widest text-dim">
            Per-Company Metering
          </div>
          {companies.length === 0 ? (
            <div className="p-10 text-center text-dim">
              No companies registered. Deploy agents to see billing.
            </div>
          ) : (
            companies.map((c) => {
              const pct = Math.min(
                100,
                (c.active_agents / Math.max(c.included_agents, 1)) * 100,
              );
              const overFill =
                pct > 100 ? "bg-danger" : pct > 80 ? "bg-warn" : "bg-ok";
              const overText =
                c.overage_agents > 0 ? (
                  <span className="text-orange">
                    +{c.overage_agents} overage (€
                    {(c.overage_agents * c.pricing.overage_per_agent_eur).toFixed(2)})
                  </span>
                ) : (
                  <span className="text-ok">within included tier</span>
                );
              return (
                <Card key={c.company_id}>
                  <div className="flex items-center justify-between">
                    <span className="text-[14px] font-bold text-bright">{c.company_id}</span>
                    <span className="text-[16px] font-bold text-ok">
                      €{c.pricing.estimated_monthly_eur}
                      <span className="text-[10px] text-dim">/mo</span>
                    </span>
                  </div>
                  <div className="my-2">
                    <div className="mb-1 flex justify-between text-[11px] text-dim">
                      <span>
                        {c.active_agents} / {c.included_agents} agents
                      </span>
                      {overText}
                    </div>
                    <Progress value={Math.min(pct, 100)} fillClassName={overFill} />
                  </div>
                  <div className="mt-1 flex gap-4 text-[11px] text-dim">
                    <span>{fmt(c.total_tokens)} tokens</span>
                    <span>{usd(c.total_cost_usd)} LLM cost</span>
                    <span>{c.total_tool_calls} tool calls</span>
                    <span>{c.running_agents} running</span>
                  </div>
                  <div className="mt-2">
                    <Table>
                      <Thead>
                        <Tr>
                          <Th>Agent</Th>
                          <Th>NS</Th>
                          <Th>Phase</Th>
                          <Th>Tokens</Th>
                          <Th>Cost</Th>
                        </Tr>
                      </Thead>
                      <Tbody>
                        {c.agents.map((a) => (
                          <Tr key={a.name}>
                            <Td className="text-bright">{a.name.split("/").pop()}</Td>
                            <Td>{a.namespace}</Td>
                            <Td>
                              <PhaseBadge phase={a.phase} />
                            </Td>
                            <Td>{fmt(a.tokens)}</Td>
                            <Td>{usd(a.dollars)}</Td>
                          </Tr>
                        ))}
                      </Tbody>
                    </Table>
                  </div>
                </Card>
              );
            })
          )}
        </div>
      }
    />
  );
}

function DR({ k, v }: { k: string; v: React.ReactNode }) {
  return (
    <div className="flex justify-between border-b border-border py-1">
      <span className="text-dim">{k}</span>
      <span className="text-bright">{v}</span>
    </div>
  );
}
