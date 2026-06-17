import { ArrowDown, ArrowUp, Plus, Trash } from '@phosphor-icons/react';

import { Button } from '@/components/ui/button';
import { Input, Textarea } from '@/components/ui/input';
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select';
import {
  APPROVAL_MODES, APPROVAL_ON_TIMEOUT, APPROVAL_PRIORITIES, WHEN_OPS,
  emptyApprovalRule, emptyPolicyRef,
  type ApprovalRuleState, type PolicyRefState,
} from '@/lib/wizard/types';
import { LabeledField, ChipsInput } from './fields';

function move<T>(arr: T[], i: number, dir: -1 | 1): T[] {
  const j = i + dir;
  if (j < 0 || j >= arr.length) return arr;
  const next = arr.slice();
  [next[i], next[j]] = [next[j], next[i]];
  return next;
}

/** Repeatable per-tool approval rules. First match wins, so order matters —
 *  rows are reorderable. Supports always / never / conditional (when-clause). */
export function ApprovalRuleEditor({
  rules,
  onChange,
  errors,
}: {
  rules: ApprovalRuleState[];
  onChange: (next: ApprovalRuleState[]) => void;
  errors: Record<string, string>;
}) {
  const patch = (i: number, p: Partial<ApprovalRuleState>) =>
    onChange(rules.map((r, idx) => (idx === i ? { ...r, ...p } : r)));

  return (
    <div className="space-y-3">
      {rules.map((r, i) => (
        <div key={i} className="space-y-3 rounded-lg border border-edge bg-surface p-3">
          <div className="flex items-center gap-1.5">
            <span className="text-[11px] font-medium text-tertiary">Rule {i + 1}</span>
            <span className="flex-1" />
            <Button type="button" size="icon-sm" variant="ghost" aria-label="Move up"
              disabled={i === 0} onClick={() => onChange(move(rules, i, -1))}>
              <ArrowUp className="h-3.5 w-3.5" aria-hidden />
            </Button>
            <Button type="button" size="icon-sm" variant="ghost" aria-label="Move down"
              disabled={i === rules.length - 1} onClick={() => onChange(move(rules, i, 1))}>
              <ArrowDown className="h-3.5 w-3.5" aria-hidden />
            </Button>
            <Button type="button" size="icon-sm" variant="ghost" aria-label="Remove rule"
              onClick={() => onChange(rules.filter((_, idx) => idx !== i))}>
              <Trash className="h-3.5 w-3.5 text-danger" aria-hidden />
            </Button>
          </div>

          <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
            <LabeledField label="Tool (name or wildcard)" error={errors[`approval_${i}_tool`]}>
              <Input value={r.tool} onChange={(e) => patch(i, { tool: e.target.value })}
                placeholder="mcp__atlassian__* or notify__email" className="font-mono text-[12px]" />
            </LabeledField>
            <LabeledField label="Mode">
              <Select value={r.mode} onValueChange={(v) => patch(i, { mode: v as ApprovalRuleState['mode'] })}>
                <SelectTrigger><SelectValue /></SelectTrigger>
                <SelectContent>
                  {APPROVAL_MODES.map((m) => <SelectItem key={m} value={m}>{m}</SelectItem>)}
                </SelectContent>
              </Select>
            </LabeledField>
          </div>

          {r.mode === 'conditional' ? (
            <WhenBuilder rule={r} onChange={(p) => patch(i, p)}
              fieldError={errors[`approval_${i}_when`]} valueError={errors[`approval_${i}_whenValue`]} />
          ) : null}

          {r.mode !== 'never' ? (
            <div className="grid grid-cols-1 gap-3 sm:grid-cols-3">
              <LabeledField label="Priority">
                <Select value={r.priority}
                  onValueChange={(v) => patch(i, { priority: v as ApprovalRuleState['priority'] })}>
                  <SelectTrigger><SelectValue /></SelectTrigger>
                  <SelectContent>
                    {APPROVAL_PRIORITIES.map((p) => <SelectItem key={p} value={p}>{p}</SelectItem>)}
                  </SelectContent>
                </Select>
              </LabeledField>
              <LabeledField label="SLA (hours)">
                <Input type="number" value={r.slaHours} onChange={(e) => patch(i, { slaHours: e.target.value })} />
              </LabeledField>
              <LabeledField label="On timeout">
                <Select value={r.onTimeout}
                  onValueChange={(v) => patch(i, { onTimeout: v as ApprovalRuleState['onTimeout'] })}>
                  <SelectTrigger><SelectValue /></SelectTrigger>
                  <SelectContent>
                    {APPROVAL_ON_TIMEOUT.map((t) => <SelectItem key={t} value={t}>{t}</SelectItem>)}
                  </SelectContent>
                </Select>
              </LabeledField>
            </div>
          ) : null}

          {r.mode !== 'never' ? (
            <LabeledField label="Approvers" description="Emails or roles (Enter to add)">
              <ChipsInput values={r.approvers} onChange={(v) => patch(i, { approvers: v })}
                placeholder="alice@org.com or finance-lead" ariaLabel="approver" />
            </LabeledField>
          ) : null}

          <LabeledField label="Reason (optional)">
            <Input value={r.reason} onChange={(e) => patch(i, { reason: e.target.value })}
              placeholder="Shown to the approver" />
          </LabeledField>
        </div>
      ))}
      <Button type="button" variant="secondary" size="sm" onClick={() => onChange([...rules, emptyApprovalRule()])}>
        <Plus className="h-3.5 w-3.5" aria-hidden /> Add approval rule
      </Button>
    </div>
  );
}

