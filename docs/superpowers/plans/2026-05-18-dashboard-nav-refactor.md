# Dashboard Navigation Refactor Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the flat two-section sidebar with a three-group navigation (Agents / Operations / Platform) that removes creation flows from the nav, surfaces Intelligence to users, and shows a live pending-count badge on Approvals.

**Architecture:** All changes are confined to the Next.js dashboard (`dashboard/`). The Sidebar becomes data-driven from a typed `NAV_GROUPS` array. No new files are created — existing pages are left untouched except `agents/page.tsx` which gains a secondary AI Wizard CTA. The backend API is unchanged.

**Tech Stack:** Next.js 15, React 19, TypeScript 5.6 (strict), Tailwind CSS 3.4, `lucide-react` (already installed). No test framework — verification is `npx tsc --noEmit` + `npm run build` + browser smoke.

---

## File Map

| File | Change |
|---|---|
| `dashboard/src/components/Sidebar.tsx` | Replace flat arrays with typed `NAV_GROUPS`; grouped render with section labels; live Approvals badge |
| `dashboard/src/app/agents/page.tsx` | Add "AI Wizard" secondary CTA next to "Create Agent" button |

No other files change.

---

### Task 1: Replace flat nav arrays with a typed `NAV_GROUPS` structure in Sidebar

**Files:**
- Modify: `dashboard/src/components/Sidebar.tsx`

This task replaces the two unstructured arrays (`NAV_ITEMS`, `ADMIN_ITEMS`) with a typed, grouped data model and rewrites the render to use it. The badge plumbing is added in Task 2.

- [ ] **Step 1: Verify the current file compiles cleanly before touching anything**

```bash
cd dashboard && npx tsc --noEmit
```

Expected: zero errors. If there are pre-existing errors, note them — they are not your responsibility to fix now, but do not introduce new ones.

- [ ] **Step 2: Replace the entire file content**

Write `dashboard/src/components/Sidebar.tsx` with the following:

```tsx
'use client';

import Link from 'next/link';
import { usePathname } from 'next/navigation';
import { useEffect, useState } from 'react';
import { cn } from '@/lib/utils';

interface NavItem {
  href: string;
  label: string;
  /** When true, this item receives the live pending-count badge (Approvals). */
  badge?: boolean;
}

interface NavGroup {
  /** Section label rendered above the group in uppercase. Omit for the top unlabeled group. */
  label?: string;
  items: NavItem[];
}

const NAV_GROUPS: NavGroup[] = [
  {
    items: [{ href: '/', label: 'Overview' }],
  },
  {
    label: 'Agents',
    items: [
      { href: '/agents', label: 'Agents' },
      { href: '/environments', label: 'Environments' },
      { href: '/workflows', label: 'Workflows' },
    ],
  },
  {
    label: 'Operations',
    items: [
      { href: '/approvals', label: 'Approvals', badge: true },
      { href: '/intelligence', label: 'Intelligence' },
      { href: '/clients', label: 'Clients' },
    ],
  },
  {
    label: 'Platform',
    items: [
      { href: '/admin', label: 'System Health' },
      { href: '/admin/jobs', label: 'Scheduler' },
      { href: '/admin/events', label: 'Events' },
      { href: '/admin/mcps', label: 'MCP Registry' },
      { href: '/admin/skills', label: 'Skills' },
      { href: '/admin/knowledge', label: 'Knowledge Base' },
      { href: '/admin/audit', label: 'Audit Log' },
      { href: '/admin/chat', label: 'Platform Chat' },
    ],
  },
];

const SETTINGS_ITEM: NavItem = { href: '/settings', label: 'Settings' };

function NavLink({ href, label, count }: { href: string; label: string; count?: number }) {
  const pathname = usePathname();
  const active = href === '/' ? pathname === '/' : pathname.startsWith(href);
  return (
    <Link
      href={href}
      className={cn(
        'flex items-center gap-2 px-3 py-2 rounded-lg text-[13px] transition-all duration-150',
        active
          ? 'bg-white/10 text-white font-medium'
          : 'text-[#8e8ea0] hover:bg-white/[0.06] hover:text-white/90'
      )}
    >
      <span className="flex-1">{label}</span>
      {count != null && count > 0 && (
        <span className="min-w-[18px] h-[18px] flex items-center justify-center rounded-full bg-amber-500 text-white text-[10px] font-bold px-1">
          {count > 99 ? '99+' : count}
        </span>
      )}
    </Link>
  );
}

export function Sidebar() {
  const [pendingApprovals, setPendingApprovals] = useState(0);

  useEffect(() => {
    function fetchCount() {
      fetch('/api/approvals')
        .then((r) => (r.ok ? r.json() : []))
        .then((items: unknown) => {
          setPendingApprovals(Array.isArray(items) ? items.length : 0);
        })
        .catch(() => {});
    }
    fetchCount();
    const iv = setInterval(fetchCount, 30_000);
    return () => clearInterval(iv);
  }, []);

  return (
    <aside className="w-[240px] bg-[#0d0d0d] text-white flex flex-col shrink-0 select-none">
      {/* Logo */}
      <div className="px-4 py-5 border-b border-white/[0.08]">
        <div className="flex items-center gap-2">
          <div className="w-7 h-7 rounded-lg bg-[#10A37F] flex items-center justify-center text-white text-xs font-bold">
            F
          </div>
          <div>
            <h1 className="text-sm font-semibold tracking-tight leading-none">ForgeOS</h1>
            <p className="text-[10px] text-[#6e6e80] mt-0.5">Agent Platform</p>
          </div>
        </div>
      </div>

      {/* Navigation */}
      <nav className="flex-1 py-3 px-2 overflow-y-auto scrollbar-hide">
        {NAV_GROUPS.map((group, gi) => (
          <div key={gi} className={gi > 0 ? 'mt-4' : undefined}>
            {group.label && (
              <p className="px-3 text-[10px] uppercase tracking-widest text-[#6e6e80] mb-1.5">
                {group.label}
              </p>
            )}
            <div className="space-y-0.5">
              {group.items.map((item) => (
                <NavLink
                  key={item.href}
                  href={item.href}
                  label={item.label}
                  count={item.badge ? pendingApprovals : undefined}
                />
              ))}
            </div>
          </div>
        ))}

        <div className="mt-4 pt-3 border-t border-white/[0.08]">
          <NavLink href={SETTINGS_ITEM.href} label={SETTINGS_ITEM.label} />
        </div>
      </nav>

      {/* Footer */}
      <div className="px-4 py-3 border-t border-white/[0.08] text-[11px] text-[#6e6e80]">
        v3.0
      </div>
    </aside>
  );
}
```

