'use client';

import Link from 'next/link';
import { usePathname } from 'next/navigation';
import { cn } from '@/lib/utils';

const NAV_ITEMS = [
  { href: '/', label: 'Overview' },
  { href: '/agents', label: 'Agents' },
  { href: '/agents/create/ai', label: 'AI Wizard' },
  { href: '/agents/create', label: 'Create Agent' },
  { href: '/environments', label: 'Environments' },
  { href: '/workflows', label: 'Workflows' },
  { href: '/approvals', label: 'Approvals' },
  { href: '/clients', label: 'Clients' },
];

const ADMIN_ITEMS = [
  { href: '/admin', label: 'System Health' },
  { href: '/admin/chat', label: 'Admin Chat' },
  { href: '/intelligence', label: 'Intelligence' },
  { href: '/admin/skills', label: 'Skills Library' },
  { href: '/admin/mcps', label: 'MCP Registry' },
  { href: '/admin/events', label: 'Events' },
  { href: '/admin/knowledge', label: 'Knowledge Base' },
  { href: '/admin/jobs', label: 'Scheduler' },
  { href: '/admin/audit', label: 'Audit Log' },
  { href: '/settings', label: 'Settings' },
];

function NavLink({ href, label }: { href: string; label: string }) {
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
      {label}
    </Link>
  );
}

export function Sidebar() {
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
      <nav className="flex-1 py-3 px-2 space-y-0.5 overflow-y-auto scrollbar-hide">
        {NAV_ITEMS.map((item) => (
          <NavLink key={item.href} {...item} />
        ))}

        <div className="my-3 border-t border-white/[0.08]" />
        <p className="px-3 text-[10px] uppercase tracking-widest text-[#6e6e80] mb-1.5 mt-1">
          Admin
        </p>

        {ADMIN_ITEMS.map((item) => (
          <NavLink key={item.href} {...item} />
        ))}
      </nav>

      {/* Footer */}
      <div className="px-4 py-3 border-t border-white/[0.08] text-[11px] text-[#6e6e80]">
        v3.0
      </div>
    </aside>
  );
}
