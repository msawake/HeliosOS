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
  const active =
    href === '/'
      ? pathname === '/'
      : pathname === href || pathname.startsWith(href + '/');
  return (
    <Link
      href={href}
      aria-current={active ? 'page' : undefined}
      data-testid={`nav-${label.toLowerCase().replace(/\s+/g, '-')}`}
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
    <aside aria-label="Main navigation" className="w-[240px] bg-[#0d0d0d] text-white flex flex-col shrink-0 select-none">
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
      <nav aria-label="Site" className="flex-1 py-3 px-2 overflow-y-auto scrollbar-hide">
        {NAV_GROUPS.map((group, gi) => (
          <div key={group.label ?? 'top'} className={gi > 0 ? 'mt-4' : undefined}>
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