- [ ] **Step 3: Run type check**

```bash
cd dashboard && npx tsc --noEmit
```

Expected: zero errors. If TypeScript reports an error, fix it before proceeding. Common issues:
- `items: unknown` in the `.then()` — the cast to `Array.isArray(items)` is the guard, no change needed.
- If Next.js plugin complains, run `npm run build` instead.

- [ ] **Step 4: Start the dev server and smoke-test the sidebar**

```bash
make dashboard
```

Open http://localhost:3000. Check:
- Three section labels visible: **Agents**, **Operations**, **Platform**
- "AI Wizard" and "Create Agent" are gone from the sidebar
- "Intelligence" is in Operations (not Platform)
- "Scheduler" appears where "Jobs" was
- "Platform Chat" appears where "Admin Chat" was
- Settings appears at the bottom separated by a divider
- Active page link highlights correctly when navigating

- [ ] **Step 5: Commit**

```bash
git add dashboard/src/components/Sidebar.tsx
git commit -m "feat(dashboard): group sidebar into Agents/Operations/Platform sections

- Replace flat NAV_ITEMS + ADMIN_ITEMS with typed NAV_GROUPS
- Remove AI Wizard and Create Agent as nav destinations (they are actions, not sections)
- Move Intelligence from Admin to Operations group
- Rename Admin Chat to Platform Chat, Jobs to Scheduler
- Add Approvals live pending-count badge (polls /api/approvals every 30s)
- Settings pinned at bottom with divider separator"
```

---

### Task 2: Add AI Wizard secondary CTA on the Agents list page

**Files:**
- Modify: `dashboard/src/app/agents/page.tsx`

"AI Wizard" and "Create Agent" were removed from the sidebar in Task 1. Users who want the wizard now need a way to reach it. The `/agents/create/page.tsx` already links to the wizard inline, but that's one click deep. This task adds a visible "AI Wizard" button in the Agents page header so the path is direct.

- [ ] **Step 1: Locate the header row in `dashboard/src/app/agents/page.tsx`**

The current header at the top of the `return (...)` block looks like this (lines ~43–61):

```tsx
<div className="flex items-center justify-between mb-6">
  <div>
    <h1 className="text-2xl font-bold">Agents</h1>
    <p className="text-xs text-gray-500 mt-1 flex items-center gap-2">
      ...live indicator...
    </p>
  </div>
  <Link
    href="/agents/create"
    className="px-4 py-2 bg-[#10A37F] text-white rounded-lg text-sm font-medium hover:bg-[#0d8c6d] transition-colors"
  >
    + Create Agent
  </Link>
</div>
```

- [ ] **Step 2: Replace the single Link with a button group**

Replace only the `<Link href="/agents/create" ...>` element (keep everything else intact) with:

