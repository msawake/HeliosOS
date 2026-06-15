'use client';

import { usePathname } from 'next/navigation';
import { Sidebar } from '@/components/layout/Sidebar';
import { Topbar } from '@/components/layout/Topbar';
import { RequireAuth } from '@/lib/auth';

/**
 * Top-level shell:
 *  - /login → render children full-bleed (no sidebar, no auth gate)
 *  - everything else → RequireAuth + sidebar + topbar + scrolling content
 *
 * The frame owns scrolling: the window never scrolls; only the content pane does.
 */
export function AppShell({ children }: { children: React.ReactNode }) {
  const pathname = usePathname();

  if (pathname === '/login') {
    return <>{children}</>;
  }

  return (
    <RequireAuth>
      <div className="flex h-screen overflow-hidden">
        <Sidebar />
        <div className="flex min-w-0 flex-1 flex-col">
          <Topbar />
          <main className="flex-1 overflow-auto">
            <div className="mx-auto max-w-6xl px-8 py-7">{children}</div>
          </main>
        </div>
      </div>
    </RequireAuth>
  );
}
