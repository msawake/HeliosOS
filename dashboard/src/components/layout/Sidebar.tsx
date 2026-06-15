'use client';

import Link from 'next/link';
import { usePathname } from 'next/navigation';
import {
  Robot,
  RocketLaunch,
  CheckCircle,
  PlugsConnected,
  Key,
  type Icon,
} from '@phosphor-icons/react';
import { cn } from '@/lib/utils';
import { Logo } from '@/components/brand/logo';

interface NavItem {
  href: string;
  label: string;
  icon: Icon;
  /** Exact-match only (don't treat as a prefix). */
  exact?: boolean;
}

const NAV_ITEMS: NavItem[] = [
  { href: '/', label: 'Agents', icon: Robot, exact: true },
  { href: '/deploy', label: 'Deploy', icon: RocketLaunch },
  { href: '/approvals', label: 'Approvals', icon: CheckCircle },
  { href: '/mcp', label: 'MCP servers', icon: PlugsConnected },
  { href: '/credentials', label: 'Credentials', icon: Key },
];

function NavLink({ href, label, icon: IconCmp, exact }: NavItem) {
  const pathname = usePathname();
  // Agent detail/chat live under /agents/* — keep "Agents" active there too.
  const active = exact
    ? pathname === '/' || pathname.startsWith('/agents')
    : pathname === href || pathname.startsWith(`${href}/`);
  return (
    <Link
      href={href}
      className={cn(
        'flex items-center gap-2.5 rounded-md px-3 py-2 text-[13px] transition-colors duration-(--duration-fast)',
        active
          ? 'bg-accent-wash font-medium text-accent'
          : 'text-secondary hover:bg-surface-hover hover:text-primary'
      )}
    >
      <IconCmp className="h-[18px] w-[18px] shrink-0" weight={active ? 'fill' : 'regular'} aria-hidden />
      {label}
    </Link>
  );
}

export function Sidebar() {
  return (
    <aside
      className="flex shrink-0 flex-col border-r border-edge bg-surface select-none"
      style={{ width: 'var(--sidebar-width)' }}
    >
      <div className="flex h-(--topbar-height) items-center border-b border-edge px-5">
        <Link href="/" aria-label="ForgeOS home">
          <Logo />
        </Link>
      </div>

      <nav className="flex-1 space-y-0.5 overflow-y-auto px-3 py-4 scrollbar-hide">
        {NAV_ITEMS.map((item) => (
          <NavLink key={item.href} {...item} />
        ))}
      </nav>

      <div className="border-t border-edge px-5 py-3 text-[11px] text-muted">
        ForgeOS v3.1
      </div>
    </aside>
  );
}