```tsx
<div className="flex items-center gap-2">
  <Link
    href="/agents/create/ai"
    className="px-4 py-2 border border-[#10A37F] text-[#10A37F] rounded-lg text-sm font-medium hover:bg-[#10A37F]/10 transition-colors"
  >
    AI Wizard
  </Link>
  <Link
    href="/agents/create"
    className="px-4 py-2 bg-[#10A37F] text-white rounded-lg text-sm font-medium hover:bg-[#0d8c6d] transition-colors"
  >
    + Create Agent
  </Link>
</div>
```

- [ ] **Step 3: Run type check**

```bash
cd dashboard && npx tsc --noEmit
```

Expected: zero errors.

- [ ] **Step 4: Smoke-test in browser**

With `make dashboard` running, navigate to http://localhost:3000/agents.

Check:
- Two buttons appear in the top-right of the header: "AI Wizard" (outlined) and "+ Create Agent" (filled green)
- Clicking "AI Wizard" navigates to `/agents/create/ai`
- Clicking "+ Create Agent" navigates to `/agents/create`
- Both pages still work normally

- [ ] **Step 5: Commit**

```bash
git add dashboard/src/app/agents/page.tsx
git commit -m "feat(dashboard): add AI Wizard CTA to Agents page header

AI Wizard and Create Agent were removed from the sidebar as standalone
nav items. Users now reach the wizard from the Agents list page directly
via the outlined secondary button alongside the primary Create CTA."
```

---

### Task 3: Full build verification

**Files:** None modified — verification only.

This task confirms there are no type errors or build-time issues introduced by the changes.

- [ ] **Step 1: Stop the dev server if running**

Press `Ctrl+C` in the terminal running `make dashboard`.

- [ ] **Step 2: Run the production build**

```bash
cd dashboard && npm run build
```

Expected output ends with something like:

```
Route (app)                              Size     First Load JS
┌ ○ /                                    ...
├ ○ /agents                              ...
...
✓ Compiled successfully
```

If the build fails with TypeScript errors, fix them. Common issues and fixes:
- "Property 'X' does not exist on type 'Y'" → check the interfaces in `Sidebar.tsx` match usage.
- "Type 'unknown' is not assignable..." → ensure the `Array.isArray(items)` guard is present before calling `.length`.

- [ ] **Step 3: Restart dev server and run final browser smoke test**

```bash
make dashboard
```

Walk through every page reachable from the new sidebar:
- `/` — Overview loads, live indicator shows
- `/agents` — Agents list loads, two CTAs visible
- `/environments` — Environments list loads
- `/workflows` — Workflows list loads
- `/approvals` — Approvals loads; if backend is running and returns data, badge shows on sidebar link
- `/intelligence` — Intelligence chat loads (was previously only reachable via Admin)
- `/clients` — Clients loads
- `/admin` — System Health loads
- `/admin/jobs` — Scheduler loads
- `/admin/events` — Events loads
- `/admin/mcps` — MCP Registry loads
- `/admin/skills` — Skills loads
- `/admin/knowledge` — Knowledge Base loads
- `/admin/audit` — Audit Log loads
- `/admin/chat` — Platform Chat loads
- `/settings` — Settings loads

- [ ] **Step 4: Commit (if any fixes were made during build)**

Only needed if you had to fix build errors in Step 2.

```bash
git add -p   # stage only the fix hunks
git commit -m "fix(dashboard): resolve build-time type errors from nav refactor"
```

---

## Self-Review

### Spec Coverage

| Requirement from proposal | Task |
|---|---|
| Remove AI Wizard + Create Agent from nav | Task 1 (NAV_GROUPS excludes them) |
| Move Intelligence from Admin to Operations | Task 1 (placed in Operations group) |
| Three section labels: Agents / Operations / Platform | Task 1 (render loop with `group.label`) |
| Rename Jobs → Scheduler, Admin Chat → Platform Chat | Task 1 (NAV_GROUPS labels) |
| Settings pinned at bottom with separator | Task 1 (SETTINGS_ITEM + divider) |
| Live Approvals pending-count badge | Task 1 (useEffect polling /api/approvals) |
| AI Wizard reachable without nav | Task 2 (button on /agents page) |
| Full build passes | Task 3 |

All requirements covered.

### Placeholder Scan

No TBD, TODO, or "similar to above" phrases present. All code blocks are complete and self-contained.

### Type Consistency

- `NavItem` defined once, used in `NavGroup.items[]`, `SETTINGS_ITEM`, and `NavLink` props — consistent throughout.
- `count?: number` on `NavLink` matches the conditional render `count != null && count > 0`.
- `pendingApprovals: number` state initialized to `0`, passed as `count` only when `item.badge === true`.
- `items: unknown` in the `.then()` callback is intentional — `Array.isArray` guard before `.length` satisfies TypeScript strict mode.
