'use client';

import Link from 'next/link';
import { usePathname } from 'next/navigation';
import { cn } from '@/lib/utils';

const NAV_ITEMS = [
  { href: '/', label: 'Overview', icon: '◉' },
  { href: '/agents', label: 'Agents', icon: '⬡' },
  { href: '/agents/create/ai', label: 'AI Wizard', icon: '✦' },
  { href: '/agents/create', label: 'Create Agent', icon: '+' },
  { href: '/workflows', label: 'Workflows', icon: '⇄' },
  { href: '/approvals', label: 'Approvals', icon: '✓' },
];

const ADMIN_ITEMS = [
  { href: '/admin', label: 'System Health', icon: '♥' },
  { href: '/admin/chat', label: 'Admin Chat', icon: '▶' },
  { href: '/intelligence', label: 'Intelligence', icon: '◈' },
  { href: '/admin/skills', label: 'Skills Library', icon: '★' },
  { href: '/admin/mcps', label: 'MCP Registry', icon: '⊞' },
  { href: '/admin/events', label: 'Events', icon: '⚡' },
  { href: '/admin/knowledge', label: 'Knowledge Base', icon: '◆' },
  { href: '/admin/jobs', label: 'Scheduler', icon: '⏱' },
  { href: '/admin/audit', label: 'Audit Log', icon: '▤' },
  { href: '/settings', label: 'Settings', icon: '⚙' },
];

function NavLink({ href, label, icon }: { href: string; label: string; icon: string }) {
  const pathname = usePathname();
  const active = href === '/' ? pathname === '/' : pathname.startsWith(href);
  return (
    <Link
      href={href}
      className={cn(
        'flex items-center gap-3 px-3 py-2.5 rounded-lg text-sm transition-colors',
        active
          ? 'bg-white/15 text-white font-medium'
          : 'text-white/60 hover:bg-white/10 hover:text-white'
      )}
    >
      <span className="text-base w-5 text-center">{icon}</span>
      {label}
    </Link>
  );
}

export function Sidebar() {
  return (
    <aside className="w-60 bg-brand-900 text-white flex flex-col shrink-0">
      <div className="px-5 py-6 border-b border-white/10">
        <h1 className="text-lg font-bold tracking-tight">ForgeOS</h1>
        <p className="text-xs text-white/50 mt-0.5">Multi-Stack Agent Platform</p>
      </div>
      <nav className="flex-1 py-4 space-y-1 px-3 overflow-y-auto">
        {NAV_ITEMS.map((item) => (
          <NavLink key={item.href} {...item} />
        ))}
        <div className="my-3 border-t border-white/10" />
        <p className="px-3 text-[10px] uppercase tracking-wider text-white/30 mb-1">Admin</p>
        {ADMIN_ITEMS.map((item) => (
          <NavLink key={item.href} {...item} />
        ))}
      </nav>
      <div className="px-5 py-4 border-t border-white/10 text-xs text-white/40">
        v3.0 · 232 skills · 4,548 MCPs
      </div>
    </aside>
  );
}