function WhenBuilder({
  rule,
  onChange,
  fieldError,
  valueError,
}: {
  rule: ApprovalRuleState;
  onChange: (p: Partial<ApprovalRuleState>) => void;
  fieldError?: string;
  valueError?: string;
}) {
  return (
    <div className="rounded-md border border-edge-subtle bg-inset p-2.5">
      <p className="mb-2 text-[11px] text-tertiary">
        Ask a human when <span className="font-mono">field op value</span> holds:
      </p>
      <div className="grid grid-cols-1 gap-2 sm:grid-cols-[2fr_1fr_1fr]">
        <div>
          <Input value={rule.whenField} onChange={(e) => onChange({ whenField: e.target.value })}
            placeholder="tool_input.amount_usd" className="font-mono text-[12px]" />
          {fieldError ? <p className="mt-1 text-xs text-danger">{fieldError}</p> : null}
        </div>
        <Select value={rule.whenOp} onValueChange={(v) => onChange({ whenOp: v as ApprovalRuleState['whenOp'] })}>
          <SelectTrigger><SelectValue /></SelectTrigger>
          <SelectContent>
            {WHEN_OPS.map((o) => <SelectItem key={o} value={o}>{o}</SelectItem>)}
          </SelectContent>
        </Select>
        <div>
          <Input value={rule.whenValue} onChange={(e) => onChange({ whenValue: e.target.value })} placeholder="500" />
          {valueError ? <p className="mt-1 text-xs text-danger">{valueError}</p> : null}
        </div>
      </div>
    </div>
  );
}

/** Repeatable policy refs — a file path (.rego/.json) OR inline JSON-logic. */
export function PolicyRefEditor({
  policies,
  onChange,
  errors,
}: {
  policies: PolicyRefState[];
  onChange: (next: PolicyRefState[]) => void;
  errors: Record<string, string>;
}) {
  const patch = (i: number, p: Partial<PolicyRefState>) =>
    onChange(policies.map((x, idx) => (idx === i ? { ...x, ...p } : x)));

  return (
    <div className="space-y-3">
      {policies.map((p, i) => (
        <div key={i} className="space-y-3 rounded-lg border border-edge bg-surface p-3">
          <div className="flex items-center gap-2">
            <span className="text-[11px] font-medium text-tertiary">Policy {i + 1}</span>
            <span className="flex-1" />
            <Button type="button" size="icon-sm" variant="ghost" aria-label="Remove policy"
              onClick={() => onChange(policies.filter((_, idx) => idx !== i))}>
              <Trash className="h-3.5 w-3.5 text-danger" aria-hidden />
            </Button>
          </div>
          <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
            <LabeledField label="Name" error={errors[`policy_${i}_name`]}>
              <Input value={p.name} onChange={(e) => patch(i, { name: e.target.value })} placeholder="spend-guard" />
            </LabeledField>
            <LabeledField label="Source">
              <Select value={p.kind} onValueChange={(v) => patch(i, { kind: v as PolicyRefState['kind'] })}>
                <SelectTrigger><SelectValue /></SelectTrigger>
                <SelectContent>
                  <SelectItem value="ref">File ref (.rego / .json)</SelectItem>
                  <SelectItem value="inline">Inline JSON-logic</SelectItem>
                </SelectContent>
              </Select>
            </LabeledField>
          </div>
          {p.kind === 'ref' ? (
            <LabeledField label="File path" error={errors[`policy_${i}_ref`]}>
              <Input value={p.ref} onChange={(e) => patch(i, { ref: e.target.value })}
                placeholder="policies/spend.rego" className="font-mono text-[12px]" />
            </LabeledField>
          ) : (
            <LabeledField label="Inline JSON" error={errors[`policy_${i}_inline`]}>
              <Textarea value={p.inline} onChange={(e) => patch(i, { inline: e.target.value })}
                placeholder='{ "deny_if": { ... } }' className="font-mono text-[12px]" />
            </LabeledField>
          )}
        </div>
      ))}
      <Button type="button" variant="secondary" size="sm" onClick={() => onChange([...policies, emptyPolicyRef()])}>
        <Plus className="h-3.5 w-3.5" aria-hidden /> Add policy
      </Button>
    </div>
  );
}
