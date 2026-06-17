'use client';

import Link from 'next/link';
import { usePathname } from 'next/navigation';
import {
  Robot,
  RocketLaunch,
  CheckCircle,
  PlugsConnected,
  Key,
  HardDrives,
  ShieldCheck,
  type Icon,
} from '@phosphor-icons/react';
import { cn } from '@/lib/utils';
import { useAuth } from '@/lib/auth';
import { Logo } from '@/components/brand/logo';

interface NavItem {
  href: string;
  label: string;
  icon: Icon;
  /** Exact-match only (don't treat as a prefix). */
  exact?: boolean;
  /** Only shown to admins (server still enforces; nav-hiding is cosmetic). */
  requiresAdmin?: boolean;
}

const NAV_ITEMS: NavItem[] = [
  { href: '/', label: 'Agents', icon: Robot, exact: true },
  { href: '/deploy', label: 'Deploy', icon: RocketLaunch },
  { href: '/approvals', label: 'Approvals', icon: CheckCircle },
  { href: '/mcp', label: 'MCP servers', icon: PlugsConnected },
  { href: '/environments', label: 'Environments', icon: HardDrives },
  { href: '/credentials', label: 'Credentials', icon: Key },
  { href: '/access', label: 'Access', icon: ShieldCheck, requiresAdmin: true },
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
  const { user } = useAuth();
  // When auth is optional/off, user is null → show everything (dev convenience).
  const isAdmin = user === null || user.role === 'admin';
  return (
    <aside
      className="flex shrink-0 flex-col border-r border-edge bg-surface select-none"
      style={{ width: 'var(--sidebar-width)' }}
    >
      <div className="flex h-(--topbar-height) items-center border-b border-edge px-5">
        <Link href="/" aria-label="Helios OS home">
          <Logo />
        </Link>
      </div>

      <nav className="flex-1 space-y-0.5 overflow-y-auto px-3 py-4 scrollbar-hide">
        {NAV_ITEMS.filter((item) => !item.requiresAdmin || isAdmin).map((item) => (
          <NavLink key={item.href} {...item} />
        ))}
      </nav>

      <div className="border-t border-edge px-5 py-3 text-[11px] text-muted">
        Helios OS v3.1
      </div>
    </aside>
  );
}
